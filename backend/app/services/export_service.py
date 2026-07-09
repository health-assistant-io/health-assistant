import hashlib
import json
import logging
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_provider_model import AIProviderModel, AIModel, AITaskAssignment
from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition, Unit
from app.models.clinical_event import (
    ClinicalEvent,
    ClinicalEventType,
)
from app.models.concept_model import Concept, ConceptEdge
from app.models.document_model import DocumentModel
from app.models.enums import (
    EdgeApprovalStatus,
    EdgeEndpointType,
    ExportScope,
    ExportType,
    JobStatus,
)
from app.models.examination_model import ExaminationModel
from app.models.export_import_job import ExportJobModel
from app.models.fhir.allergy import AllergyCatalog, AllergyIntolerance
from app.models.fhir.medication import Medication, MedicationCatalog
from app.models.fhir.organization import OrganizationModel
from app.models.fhir.patient import DiagnosticReport, Observation, Patient
from app.models.notification import NotificationTrigger
from app.models.telemetry_model import TelemetryDataModel
from app.models.user_integration import UserIntegration
from app.schemas.backup import (
    BACKUP_SCHEMA_VERSION,
    FHIR_VERSION,
    BackupManifest,
    ManifestFile,
)
from app.services.fhir_converter import (
    build_bundle,
    scope_to_smart,
    validate_bundle,
)
from app.services.fhir_helpers import FhirSerializationError, build_meta

logger = logging.getLogger(__name__)

EXPORT_DIR_NAME = "exports"


class ExportError(Exception):
    """Raised when an export cannot produce a valid/complete result (e.g. one or
    more resources fail FHIR validation). Carries a human-readable report so the
    job failure surfaces exactly what to fix — backups must never silently drop
    data (fail-loud policy)."""


def _uuid(v: Any) -> Optional[UUID]:
    if v is None:
        return None
    if isinstance(v, UUID):
        return v
    try:
        return UUID(str(v))
    except (ValueError, AttributeError):
        return None


def _patient_filter_conditions(
    model, patient_ids: List[str], ref_field: str = "patient_id"
):
    ids = [p for p in (_uuid(pid) for pid in patient_ids) if p]
    if not ids:
        return None
    return getattr(model, ref_field).in_(ids)


def _subject_filter_conditions(model, patient_ids: List[str]):
    ids = [str(p) for p in patient_ids if p]
    if not ids:
        return None
    refs = [f"Patient/{pid}" for pid in ids]
    return or_(*[model.subject["reference"].astext == ref for ref in refs])


class ExportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------------- job lifecycle ----------------

    async def create_job(
        self,
        user_id: UUID,
        tenant_id: UUID,
        scope: ExportScope,
        export_type: ExportType,
        patient_ids: Optional[List[str]] = None,
        options: Optional[Dict[str, bool]] = None,
    ) -> ExportJobModel:
        job = ExportJobModel(
            tenant_id=tenant_id,
            user_id=user_id,
            scope=scope,
            export_type=export_type,
            status=JobStatus.PENDING,
            progress=0,
            patient_ids=patient_ids,
            smart_scope=scope_to_smart(scope),
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def update_job_progress(
        self, job_id: UUID, progress: int, status: Optional[JobStatus] = None
    ) -> None:
        values: Dict[str, Any] = {"progress": progress}
        if status:
            values["status"] = status
        await self.db.execute(
            sa_update(ExportJobModel)
            .where(ExportJobModel.id == job_id)
            .values(**values)
        )
        await self.db.commit()

    async def complete_job(
        self,
        job_id: UUID,
        file_path: str,
        file_size: int,
        counts: Dict[str, int],
        manifest: Optional[BackupManifest] = None,
    ) -> None:
        manifest_path = None
        if manifest:
            manifest_path = file_path + ".manifest.json"
        await self.db.execute(
            sa_update(ExportJobModel)
            .where(ExportJobModel.id == job_id)
            .values(
                status=JobStatus.COMPLETED,
                progress=100,
                file_path=file_path,
                manifest_path=manifest_path,
                file_size_bytes=file_size,
                resource_counts=counts,
                completed_at=datetime.now(timezone.utc),
            )
        )
        await self.db.commit()
        if manifest and manifest_path:
            Path(manifest_path).write_text(manifest.model_dump_json(indent=2))

    async def fail_job(self, job_id: UUID, error: str) -> None:
        await self.db.execute(
            sa_update(ExportJobModel)
            .where(ExportJobModel.id == job_id)
            .values(
                status=JobStatus.FAILED,
                error_message=error,
                completed_at=datetime.now(timezone.utc),
            )
        )
        await self.db.commit()

    async def get_job(
        self, job_id: UUID, tenant_id: Optional[UUID] = None
    ) -> Optional[ExportJobModel]:
        q = select(ExportJobModel).where(ExportJobModel.id == job_id)
        if tenant_id:
            q = q.where(ExportJobModel.tenant_id == tenant_id)
        res = await self.db.execute(q)
        return res.scalar_one_or_none()

    async def list_jobs(self, tenant_id: UUID, limit: int = 50) -> List[ExportJobModel]:
        res = await self.db.execute(
            select(ExportJobModel)
            .where(ExportJobModel.tenant_id == tenant_id)
            .order_by(ExportJobModel.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars().all())

    # ---------------- gather (queries) ----------------

    async def gather_patients(
        self, tenant_id: UUID, patient_ids: Optional[List[str]]
    ) -> List[Patient]:
        q = select(Patient).where(Patient.tenant_id == tenant_id)
        cond = _patient_filter_conditions(Patient, patient_ids or [], "id")
        if cond is not None:
            q = q.where(cond)
        res = await self.db.execute(q)
        return list(res.scalars().unique().all())

    async def gather_observations(
        self, tenant_id: UUID, patient_ids: Optional[List[str]]
    ) -> List[Observation]:
        q = select(Observation).where(Observation.tenant_id == tenant_id)
        if patient_ids:
            cond = _subject_filter_conditions(Observation, patient_ids)
            if cond is not None:
                q = q.where(cond)
        res = await self.db.execute(q)
        return list(res.scalars().unique().all())

    async def gather_medications(
        self, tenant_id: UUID, patient_ids: Optional[List[str]]
    ) -> List[Medication]:
        q = select(Medication).where(Medication.tenant_id == tenant_id)
        cond = _patient_filter_conditions(Medication, patient_ids or [])
        if cond is not None:
            q = q.where(cond)
        res = await self.db.execute(q)
        return list(res.scalars().all())

    async def gather_allergies(
        self, tenant_id: UUID, patient_ids: Optional[List[str]]
    ) -> List[AllergyIntolerance]:
        q = select(AllergyIntolerance).where(AllergyIntolerance.tenant_id == tenant_id)
        cond = _patient_filter_conditions(AllergyIntolerance, patient_ids or [])
        if cond is not None:
            q = q.where(cond)
        res = await self.db.execute(q)
        return list(res.scalars().all())

    async def gather_diagnostic_reports(
        self, tenant_id: UUID, patient_ids: Optional[List[str]]
    ) -> List[DiagnosticReport]:
        q = select(DiagnosticReport).where(DiagnosticReport.tenant_id == tenant_id)
        if patient_ids:
            cond = _subject_filter_conditions(DiagnosticReport, patient_ids)
            if cond is not None:
                q = q.where(cond)
        res = await self.db.execute(q)
        return list(res.scalars().all())

    async def gather_organizations(self, tenant_id: UUID) -> List[OrganizationModel]:
        res = await self.db.execute(
            select(OrganizationModel).where(OrganizationModel.tenant_id == tenant_id)
        )
        return list(res.scalars().all())

    async def gather_practitioners(self, tenant_id: UUID) -> List[Any]:
        from app.models.doctor_model import DoctorModel

        res = await self.db.execute(
            select(DoctorModel).where(DoctorModel.tenant_id == tenant_id)
        )
        return list(res.scalars().all())

    async def gather_documents(
        self, tenant_id: UUID, patient_ids: Optional[List[str]]
    ) -> List[DocumentModel]:
        q = select(DocumentModel).where(DocumentModel.tenant_id == tenant_id)
        cond = _patient_filter_conditions(DocumentModel, patient_ids or [])
        if cond is not None:
            q = q.where(cond)
        res = await self.db.execute(q)
        return list(res.scalars().all())

    async def gather_telemetry(self, tenant_id: UUID) -> List[TelemetryDataModel]:
        res = await self.db.execute(
            select(TelemetryDataModel).where(TelemetryDataModel.tenant_id == tenant_id)
        )
        return list(res.scalars().all())

    async def gather_integrations(
        self, tenant_id: UUID, patient_ids: Optional[List[str]]
    ) -> List[UserIntegration]:
        q = select(UserIntegration).where(UserIntegration.tenant_id == tenant_id)
        cond = _patient_filter_conditions(UserIntegration, patient_ids or [])
        if cond is not None:
            q = q.where(cond)
        res = await self.db.execute(q)
        return list(res.scalars().all())

    async def gather_notification_triggers(
        self, tenant_id: UUID, patient_ids: Optional[List[str]]
    ) -> List[NotificationTrigger]:
        q = select(NotificationTrigger).where(
            NotificationTrigger.tenant_id == tenant_id
        )
        cond = _patient_filter_conditions(NotificationTrigger, patient_ids or [])
        if cond is not None:
            q = q.where(cond)
        res = await self.db.execute(q)
        return list(res.scalars().all())

    async def gather_examinations(
        self, tenant_id: UUID, patient_ids: Optional[List[str]]
    ) -> List[ExaminationModel]:
        q = select(ExaminationModel).where(ExaminationModel.tenant_id == tenant_id)
        cond = _patient_filter_conditions(ExaminationModel, patient_ids or [])
        if cond is not None:
            q = q.where(cond)
        res = await self.db.execute(q)
        return list(res.scalars().unique().all())

    async def gather_clinical_events(
        self, tenant_id: UUID, patient_ids: Optional[List[str]]
    ) -> List[ClinicalEvent]:
        q = select(ClinicalEvent).where(ClinicalEvent.tenant_id == tenant_id)
        cond = _patient_filter_conditions(ClinicalEvent, patient_ids or [])
        if cond is not None:
            q = q.where(cond)
        res = await self.db.execute(q)
        return list(res.scalars().unique().all())

    async def gather_clinical_event_types(self, tenant_id: UUID) -> Dict[str, Any]:
        from app.models.concept_model import Concept
        from app.models.enums import ConceptKind
        from app.services.concept_service import concepts_with_kind

        types_res = await self.db.execute(
            select(ClinicalEventType).where(
                or_(
                    ClinicalEventType.tenant_id == tenant_id,
                    ClinicalEventType.tenant_id.is_(None),
                )
            )
        )
        cats_res = await self.db.execute(
            select(Concept).where(
                concepts_with_kind(ConceptKind.EVENT_CATEGORY),
                Concept.deleted_at.is_(None),
                or_(
                    Concept.tenant_id == tenant_id,
                    Concept.tenant_id.is_(None),
                ),
            )
        )
        return {
            "types": [t.to_dict() for t in types_res.scalars().all()],
            "categories": [c.to_dict() for c in cats_res.scalars().all()],
        }

    async def gather_concepts(self, tenant_id: UUID) -> Dict[str, Any]:
        """Tenant-private concepts (the part of the taxonomy not recreated by
        the global seed). Global/seeded concepts (``tenant_id IS NULL``) are
        re-created by ``SeedService`` on the target, so we export only
        tenant-scoped rows to keep backups small and avoid version-skew
        clashes. References from tenant-scoped rows back to global concepts are
        preserved by id-remap + slug fallback on import."""
        res = await self.db.execute(
            select(Concept).where(
                Concept.tenant_id == tenant_id,
                Concept.deleted_at.is_(None),
            )
        )
        return {"concepts": [c.to_dict() for c in res.scalars().unique().all()]}

    async def gather_concept_edges(self, tenant_id: UUID) -> Dict[str, Any]:
        """Tenant-scoped knowledge-graph edges. Global/seeded edges are
        recreated by ``SeedService`` on the target. Endpoints that point at
        global concepts/anatomy/biomarkers are remapped on import via slug /
        existence lookup against the target's re-seeded rows."""
        res = await self.db.execute(
            select(ConceptEdge).where(ConceptEdge.tenant_id == tenant_id)
        )
        return {"edges": [e.to_dict() for e in res.scalars().all()]}

    async def gather_anatomy(self, tenant_id: UUID) -> Dict[str, Any]:
        """Tenant-scoped anatomy structures and the relations between them.

        Global/seeded body parts (``tenant_id IS NULL``) are recreated by
        ``SeedService`` on the target, so we export only rows owned by this
        tenant — that already includes every custom organ an admin here
        created (custom rows are tenant-scoped). We deliberately do NOT use
        ``OR is_custom = True``: that would leak other tenants' private custom
        anatomy and break tenant isolation. Relations are included only when
        both endpoints are in the exported set."""
        struct_res = await self.db.execute(
            select(AnatomyStructure).where(
                AnatomyStructure.tenant_id == tenant_id
            )
        )
        structures = list(struct_res.scalars().unique().all())
        exported_ids = {s.id for s in structures}
        rel_res = await self.db.execute(
            select(ConceptEdge).where(
                ConceptEdge.src_type == EdgeEndpointType.ANATOMY,
                ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
                ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                or_(
                    ConceptEdge.tenant_id == tenant_id,
                    ConceptEdge.tenant_id.is_(None),
                ),
            )
        )
        relations = [
            {
                "id": str(e.id),
                "source_id": str(e.src_id),
                "target_id": str(e.dst_id),
                "relation_type": e.relation.value,
            }
            for e in rel_res.scalars().all()
            if e.src_id in exported_ids and e.dst_id in exported_ids
        ]
        return {
            "structures": [s.to_dict() for s in structures],
            "relations": relations,
        }

    async def gather_biomarker_catalog(self, tenant_id: UUID) -> Dict[str, Any]:
        units_res = await self.db.execute(select(Unit))
        bios_res = await self.db.execute(
            select(BiomarkerDefinition).where(
                or_(
                    BiomarkerDefinition.tenant_id == tenant_id,
                    BiomarkerDefinition.tenant_id.is_(None),
                )
            )
        )
        return {
            "units": [
                {
                    "id": str(u.id),
                    "symbol": u.symbol,
                    "name": u.name,
                    "quantity_type": u.quantity_type.value if u.quantity_type else None,
                    "base_unit_id": str(u.base_unit_id) if u.base_unit_id else None,
                    "conversion_multiplier": u.conversion_multiplier,
                }
                for u in units_res.scalars().all()
            ],
            "biomarkers": [
                {
                    "id": str(b.id),
                    "slug": b.slug,
                    "coding_system": b.coding_system.value if b.coding_system else None,
                    "code": b.code,
                    "name": b.name,
                    "category": b.category,
                    # The class concept *slug* — the canonical, stable key the
                    # import resolves against ``concepts.slug``. The legacy
                    # ``category`` field above is the concept *name* (human-
                    # readable) which does NOT round-trip through
                    # ``biomarker_category_to_concept_slug`` (it only swaps
                    # ``_``→``-``, leaving spaces). Without this slug the
                    # biomarker's class link is silently dropped on restore.
                    "class_concept_slug": b.class_concept.slug
                    if b.class_concept and getattr(b.class_concept, "slug", None)
                    else None,
                    "preferred_unit_id": str(b.preferred_unit_id)
                    if b.preferred_unit_id
                    else None,
                    "aliases": b.aliases or [],
                    "info": b.info,
                    "reference_range_min": b.reference_range_min,
                    "reference_range_max": b.reference_range_max,
                    "is_telemetry": b.is_telemetry,
                    "tenant_id": str(b.tenant_id) if b.tenant_id else None,
                }
                for b in bios_res.scalars().all()
            ],
        }

    async def gather_medication_catalog(self, tenant_id: UUID) -> Dict[str, Any]:
        res = await self.db.execute(
            select(MedicationCatalog).where(
                or_(
                    MedicationCatalog.tenant_id == tenant_id,
                    MedicationCatalog.tenant_id.is_(None),
                )
            )
        )
        return {
            "medications": [
                {
                    "id": str(m.id),
                    "name": m.name,
                    "description": m.description,
                    "indications": m.indications,
                    "side_effects": m.side_effects or [],
                    "contraindications": m.contraindications,
                    "dosage_info": m.dosage_info,
                    "tenant_id": str(m.tenant_id) if m.tenant_id else None,
                }
                for m in res.scalars().all()
            ]
        }

    async def gather_allergy_catalog(self, tenant_id: UUID) -> Dict[str, Any]:
        res = await self.db.execute(
            select(AllergyCatalog).where(
                or_(
                    AllergyCatalog.tenant_id == tenant_id,
                    AllergyCatalog.tenant_id.is_(None),
                )
            )
        )
        return {
            "allergies": [
                {
                    "id": str(a.id),
                    "name": a.name,
                    "category": a.category.value if a.category else None,
                    "description": a.description,
                    "typical_reactions": a.typical_reactions or [],
                    "tenant_id": str(a.tenant_id) if a.tenant_id else None,
                }
                for a in res.scalars().all()
            ]
        }

    async def gather_ai_config(self, tenant_id: UUID) -> Dict[str, Any]:
        providers_res = await self.db.execute(
            select(AIProviderModel).where(AIProviderModel.tenant_id == tenant_id)
        )
        providers = list(providers_res.scalars().all())
        provider_ids = [p.id for p in providers]
        models: List[Any] = []
        assignments: List[Any] = []
        if provider_ids:
            models_res = await self.db.execute(
                select(AIModel).where(AIModel.provider_id.in_(provider_ids))
            )
            models = list(models_res.scalars().all())
            assignments_res = await self.db.execute(
                select(AITaskAssignment).where(AITaskAssignment.tenant_id == tenant_id)
            )
            assignments = list(assignments_res.scalars().all())
        return {
            "providers": [p.to_dict() for p in providers],
            "models": [m.to_dict() for m in models],
            "task_assignments": [a.to_dict() for a in assignments],
        }

    # ---------------- build ----------------

    def build_fhir_bundle(
        self,
        tenant_id: UUID,
        patient_ids: Optional[List[str]],
        patients: List[Patient],
        observations: List[Observation],
        medications: List[Medication],
        allergies: List[AllergyIntolerance],
        diagnostic_reports: List[DiagnosticReport],
        organizations: List[OrganizationModel],
        practitioners: List[Any],
        documents: Optional[List[DocumentModel]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        counts: Dict[str, int] = {}
        entries: List[Tuple[str, Dict[str, Any], str]] = []

        # Each ORM object serializes itself via to_fhir_dict() (which validates
        # through fhir.resources). Fail-loud policy: a resource that fails FHIR
        # validation is NOT silently dropped from the backup — we collect every
        # failure and raise ExportError so the job fails fast with a report of
        # exactly what to fix. (Backups must never quietly omit data.)
        resource_groups = [
            ("Patient", patients),
            ("Observation", observations),
            ("MedicationStatement", medications),
            ("AllergyIntolerance", allergies),
            ("DiagnosticReport", diagnostic_reports),
            ("Organization", organizations),
            ("Practitioner", practitioners),
        ]
        failures: List[str] = []
        for resource_type, items in resource_groups:
            for obj in items:
                try:
                    fhir = obj.to_fhir_dict()
                except FhirSerializationError as e:
                    failures.append(f"{resource_type} {getattr(obj, 'id', '?')}: {e}")
                    continue
                entries.append((f"urn:uuid:{obj.id}", fhir, "POST"))
                counts[resource_type] = counts.get(resource_type, 0) + 1

        if failures:
            preview = "; ".join(failures[:10])
            more = f" (+{len(failures) - 10} more)" if len(failures) > 10 else ""
            raise ExportError(
                f"Export aborted: {len(failures)} resource(s) failed FHIR "
                f"validation — fix the source data/mapping and re-run: {preview}{more}"
            )

        if documents:
            for doc in documents:
                dr = self._document_to_document_reference(doc, tenant_id)
                entries.append((f"urn:uuid:{doc.id}", dr, "POST"))
                counts["DocumentReference"] = counts.get("DocumentReference", 0) + 1

        bundle_meta = build_meta(
            last_updated=datetime.now(timezone.utc).isoformat(),
        )
        bundle = build_bundle(entries, meta=bundle_meta)
        return bundle, counts

    def _document_to_document_reference(
        self, doc: DocumentModel, tenant_id: UUID
    ) -> Dict[str, Any]:
        content: List[Dict[str, Any]] = [
            {
                "attachment": {
                    "url": f"urn:uuid:{doc.id}",
                    "title": doc.filename,
                }
            }
        ]
        return {
            "resourceType": "DocumentReference",
            "id": str(doc.id),
            "status": "current",
            "docStatus": "final"
            if (doc.status or "").lower() == "completed"
            else "preliminary",
            "content": content,
            "meta": build_meta(str(doc.id)),
        }

    def build_nonfhir_sidecars(
        self,
        tenant_id: UUID,
        patient_ids: Optional[List[str]],
        scope: ExportScope,
        options: Dict[str, bool],
        examinations: List[ExaminationModel],
        clinical_events: List[ClinicalEvent],
        clinical_event_types: Dict[str, Any],
        biomarker_catalog: Dict[str, Any],
        medication_catalog: Dict[str, Any],
        allergy_catalog: Dict[str, Any],
        documents: List[DocumentModel],
        telemetry: Optional[List[TelemetryDataModel]] = None,
        integrations: Optional[List[UserIntegration]] = None,
        notification_triggers: Optional[List[NotificationTrigger]] = None,
        ai_config: Optional[Dict[str, Any]] = None,
        concepts: Optional[Dict[str, Any]] = None,
        concept_edges: Optional[Dict[str, Any]] = None,
        anatomy: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, int], List[str]]:
        sidecars: Dict[str, Any] = {}
        counts: Dict[str, int] = {}
        notes: List[str] = []

        # Taxonomy + anatomy first: they hold the FK targets for biomarker
        # classes, examination categories, and edge endpoints. On import they
        # are restored before the sidecars that reference them.
        if concepts is not None:
            sidecars["concepts.json"] = concepts
            counts["concepts"] = len(concepts.get("concepts", []))
        if anatomy is not None:
            sidecars["anatomy.json"] = anatomy
            counts["anatomy_structures"] = len(anatomy.get("structures", []))
            counts["anatomy_relations"] = len(anatomy.get("relations", []))

        sidecars["examinations.json"] = [e.to_dict() for e in examinations]
        counts["examinations"] = len(examinations)

        sidecars["clinical_events.json"] = [e.to_dict() for e in clinical_events]
        counts["clinical_events"] = len(clinical_events)

        sidecars["clinical_event_types.json"] = clinical_event_types
        counts["clinical_event_types"] = len(clinical_event_types.get("types", []))

        sidecars["biomarker_definitions.json"] = biomarker_catalog
        counts["biomarker_definitions"] = len(biomarker_catalog.get("biomarkers", []))

        sidecars["medication_catalog.json"] = medication_catalog
        counts["medication_catalog"] = len(medication_catalog.get("medications", []))

        sidecars["allergy_catalog.json"] = allergy_catalog
        counts["allergy_catalog"] = len(allergy_catalog.get("allergies", []))

        doc_meta = []
        for d in documents:
            meta = d.to_dict()
            ext = os.path.splitext(d.filename or "")[1] or ".bin"
            meta["_archive_path"] = f"documents/{d.id}{ext}"
            doc_meta.append(meta)
        sidecars["documents.json"] = doc_meta
        counts["documents"] = len(doc_meta)

        if telemetry is not None:
            sidecars["telemetry.json"] = [t.to_dict() for t in telemetry]
            counts["telemetry"] = len(telemetry)
        elif scope == ExportScope.PATIENT:
            notes.append(
                "Telemetry excluded for patient scope (no patient_id on telemetry rows)."
            )

        if integrations is not None:
            sidecars["integrations.json"] = [
                self._integration_to_export_dict(i) for i in integrations
            ]
            counts["integrations"] = len(integrations)

        if notification_triggers is not None:
            sidecars["notification_triggers.json"] = [
                t.to_dict() for t in notification_triggers
            ]
            counts["notification_triggers"] = len(notification_triggers)

        if ai_config is not None:
            sidecars["ai_config.json"] = ai_config
            counts["ai_providers"] = len(ai_config.get("providers", []))

        # Edges go LAST in the ZIP — they are polymorphic and reference
        # concepts/anatomy/biomarkers/examinations that the other sidecars (and
        # the FHIR bundle) materialize first.
        if concept_edges is not None:
            sidecars["concept_edges.json"] = concept_edges
            counts["concept_edges"] = len(concept_edges.get("edges", []))

        return sidecars, counts, notes

    def _integration_to_export_dict(self, integ: UserIntegration) -> Dict[str, Any]:
        return {
            "id": str(integ.id),
            "tenant_id": str(integ.tenant_id) if integ.tenant_id else None,
            "user_id": str(integ.user_id) if integ.user_id else None,
            "patient_id": str(integ.patient_id) if integ.patient_id else None,
            "provider": integ.provider,
            "status": integ.status.value if integ.status else None,
            "access_token": integ.access_token,
            "refresh_token": integ.refresh_token,
            "expires_at": integ.expires_at.isoformat() if integ.expires_at else None,
            "scopes": integ.scopes,
            "provider_account_id": integ.provider_account_id,
            "instance_name": integ.instance_name,
            "is_debug_enabled": integ.is_debug_enabled,
            "last_synced_at": integ.last_synced_at.isoformat()
            if integ.last_synced_at
            else None,
            "user_config": integ.user_config,
        }

    # ---------------- write ----------------

    def _exports_dir(self, tenant_id: UUID) -> Path:
        from app.services.document_service_db import UPLOAD_DIR as RESOLVED_UPLOAD_DIR

        base = Path(RESOLVED_UPLOAD_DIR) / EXPORT_DIR_NAME / str(tenant_id)
        base.mkdir(parents=True, exist_ok=True)
        return base

    @staticmethod
    def compute_sha256(path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def write_fhir_only_file(
        self,
        bundle: Dict[str, Any],
        tenant_id: UUID,
        job_id: UUID,
        manifest: BackupManifest,
    ) -> Tuple[str, int, BackupManifest]:
        out_dir = self._exports_dir(tenant_id)
        file_path = out_dir / f"{job_id}.fhir.json"
        bundle_bytes = json.dumps(bundle, indent=2, default=str).encode("utf-8")
        file_path.write_bytes(bundle_bytes)
        sha = self._sha256_bytes(bundle_bytes)
        manifest.files = [
            ManifestFile(path="fhir/bundle.json", sha256=sha, size=len(bundle_bytes))
        ]
        manifest.counts = {"bundle_entries": len(bundle.get("entry", []))}
        size = len(bundle_bytes)
        return str(file_path), size, manifest

    def write_catalog_file(
        self,
        catalog: Dict[str, Any],
        tenant_id: UUID,
        job_id: UUID,
        manifest: BackupManifest,
    ) -> Tuple[str, int, BackupManifest]:
        out_dir = self._exports_dir(tenant_id)
        file_path = out_dir / f"{job_id}.catalog.json"
        payload_bytes = json.dumps(catalog, indent=2, default=str).encode("utf-8")
        file_path.write_bytes(payload_bytes)
        sha = self._sha256_bytes(payload_bytes)
        manifest.files = [
            ManifestFile(path="catalog.json", sha256=sha, size=len(payload_bytes))
        ]
        manifest.counts = {
            "units": len(catalog.get("units", [])),
            "biomarkers": len(catalog.get("biomarkers", [])),
            "clinical_event_types": len(
                catalog.get("clinical_event_types", {}).get("types", [])
            ),
            "medication_catalog": len(
                catalog.get("medication_catalog", {}).get("medications", [])
            ),
            "allergy_catalog": len(
                catalog.get("allergy_catalog", {}).get("allergies", [])
            ),
        }
        return str(file_path), len(payload_bytes), manifest

    def write_full_backup_zip(
        self,
        bundle: Dict[str, Any],
        sidecars: Dict[str, Any],
        documents: List[DocumentModel],
        tenant_id: UUID,
        job_id: UUID,
        manifest: BackupManifest,
    ) -> Tuple[str, int, BackupManifest]:
        out_dir = self._exports_dir(tenant_id)
        zip_path = out_dir / f"{job_id}.zip"
        files: List[ManifestFile] = []

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            bundle_bytes = json.dumps(bundle, indent=2, default=str).encode("utf-8")
            zf.writestr("fhir/bundle.json", bundle_bytes)
            files.append(
                ManifestFile(
                    path="fhir/bundle.json",
                    sha256=self._sha256_bytes(bundle_bytes),
                    size=len(bundle_bytes),
                )
            )

            for name, payload in sidecars.items():
                data = json.dumps(payload, indent=2, default=str).encode("utf-8")
                zf.writestr(f"nonfhir/{name}", data)
                files.append(
                    ManifestFile(
                        path=f"nonfhir/{name}",
                        sha256=self._sha256_bytes(data),
                        size=len(data),
                    )
                )

            for doc in documents:
                ext = os.path.splitext(doc.filename or "")[1] or ".bin"
                archive_name = f"documents/{doc.id}{ext}"
                src = doc.file_path
                if src and os.path.exists(src):
                    with open(src, "rb") as fh:
                        data = fh.read()
                    zf.writestr(archive_name, data)
                    files.append(
                        ManifestFile(
                            path=archive_name,
                            sha256=self._sha256_bytes(data),
                            size=len(data),
                        )
                    )

            manifest.files = files
            manifest.counts = {
                f: sum(1 for x in files if x.path.startswith(f))
                for f in ["fhir", "nonfhir", "documents"]
            }
            manifest_bytes = manifest.model_dump_json(indent=2).encode("utf-8")
            zf.writestr("manifest.json", manifest_bytes)
            sha256_lines = "\n".join(f"{f.sha256}  {f.path}" for f in files) + "\n"
            zf.writestr("manifest-sha256.txt", sha256_lines.encode("utf-8"))
            zf.writestr(
                "bag-info.txt",
                f"Health-Assistant-Export\n"
                f"Tenant-Id: {tenant_id}\n"
                f"Job-Id: {job_id}\n"
                f"Exported-At: {manifest.exported_at.isoformat()}\n"
                f"Schema-Version: {BACKUP_SCHEMA_VERSION}\n"
                f"FHIR-Version: {FHIR_VERSION}\n"
                f"Smart-Scope: {manifest.smart_scope}\n".encode("utf-8"),
            )

        size = zip_path.stat().st_size
        return str(zip_path), size, manifest

    def build_manifest(
        self,
        tenant_id: UUID,
        scope: ExportScope,
        export_type: ExportType,
        options: Dict[str, bool],
        notes: Optional[List[str]] = None,
    ) -> BackupManifest:
        return BackupManifest(
            exported_at=datetime.now(timezone.utc),
            tenant_id=str(tenant_id),
            scope=scope,
            export_type=export_type,
            smart_scope=scope_to_smart(scope),
            options=options,
            notes=notes,
        )

    # ---------------- orchestrator ----------------

    async def run_export(self, job_id: UUID) -> None:
        job = await self.get_job(job_id)
        if not job:
            raise ValueError(f"Export job {job_id} not found")
        tenant_id = job.tenant_id
        if not tenant_id:
            raise ValueError("Export job has no tenant_id")
        patient_ids = job.patient_ids or None
        scope = job.scope
        export_type = job.export_type
        options = {
            "include_documents": True,
            "include_telemetry": True,
            "include_integrations": True,
            "include_ai_config": False,
        }

        try:
            await self.update_job_progress(job_id, 5, JobStatus.PROCESSING)

            if export_type == ExportType.CATALOG_ONLY:
                catalog = await self.gather_biomarker_catalog(tenant_id)
                catalog[
                    "clinical_event_types"
                ] = await self.gather_clinical_event_types(tenant_id)
                catalog["medication_catalog"] = await self.gather_medication_catalog(
                    tenant_id
                )
                catalog["allergy_catalog"] = await self.gather_allergy_catalog(
                    tenant_id
                )
                manifest = self.build_manifest(tenant_id, scope, export_type, options)
                file_path, size, manifest = self.write_catalog_file(
                    catalog, tenant_id, job_id, manifest
                )
                counts = dict(manifest.counts)
                await self.complete_job(job_id, file_path, size, counts, manifest)
                return

            patients = await self.gather_patients(tenant_id, patient_ids)
            observations = await self.gather_observations(tenant_id, patient_ids)
            medications = await self.gather_medications(tenant_id, patient_ids)
            allergies = await self.gather_allergies(tenant_id, patient_ids)
            diag_reports = await self.gather_diagnostic_reports(tenant_id, patient_ids)
            organizations = await self.gather_organizations(tenant_id)
            practitioners = await self.gather_practitioners(tenant_id)
            await self.update_job_progress(job_id, 40)

            documents = []
            if options["include_documents"]:
                documents = await self.gather_documents(tenant_id, patient_ids)

            bundle, fhir_counts = self.build_fhir_bundle(
                tenant_id,
                patient_ids,
                patients,
                observations,
                medications,
                allergies,
                diag_reports,
                organizations,
                practitioners,
                documents=documents,
            )
            ok, errs = validate_bundle(bundle)
            if not ok:
                logger.warning(
                    f"Export bundle validation issues for job {job_id}: {errs}"
                )

            if export_type == ExportType.FHIR_ONLY:
                manifest = self.build_manifest(tenant_id, scope, export_type, options)
                file_path, size, manifest = self.write_fhir_only_file(
                    bundle, tenant_id, job_id, manifest
                )
                manifest.counts = {**fhir_counts, **manifest.counts}
                await self.complete_job(
                    job_id, file_path, size, manifest.counts, manifest
                )
                return

            examinations = await self.gather_examinations(tenant_id, patient_ids)
            clinical_events = await self.gather_clinical_events(tenant_id, patient_ids)
            clinical_event_types = await self.gather_clinical_event_types(tenant_id)
            biomarker_catalog = await self.gather_biomarker_catalog(tenant_id)
            medication_catalog = await self.gather_medication_catalog(tenant_id)
            allergy_catalog = await self.gather_allergy_catalog(tenant_id)
            concepts = await self.gather_concepts(tenant_id)
            concept_edges = await self.gather_concept_edges(tenant_id)
            anatomy = await self.gather_anatomy(tenant_id)
            await self.update_job_progress(job_id, 70)

            telemetry = None
            if options["include_telemetry"] and scope != ExportScope.PATIENT:
                telemetry = await self.gather_telemetry(tenant_id)
            integrations = None
            if options["include_integrations"]:
                integrations = await self.gather_integrations(tenant_id, patient_ids)
            triggers = await self.gather_notification_triggers(tenant_id, patient_ids)
            ai_config = None
            if options.get("include_ai_config") and scope == ExportScope.SYSTEM:
                ai_config = await self.gather_ai_config(tenant_id)

            sidecars, sidecar_counts, notes = self.build_nonfhir_sidecars(
                tenant_id,
                patient_ids,
                scope,
                options,
                examinations,
                clinical_events,
                clinical_event_types,
                biomarker_catalog,
                medication_catalog,
                allergy_catalog,
                documents,
                telemetry=telemetry,
                integrations=integrations,
                notification_triggers=triggers,
                ai_config=ai_config,
                concepts=concepts,
                concept_edges=concept_edges,
                anatomy=anatomy,
            )

            manifest = self.build_manifest(
                tenant_id, scope, export_type, options, notes=notes
            )
            file_path, size, manifest = self.write_full_backup_zip(
                bundle, sidecars, documents, tenant_id, job_id, manifest
            )
            counts = {**fhir_counts, **sidecar_counts}
            await self.complete_job(job_id, file_path, size, counts, manifest)

        except Exception as e:
            logger.exception(f"Export job {job_id} failed")
            await self.fail_job(job_id, str(e))
            raise
