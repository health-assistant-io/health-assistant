import hashlib
import json
import logging
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from sqlalchemy import select, update as sa_update, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.clinical_event import (
    ClinicalEvent,
    ClinicalEventType,
)
from app.models.anatomy_model import AnatomyStructure
from app.models.concept_model import Concept, ConceptEdge, ConceptKindTag
from app.models.document_model import DocumentModel
from app.models.enums import (
    AllergyCategory,
    AllergyClinicalStatus,
    AllergyCriticality,
    ConceptKind,
    ConceptProvenance,
    ConceptRelationType,
    ConceptStatus,
    CodingSystem,
    EdgeApprovalStatus,
    EdgeEndpointType,
    Gender,
    JobStatus,
    MedicationStatus,
)
from app.models.examination_model import ExaminationModel
from app.models.export_import_job import ImportJobModel
from app.models.fhir.allergy import AllergyCatalog, AllergyIntolerance
from app.models.fhir.communication import CommunicationModel
from app.models.fhir.device import DeviceModel
from app.models.fhir.medication import Medication, MedicationCatalog
from app.models.fhir.organization import OrganizationModel
from app.models.fhir.provenance import ProvenanceModel
from app.models.fhir.patient import DiagnosticReport, Observation, Patient
from app.models.notification import NotificationTrigger
from app.models.telemetry_model import TelemetryDataModel
from app.models.user_integration import UserIntegration
from app.schemas.backup import BackupManifest, RestoreResult
from app.services.fhir_converter import (
    fhir_to_orm,
    validate_bundle,
)
from app.services.fhir_helpers import coerce_patient_id
from app.core.converters import parse_dt as _parse_dt, to_uuid as _uuid
from app.schemas.import_data import (
    CSVImportConfig,
    FHIRImportConfig,
    ImportResult,
    ImportStatus,
    OCRImportConfig,
)

logger = logging.getLogger(__name__)


def _parse_date(v: Any) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date):
        return v if not isinstance(v, datetime) else v.date()
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError, AttributeError):
        return None


# ---------------- FHIR resource type -> ORM model (for verb routing) ----------------
#
# Used by DELETE (soft-delete) and POST+ifNoneExist (conditional create) routing
# in ``_restore_one_fhir_resource``. Practitioner is resolved lazily because
# ``DoctorModel`` is imported lazily elsewhere to avoid a circular import.
# MedicationRequest maps to the same ``Medication`` table as MedicationStatement
# (distinguished by the ``intent`` discriminator).
_RESOURCE_TYPE_TO_MODEL: Dict[str, Any] = {
    "Patient": Patient,
    "Observation": Observation,
    "MedicationStatement": Medication,
    "MedicationRequest": Medication,
    "AllergyIntolerance": AllergyIntolerance,
    "DiagnosticReport": DiagnosticReport,
    "Organization": OrganizationModel,
    "Condition": ClinicalEvent,
    "Encounter": ExaminationModel,
    "Device": DeviceModel,
    "Communication": CommunicationModel,
    "Provenance": ProvenanceModel,
}


# ---------------- FHIR reference field → resource type (G11) ----------------
#
# Used by _apply_remap to route bare urn:uuid: references to the correct
# resource type. Entries with None are ambiguous (could be several types) and
# are resolved by a bundle look-ahead (urn_type_index built in restore_fhir_bundle).
FIELD_HINT_TO_TYPE: Dict[str, Optional[str]] = {
    "subject": "Patient",
    "patient": "Patient",
    "performer": "Practitioner",
    "partOf": "Organization",
    "context": "Encounter",
    "encounter": "Encounter",
    "author": "Practitioner",
    "device": "Device",
    "specimen": "Specimen",
    "sender": None,  # Practitioner | Organization | Device | Patient — resolve via look-ahead
    "recipient": None,  # Practitioner | Organization | Patient — resolve via look-ahead
}


def _model_for_type(rt: str) -> Optional[Any]:
    """Return the ORM model class for a FHIR resource type, or None."""
    if rt == "Practitioner":
        from app.models.doctor_model import DoctorModel

        return DoctorModel
    return _RESOURCE_TYPE_TO_MODEL.get(rt)


@dataclass
class BundleRestoreResult:
    """Structured result of restoring a single FHIR Bundle.

    Breaking change (2026-06-30, G7/I4): ``restore_fhir_bundle`` previously
    returned a 5-tuple ``(created, updated, errors, warnings, id_remap)``.
    It now returns a ``BundleRestoreResult`` dataclass with attribute access.
    The new ``deleted`` and ``skipped`` counters track DELETE soft-deletes and
    conditional-create skips (``ifNoneExist`` matched) respectively.
    """

    created: Dict[str, int] = field(default_factory=dict)
    updated: Dict[str, int] = field(default_factory=dict)
    deleted: Dict[str, int] = field(default_factory=dict)
    skipped: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    id_remap: Dict[str, str] = field(default_factory=dict)

    @property
    def total_created(self) -> int:
        return sum(self.created.values())

    @property
    def total_updated(self) -> int:
        return sum(self.updated.values())

    @property
    def total_deleted(self) -> int:
        return sum(self.deleted.values())


class ImportService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._job_id: Optional[UUID] = None

    # ---------------- job lifecycle ----------------

    async def create_import_job(
        self, user_id: UUID, tenant_id: UUID, source_filename: Optional[str] = None
    ) -> ImportJobModel:
        job = ImportJobModel(
            tenant_id=tenant_id,
            user_id=user_id,
            source_filename=source_filename,
            status=JobStatus.PENDING,
            progress=0,
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)
        return job

    async def _update_progress(
        self,
        job_id: UUID,
        progress: int,
        status: Optional[JobStatus] = None,
        message: Optional[str] = None,
    ) -> None:
        values: Dict[str, Any] = {"progress": min(progress, 99)}
        if status:
            values["status"] = status
        await self.db.execute(
            sa_update(ImportJobModel)
            .where(ImportJobModel.id == job_id)
            .values(**values)
        )
        await self.db.commit()

        # Publish to Redis for live WebSocket updates (best-effort).
        try:
            from app.core.redis import publish_message

            job = await self.get_job(job_id)
            tenant_id = job.tenant_id if job else None
            if tenant_id:
                payload = {
                    "type": "import_progress",
                    "job_id": str(job_id),
                    "status": status.value
                    if status and hasattr(status, "value")
                    else None,
                    "progress": min(progress, 99),
                    "message": message,
                }
                await publish_message(f"tenant:{tenant_id}:tasks", json.dumps(payload))
        except Exception:
            pass

    async def _complete_job(self, job_id: UUID, result: RestoreResult) -> None:
        await self.db.execute(
            sa_update(ImportJobModel)
            .where(ImportJobModel.id == job_id)
            .values(
                status=JobStatus.COMPLETED if not result.errors else JobStatus.PARTIAL,
                progress=100,
                total_records=result.total_records,
                processed_records=result.processed_records,
                failed_records=result.failed_records,
                restore_result={
                    "created_resources": result.created_resources,
                    "updated_resources": result.updated_resources,
                    "manifest_verified": result.manifest_verified,
                    "fhir_validated": result.fhir_validated,
                },
                errors=result.errors,
                warnings=result.warnings,
                completed_at=datetime.now(timezone.utc),
            )
        )
        await self.db.commit()

    async def _fail_job(self, job_id: UUID, error: str) -> None:
        await self.db.execute(
            sa_update(ImportJobModel)
            .where(ImportJobModel.id == job_id)
            .values(
                status=JobStatus.FAILED,
                error_message=error,
                completed_at=datetime.now(timezone.utc),
            )
        )
        await self.db.commit()

    async def get_job(
        self, job_id: UUID, tenant_id: Optional[UUID] = None
    ) -> Optional[ImportJobModel]:
        q = select(ImportJobModel).where(ImportJobModel.id == job_id)
        if tenant_id:
            q = q.where(ImportJobModel.tenant_id == tenant_id)
        res = await self.db.execute(q)
        return res.scalar_one_or_none()

    # ---------------- manifest verification ----------------

    @staticmethod
    def verify_manifest_from_zip(
        zf: zipfile.ZipFile,
    ) -> Tuple[bool, Optional[BackupManifest], List[str]]:
        errors: List[str] = []
        try:
            manifest_bytes = zf.read("manifest.json")
        except KeyError:
            return False, None, ["manifest.json not found in archive"]
        try:
            manifest = BackupManifest.model_validate_json(manifest_bytes)
        except Exception as e:
            return False, None, [f"Invalid manifest.json: {e}"]

        all_ok = True
        for f in manifest.files:
            try:
                data = zf.read(f.path)
            except KeyError:
                errors.append(f"File listed in manifest missing from archive: {f.path}")
                all_ok = False
                continue
            actual = hashlib.sha256(data).hexdigest()
            if actual != f.sha256:
                errors.append(f"SHA256 mismatch for {f.path}")
                all_ok = False
        return all_ok, manifest, errors

    # ---------------- FHIR bundle restore ----------------

    async def restore_fhir_bundle(
        self,
        bundle: Dict[str, Any],
        tenant_id: UUID,
        validate: bool = True,
        config: Optional[FHIRImportConfig] = None,
        actor_user_id: Optional[UUID] = None,
        source_job_id: Optional[UUID] = None,
    ) -> BundleRestoreResult:
        created: Dict[str, int] = {}
        updated: Dict[str, int] = {}
        deleted: Dict[str, int] = {}
        skipped: Dict[str, int] = {}
        errors: List[str] = []
        warnings: List[str] = []
        id_remap: Dict[str, str] = {}
        imported_obs_ids: List[UUID] = []

        if validate:
            ok, verrors = validate_bundle(bundle)
            if not ok:
                errors.extend(verrors)
                return BundleRestoreResult(
                    created=created,
                    updated=updated,
                    deleted=deleted,
                    skipped=skipped,
                    errors=errors,
                    warnings=warnings,
                    id_remap=id_remap,
                )

        entries = bundle.get("entry", [])
        if bundle.get("resourceType") == "Bundle" and not entries:
            return BundleRestoreResult(
                created=created,
                updated=updated,
                deleted=deleted,
                skipped=skipped,
                errors=errors,
                warnings=warnings,
                id_remap=id_remap,
            )

        if bundle.get("resourceType") != "Bundle":
            entries = [{"resource": bundle}]

        # G11: build a {id → resourceType} index once for the whole bundle so
        # _apply_remap can resolve ambiguous urn:uuid references (sender/recipient)
        # via bundle look-ahead.
        urn_type_index: Dict[str, str] = {}
        for entry in entries:
            res = entry.get("resource") or {}
            rid = res.get("id")
            if rid:
                urn_type_index[str(rid)] = res.get("resourceType", "")

        # G9: cross-tenant collision warnings accumulated by _resolve_id.
        self._collision_warnings: List[str] = []

        for entry in entries:
            resource = entry.get("resource") or {}
            rt = resource.get("resourceType")
            if not rt:
                errors.append("Entry missing resource.resourceType; skipped")
                continue

            if rt == "DocumentReference":
                # We skip DocumentReference because Health Assistant exports it for FHIR
                # completeness, but actually restores documents via the nonfhir/documents.json sidecar.
                warnings.append(
                    f"Skipped {rt} (handled via documents.json sidecar if present)."
                )
                continue

            # G7/I4: honor entry.request.method + ifNoneExist. A bundle authored
            # as a transaction with {"request": {"method": "PUT", "url": "Type/id"}}
            # is now respected (update-if-exists / create-with-id), POST + ifNoneExist
            # is a conditional create, DELETE soft-deletes. Missing request block
            # defaults to POST (create-new) — the historical behaviour.
            request_block = entry.get("request") or {}
            method = (request_block.get("method") or "POST").upper()
            request_url = request_block.get("url")
            if_none_exist = request_block.get("ifNoneExist")

            # Per-resource validation happens inside fhir_to_orm() (via
            # fhir.resources); an invalid resource raises FhirSerializationError
            # which is caught below → skipped + logged (skip-and-log policy).
            try:
                stats_delta, obs_id = await self._restore_one_fhir_resource(
                    rt,
                    resource,
                    tenant_id,
                    id_remap,
                    method=method,
                    request_url=request_url,
                    if_none_exist=if_none_exist,
                    actor_user_id=actor_user_id,
                    source_job_id=source_job_id,
                    urn_type_index=urn_type_index,
                )
                if stats_delta == "created":
                    created[rt] = created.get(rt, 0) + 1
                elif stats_delta == "updated":
                    updated[rt] = updated.get(rt, 0) + 1
                elif stats_delta == "deleted":
                    deleted[rt] = deleted.get(rt, 0) + 1
                elif stats_delta == "skipped_conditional":
                    skipped[rt] = skipped.get(rt, 0) + 1
                    warnings.append(
                        f"Conditional create skipped for {rt}: ifNoneExist matched an existing resource"
                    )
                elif stats_delta in ("skipped_unsupported", "skipped_idempotent"):
                    if stats_delta == "skipped_unsupported":
                        w = f"Skipped unsupported FHIR resource type: {rt}"
                        if w not in warnings:
                            warnings.append(w)
                    # skipped_idempotent (DELETE on missing id) is silent — FHIR DELETE is idempotent
                if obs_id and rt == "Observation":
                    imported_obs_ids.append(obs_id)
            except Exception as e:
                logger.exception(f"Failed to restore {rt}")
                errors.append(f"{rt}: {e}")

        # Deduplicate DocumentReference warnings
        doc_ref_warning = (
            "Skipped DocumentReference (handled via documents.json sidecar if present)."
        )
        doc_ref_count = warnings.count(doc_ref_warning)
        if doc_ref_count > 1:
            warnings = [w for w in warnings if w != doc_ref_warning]
            warnings.append(doc_ref_warning)

        # Run biomarker mapping for newly imported observations
        if imported_obs_ids:
            if self._job_id:
                await self._update_progress(
                    self._job_id,
                    52,
                    message=f"Mapping {len(imported_obs_ids)} biomarker observation(s)",
                )
            try:
                from sqlalchemy import select
                from app.services.fhir_service import map_observations_to_biomarkers

                # We need to load the observations from DB
                res = await self.db.execute(
                    select(Observation).where(Observation.id.in_(imported_obs_ids))
                )
                obs_to_map = res.scalars().all()

                if obs_to_map:
                    # By default we map to existing definitions.
                    # We can use use_ai_normalization from config to determine if we should send unknowns to LLM
                    auto_map = (
                        getattr(config, "auto_map_biomarkers", True) if config else True
                    )
                    use_ai = (
                        getattr(config, "use_ai_normalization", False)
                        if config
                        else False
                    )

                    if auto_map:
                        # map_observations_to_biomarkers does basic string/code mapping
                        # If use_ai is enabled, DO NOT auto-create missing entries yet so the AI can handle them
                        await map_observations_to_biomarkers(
                            self.db, obs_to_map, auto_create_missing=not use_ai
                        )

                        if use_ai:
                            unmapped = [o for o in obs_to_map if not o.biomarker_id]
                            if unmapped:
                                from app.ai.pipeline.service import (
                                    MedicalProcessingService,
                                )
                                from app.ai.providers.service import AIProviderService
                                from app.ai.schemas.nlp import UnknownBiomarkerExtract

                                logger.info(
                                    "AI normalization: resolving %d unmapped observation(s) via NLP task",
                                    len(unmapped),
                                )
                                if self._job_id:
                                    await self._update_progress(
                                        self._job_id,
                                        55,
                                        message="AI normalization: generating biomarker definitions",
                                    )

                                ai_service = AIProviderService(self.db)
                                nlp_extractor = await ai_service.get_nlp_extractor(
                                    tenant_id
                                )
                                med_service = MedicalProcessingService(self.db)

                                unknown_bios: List[Any] = []
                                seen_names: set = set()
                                for o in unmapped:
                                    text = o.code.get("text") or next(
                                        (
                                            c.get("display") or c.get("code")
                                            for c in o.code.get("coding", [])
                                        ),
                                        "Unknown",
                                    )
                                    name_key = text.lower().strip()
                                    if (
                                        not name_key
                                        or name_key == "unknown"
                                        or name_key in seen_names
                                    ):
                                        continue
                                    seen_names.add(name_key)
                                    try:
                                        value = float(
                                            o.raw_value
                                            or (
                                                o.value_quantity.get("value")
                                                if o.value_quantity
                                                else 0
                                            )
                                        )
                                    except (TypeError, ValueError):
                                        value = 0.0
                                    unit_symbol = (
                                        o.value_quantity.get("unit")
                                        if o.value_quantity
                                        else None
                                    )
                                    unknown_bios.append(
                                        UnknownBiomarkerExtract(
                                            raw_name=text,
                                            value=value,
                                            unit_symbol=unit_symbol or "",
                                        )
                                    )

                                if unknown_bios:
                                    slug_map: Dict[str, str] = {}
                                    await med_service._process_unknown_biomarkers(
                                        unknown_bios, nlp_extractor, tenant_id, slug_map
                                    )
                                    await self.db.flush()
                                    logger.info(
                                        "AI normalization: created %d definition(s) (%d new slugs)",
                                        len(unknown_bios),
                                        len(slug_map),
                                    )

                                    await map_observations_to_biomarkers(
                                        self.db, unmapped
                                    )

                    # Telemetry fan-out for newly mapped observations
                    from app.models.biomarker_model import BiomarkerDefinition
                    from app.models.telemetry_model import TelemetryDataModel

                    # Ensure we have the biomarkers loaded
                    mapped_obs = [o for o in obs_to_map if o.biomarker_id]
                    if mapped_obs:
                        b_ids = {o.biomarker_id for o in mapped_obs}
                        b_res = await self.db.execute(
                            select(BiomarkerDefinition).where(
                                BiomarkerDefinition.id.in_(b_ids)
                            )
                        )
                        b_dict = {b.id: b for b in b_res.scalars().all()}

                        telemetry_records = []
                        for o in mapped_obs:
                            b_def = b_dict.get(o.biomarker_id)
                            if b_def and b_def.is_telemetry:
                                slug = b_def.slug.lower() if b_def.slug else ""
                                val = (
                                    getattr(o, "normalized_value", None)
                                    or getattr(o, "raw_value", None)
                                    or (
                                        o.value_quantity.get("value")
                                        if o.value_quantity
                                        else None
                                    )
                                )
                                if val is not None:
                                    hr = (
                                        val
                                        if slug == "8867-4" or "heart-rate" in slug
                                        else None
                                    )
                                    steps = (
                                        val
                                        if slug == "41950-7" or "steps" in slug
                                        else None
                                    )
                                    cal = val if "calories" in slug else None

                                    data_payload = {}
                                    if not hr and not steps and not cal:
                                        data_payload[slug] = val
                                        if getattr(o, "value_quantity", None):
                                            data_payload[f"{slug}_unit"] = (
                                                o.value_quantity.get("unit", "")
                                            )

                                    telemetry_records.append(
                                        TelemetryDataModel(
                                            tenant_id=o.tenant_id,
                                            device_id="fhir_import",
                                            timestamp=o.effective_datetime,
                                            heart_rate=hr,
                                            steps=steps,
                                            calories=cal,
                                            data=data_payload if data_payload else None,
                                        )
                                    )

                        if telemetry_records:
                            self.db.add_all(telemetry_records)
                            await self.db.flush()

            except Exception as e:
                logger.exception("Failed to map biomarkers for imported observations")
                warnings.append(
                    f"Failed to map biomarkers for imported observations: {e}"
                )

        # G9: surface cross-tenant collision warnings collected by _resolve_id.
        warnings.extend(getattr(self, "_collision_warnings", []))

        return BundleRestoreResult(
            created=created,
            updated=updated,
            deleted=deleted,
            skipped=skipped,
            errors=list(set(errors)),
            warnings=warnings,
            id_remap=id_remap,
        )

    async def _restore_one_fhir_resource(
        self,
        rt: str,
        fhir_dict: Dict[str, Any],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        method: str = "POST",
        request_url: Optional[str] = None,
        if_none_exist: Optional[str] = None,
        actor_user_id: Optional[UUID] = None,
        source_job_id: Optional[UUID] = None,
        urn_type_index: Optional[Dict[str, str]] = None,
    ) -> Tuple[str, Optional[UUID]]:
        """Restore one FHIR entry, honoring the bundle's request verb.

        Verb routing (G7/I4):
        - ``DELETE``  → soft-delete by id (idempotent on missing).
        - ``PUT``     → update-if-exists, else create-with-the-supplied-id.
        - ``POST`` + ``ifNoneExist`` → conditional create (skip if match).
        - ``POST`` (default) → create-new (historical behaviour).

        G6: records a Provenance per successful created/updated/deleted entry
        (best-effort — never aborts the import).
        """
        method = (method or "POST").upper()
        model = _model_for_type(rt)

        # --- DELETE: soft-delete by id (idempotent on missing) ---
        if method == "DELETE":
            target_id_str = self._parse_request_id(request_url) or fhir_dict.get("id")
            if model is None or not hasattr(model, "deleted_at") or not target_id_str:
                return "skipped_unsupported", None
            ok = await self._soft_delete_by_id(model, tenant_id, target_id_str)
            action = "deleted" if ok else "skipped_idempotent"
            if ok:
                await self._record_import_provenance(
                    rt,
                    _uuid(target_id_str),
                    action,
                    tenant_id,
                    actor_user_id,
                    source_job_id,
                )
            return action, None

        # For PUT/POST we need a converter. Practitioner has no module-level
        # model entry but DOES have a converter (fhir_to_practitioner_orm).
        from app.services.fhir_converter import _TO_ORM

        if rt not in _TO_ORM:
            logger.warning(f"Unsupported resource type {rt}")
            return "skipped_unsupported", None

        # --- PUT: id comes from the request URL (FHIR spec); ensure the body carries it ---
        force_id: Optional[UUID] = None
        remapped = fhir_dict
        if method == "PUT":
            url_id = self._parse_request_id(request_url)
            if url_id:
                remapped = dict(fhir_dict)  # don't mutate the caller's resource dict
                remapped["id"] = url_id
                force_id = _uuid(url_id)
            # fall through to the upsert path (update-if-exists, create-with-id-if-not)

        # --- POST + ifNoneExist: conditional create (skip if a match exists) ---
        if method == "POST" and if_none_exist and model is not None:
            existing_id = await self._conditional_find(
                model, rt, tenant_id, if_none_exist
            )
            if existing_id is not None:
                return "skipped_conditional", None
            # no match (or unsupported form) → fall through to create

        old_id = remapped.get("id")
        old_id_str = str(old_id) if old_id else None
        remapped = self._apply_remap(remapped, id_remap, urn_type_index=urn_type_index)
        orm_dict = fhir_to_orm(rt, remapped)
        orm_dict["tenant_id"] = tenant_id

        if rt == "Patient":
            action_str, target_id = (
                await self._upsert_patient(
                    orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Observation":
            action_str, target_id = await self._upsert_observation(
                orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
            )
        elif rt in ("MedicationStatement", "MedicationRequest"):
            # Both persist to the Medication table, distinguished by `intent`.
            action_str, target_id = (
                await self._upsert_medication(
                    orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "AllergyIntolerance":
            action_str, target_id = (
                await self._upsert_allergy(
                    orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "DiagnosticReport":
            action_str, target_id = (
                await self._upsert_diagnostic_report(
                    orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Organization":
            action_str, target_id = (
                await self._upsert_organization(
                    orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Practitioner":
            action_str, target_id = (
                await self._upsert_practitioner(
                    orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Condition":
            action_str, target_id = (
                await self._upsert_condition(
                    orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Encounter":
            action_str, target_id = (
                await self._upsert_encounter(
                    orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Device":
            action_str, target_id = (
                await self._upsert_device(
                    orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Communication":
            action_str, target_id = (
                await self._upsert_communication(
                    orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        elif rt == "Provenance":
            action_str, target_id = (
                await self._upsert_provenance(
                    orm_dict, old_id_str, tenant_id, id_remap, force_id=force_id
                ),
                None,
            )
        else:
            logger.warning(f"Unsupported resource type {rt} (no upsert branch)")
            return "skipped_unsupported", None

        # G6: record a Provenance for the created/updated entry. Derive the
        # target id from the upsert result (Observation returns it directly);
        # for other types, read it from id_remap when the entry carried a
        # bundle id. Best-effort — never aborts the import.
        if action_str in ("created", "updated") and target_id is None and old_id_str:
            target_id = _uuid(id_remap.get(old_id_str))
        if action_str in ("created", "updated"):
            await self._record_import_provenance(
                rt, target_id, action_str, tenant_id, actor_user_id, source_job_id
            )
        return action_str, target_id

    async def _record_import_provenance(
        self,
        rt: str,
        target_id: Optional[UUID],
        action: str,
        tenant_id: UUID,
        actor_user_id: Optional[UUID],
        source_job_id: Optional[UUID],
    ) -> None:
        """G6: record one Provenance per imported/updated entry (best-effort).

        The ``entity_inputs`` carry an ``ImportJob/<id>`` reference so the
        audit trail is self-describing (distinguishes bulk-import Provenance
        from facade-write Provenance). Never raises — ``record_provenance``
        itself is best-effort and this wrapper adds its own guard.
        """
        if target_id is None:
            logger.debug("Skipping import Provenance for %s (no target id)", rt)
            return
        from app.services.provenance_service import (
            record_provenance,
            RECORD_CREATE,
            RECORD_UPDATE,
            RECORD_DELETE,
        )

        activity = {
            "created": RECORD_CREATE,
            "updated": RECORD_UPDATE,
            "deleted": RECORD_DELETE,
        }.get(action)
        if activity is None:
            return
        entity_inputs = None
        if source_job_id is not None:
            entity_inputs = [
                {
                    "role": "source",
                    "what": {"reference": f"ImportJob/{source_job_id}"},
                }
            ]
        try:
            await record_provenance(
                self.db,
                target_resource_type=rt,
                target_id=target_id,
                activity=activity,
                tenant_id=tenant_id,
                user_id=actor_user_id,
                entity_inputs=entity_inputs,
            )
        except Exception:
            logger.debug(
                "Import Provenance recording failed for %s/%s",
                rt,
                target_id,
                exc_info=True,
            )

    # ---------------- verb-routing helpers (G7/I4) ----------------

    @staticmethod
    def _parse_request_id(url: Optional[str]) -> Optional[str]:
        """Extract the id from a FHIR request URL like ``Observation/abc``.

        Returns ``None`` for conditional URLs (``Observation?identifier=...``)
        or malformed input, so callers can fall back gracefully.
        """
        if not url:
            return None
        url = url.lstrip("/")
        if "?" in url or "=" in url:
            return None  # conditional form — no literal id
        parts = url.split("/")
        if len(parts) == 2 and parts[1]:
            return parts[1]
        return None

    async def _soft_delete_by_id(
        self, model: Any, tenant_id: UUID, resource_id_str: str
    ) -> bool:
        """Soft-delete (set ``deleted_at``) a resource by id, tenant-scoped.

        Returns True if a row was updated, False if not found / already deleted.
        Only callable on models mixing in ``SoftDeleteMixin``.
        """
        rid = _uuid(resource_id_str)
        if rid is None or not hasattr(model, "deleted_at"):
            return False
        result = await self.db.execute(
            sa_update(model)
            .where(
                model.id == rid,
                model.tenant_id == tenant_id,
                model.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(timezone.utc))
        )
        return bool(getattr(result, "rowcount", 0) or 0)

    async def _conditional_find(
        self, model: Any, rt: str, tenant_id: UUID, if_none_exist: str
    ) -> Optional[UUID]:
        """Best-effort conditional match for FHIR ``ifNoneExist``.

        Supported forms (commonly used by real clients):
        - ``identifier=<code>`` or ``identifier=<system>|<code>`` on **Patient** →
          matches the ``mrn`` column (tenant-scoped, not-deleted).

        Unsupported forms (any other resource type, or non-identifier params)
        log a warning and return None so the caller falls through to an
        unconditional create. This is honest: we surface the gap rather than
        silently treat "couldn't match" as "no match".
        """
        params = parse_qs(if_none_exist, keep_blank_values=True)
        if "identifier" in params and params["identifier"]:
            ident = params["identifier"][0]
            # FHIR token form: "system|code" → take the code (after the pipe)
            code = ident.split("|", 1)[-1] if "|" in ident else ident
            code = code.strip()
            if not code:
                return None
            if rt == "Patient" and hasattr(model, "mrn"):
                not_deleted = (
                    model.deleted_at.is_(None) if hasattr(model, "deleted_at") else True
                )
                res = await self.db.execute(
                    select(model.id).where(
                        model.tenant_id == tenant_id,
                        model.mrn == code,
                        not_deleted,
                    )
                )
                row = res.first()
                return row[0] if row else None
        logger.warning(
            "ifNoneExist query form not supported for %s: %r; creating unconditionally",
            rt,
            if_none_exist,
        )
        return None

    @staticmethod
    def _apply_remap(
        fhir_dict: Dict[str, Any],
        id_remap: Dict[str, str],
        urn_type_index: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Rewrite inter-resource references using the id_remap table.

        G11: routes bare ``urn:uuid:`` references to the correct resource type
        via :data:`FIELD_HINT_TO_TYPE`. Ambiguous hints (``sender``/``recipient``)
        are resolved by looking up the referenced id in ``urn_type_index`` (a
        bundle-wide ``{id → resourceType}`` map built once by
        ``restore_fhir_bundle``). Previously every bare urn:uuid defaulted to
        ``Patient`` unless the hint was ``performer``/``partOf``, so an
        ``Observation.encounter`` given as ``urn:uuid:abc`` was misrouted to
        ``Patient/<new>``.
        """
        if not id_remap:
            return dict(fhir_dict)
        d = json.loads(json.dumps(fhir_dict, default=str))
        urn_type_index = urn_type_index or {}

        def _resolve_type(field_hint: str, rid: str) -> Optional[str]:
            """Determine the FHIR resource type for a bare urn:uuid reference."""
            mapped = FIELD_HINT_TO_TYPE.get(field_hint)
            if mapped is not None:
                return mapped
            # Ambiguous hint — look up the resource type from the bundle index.
            return urn_type_index.get(rid)

        def _remap_ref(obj: Any, field_hint: str) -> Any:
            if isinstance(obj, dict):
                ref = obj.get("reference")
                if isinstance(ref, str):
                    if "/" in ref:
                        prefix, rid = ref.split("/", 1)
                        if rid in id_remap:
                            return {"reference": f"{prefix}/{id_remap[rid]}"}
                    elif ref.startswith("urn:uuid:"):
                        rid = ref.replace("urn:uuid:", "")
                        if rid in id_remap:
                            prefix = _resolve_type(field_hint, rid) or "Patient"
                            return {"reference": f"{prefix}/{id_remap[rid]}"}
                return {k: _remap_ref(v, field_hint) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_remap_ref(x, field_hint) for x in obj]
            return obj

        for fld in (
            "subject",
            "patient",
            "partOf",
            "performer",
            "context",
            "encounter",
            "author",
            "device",
            "specimen",
            "sender",
            "recipient",
        ):
            if fld in d:
                d[fld] = _remap_ref(d[fld], fld)
        return d

    async def _resolve_id(
        self,
        model,
        old_id_str: Optional[str],
        tenant_id: UUID,
        *,
        force_id: Optional[UUID] = None,
    ) -> Tuple[Optional[UUID], UUID, str]:
        """Resolve whether an upsert is an update or a create.

        When ``force_id`` is supplied (PUT path) and no existing row matches,
        the create uses the supplied id instead of minting a fresh ``uuid4`` —
        this is what makes ``PUT Type/<id>`` create-with-id when the id is absent.

        G9: when the bundle id exists in ANOTHER tenant, the collision is no
        longer silent — a warning is appended to ``self._collision_warnings`` so
        the ImportJob result surfaces it (previously looked like 100% creation).
        """
        if old_id_str:
            old_uuid = _uuid(old_id_str)
            if old_uuid:
                res = await self.db.execute(select(model).where(model.id == old_uuid))
                existing = res.scalar_one_or_none()
                if existing:
                    if existing.tenant_id == tenant_id:
                        return old_uuid, old_uuid, "updated"
                    # G9: cross-tenant collision — mint a new id but surface a warning.
                    new_id = force_id or uuid4()
                    self._collision_warnings.append(
                        f"{model.__name__}/{old_id_str} already exists in tenant "
                        f"{existing.tenant_id}; created new with id {new_id} in tenant {tenant_id}"
                    )
                    return None, new_id, "created"
        return None, force_id or uuid4(), "created"

    async def _upsert_patient(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(
            Patient, old_id_str, tenant_id, force_id=force_id
        )
        mrn = (orm.get("mrn") or "").strip() or None
        if existing_id:
            await self.db.execute(
                sa_update(Patient)
                .where(Patient.id == existing_id)
                .values(
                    name=orm.get("name"),
                    gender=Gender(orm.get("gender", "UNKNOWN").upper())
                    if orm.get("gender")
                    else Gender.UNKNOWN,
                    birth_date=_parse_date(orm.get("birth_date")),
                    address=orm.get("address"),
                    telecom=orm.get("telecom"),
                    mrn=mrn,
                )
            )
            id_remap[old_id_str] = str(existing_id)
            return "updated"
        patient = Patient(
            id=new_id,
            tenant_id=tenant_id,
            name=orm.get("name") or [{"text": "Imported"}],
            gender=Gender(orm.get("gender", "UNKNOWN").upper())
            if orm.get("gender")
            else Gender.UNKNOWN,
            birth_date=_parse_date(orm.get("birth_date")),
            address=orm.get("address"),
            telecom=orm.get("telecom"),
            mrn=mrn,
        )
        self.db.add(patient)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    async def _upsert_observation(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> Tuple[str, Optional[UUID]]:
        existing_id, new_id, action = await self._resolve_id(
            Observation, old_id_str, tenant_id, force_id=force_id
        )

        # Semantic Deduplication
        if not existing_id:
            from sqlalchemy import select, and_

            # Check for identical observation
            code_text = orm.get("code", {}).get("text")
            subject_ref = orm.get("subject", {}).get("reference")
            effective_dt = _parse_dt(orm.get("effective_datetime"))

            if code_text and subject_ref and effective_dt:
                stmt = select(Observation).where(
                    and_(
                        Observation.tenant_id == tenant_id,
                        Observation.subject["reference"].astext == subject_ref,
                        Observation.effective_datetime == effective_dt,
                    )
                )
                res = await self.db.execute(stmt)
                for existing_obs in res.scalars().all():
                    existing_code = existing_obs.code.get("text")
                    if existing_code == code_text:
                        # Found a match, merge instead of create
                        existing_id = existing_obs.id
                        break

        if existing_id:
            await self.db.execute(
                sa_update(Observation)
                .where(Observation.id == existing_id)
                .values(
                    status=orm.get("status") or "final",
                    code=orm.get("code"),
                    subject=orm.get("subject"),
                    patient_id=coerce_patient_id(None, orm.get("subject")),
                    effective_datetime=_parse_dt(orm.get("effective_datetime")),
                    value_quantity=orm.get("value_quantity"),
                    value_string=orm.get("value_string"),
                    reference_range=orm.get("reference_range"),
                    performer=orm.get("performer"),
                    interpretation=orm.get("interpretation"),
                    component=orm.get("component"),
                )
            )
            if old_id_str:
                id_remap[old_id_str] = str(existing_id)
            return "updated", existing_id
        obs = Observation(
            id=new_id,
            tenant_id=tenant_id,
            status=orm.get("status") or "final",
            code=orm.get("code") or {"text": "unknown"},
            subject=orm.get("subject") or {"reference": "Patient/unknown"},
            patient_id=coerce_patient_id(None, orm.get("subject")),
            effective_datetime=_parse_dt(orm.get("effective_datetime")),
            value_quantity=orm.get("value_quantity"),
            value_string=orm.get("value_string"),
            reference_range=orm.get("reference_range"),
            performer=orm.get("performer"),
            interpretation=orm.get("interpretation"),
            component=orm.get("component"),
        )
        self.db.add(obs)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created", new_id

    async def _upsert_medication(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(
            Medication, old_id_str, tenant_id, force_id=force_id
        )
        patient_id = _uuid(orm.get("patient_id"))
        try:
            status = MedicationStatus(orm.get("status", "ACTIVE").upper())
        except ValueError:
            status = MedicationStatus.ACTIVE
        if existing_id:
            await self.db.execute(
                sa_update(Medication)
                .where(Medication.id == existing_id)
                .values(
                    status=status,
                    code=orm.get("code"),
                    patient_id=patient_id,
                    start_date=_parse_date(orm.get("start_date")),
                    end_date=_parse_date(orm.get("end_date")),
                    dosage=orm.get("dosage"),
                    frequency=orm.get("frequency"),
                    reason=orm.get("reason"),
                    note=orm.get("note"),
                )
            )
            if old_id_str:
                id_remap[old_id_str] = str(existing_id)
            return "updated"
        med = Medication(
            id=new_id,
            tenant_id=tenant_id,
            patient_id=patient_id or uuid4(),
            status=status,
            code=orm.get("code") or {"text": "unknown"},
            start_date=_parse_date(orm.get("start_date")),
            end_date=_parse_date(orm.get("end_date")),
            dosage=orm.get("dosage"),
            frequency=orm.get("frequency"),
            reason=orm.get("reason"),
            note=orm.get("note"),
        )
        self.db.add(med)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    async def _upsert_allergy(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(
            AllergyIntolerance, old_id_str, tenant_id, force_id=force_id
        )
        patient_id = _uuid(orm.get("patient_id"))
        try:
            clinical = AllergyClinicalStatus(
                orm.get("clinical_status", "ACTIVE").upper()
            )
        except ValueError:
            clinical = AllergyClinicalStatus.ACTIVE
        category = None
        if orm.get("category"):
            try:
                category = AllergyCategory(orm.get("category").upper())
            except ValueError:
                category = None
        criticality = None
        if orm.get("criticality"):
            try:
                criticality = AllergyCriticality(orm.get("criticality").upper())
            except ValueError:
                criticality = None
        if existing_id:
            await self.db.execute(
                sa_update(AllergyIntolerance)
                .where(AllergyIntolerance.id == existing_id)
                .values(
                    clinical_status=clinical,
                    verification_status=orm.get("verification_status") or "confirmed",
                    category=category,
                    criticality=criticality,
                    code=orm.get("code"),
                    patient_id=patient_id,
                    onset_date=_parse_dt(orm.get("onset_date")),
                    note=orm.get("note"),
                    reactions=orm.get("reactions"),
                )
            )
            if old_id_str:
                id_remap[old_id_str] = str(existing_id)
            return "updated"
        allergy = AllergyIntolerance(
            id=new_id,
            tenant_id=tenant_id,
            patient_id=patient_id or uuid4(),
            clinical_status=clinical,
            verification_status=orm.get("verification_status") or "confirmed",
            category=category,
            criticality=criticality,
            code=orm.get("code") or {"text": "unknown"},
            onset_date=_parse_dt(orm.get("onset_date")),
            note=orm.get("note"),
            reactions=orm.get("reactions"),
        )
        self.db.add(allergy)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    async def _upsert_diagnostic_report(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(
            DiagnosticReport, old_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(DiagnosticReport)
                .where(DiagnosticReport.id == existing_id)
                .values(
                    status=orm.get("status") or "final",
                    code=orm.get("code"),
                    subject=orm.get("subject"),
                    patient_id=coerce_patient_id(None, orm.get("subject")),
                    effective_datetime=_parse_dt(orm.get("effective_datetime")),
                    issued=_parse_dt(orm.get("issued")),
                    performer=orm.get("performer"),
                    conclusion=orm.get("conclusion"),
                )
            )
            if old_id_str:
                id_remap[old_id_str] = str(existing_id)
            return "updated"
        dr = DiagnosticReport(
            id=new_id,
            tenant_id=tenant_id,
            status=orm.get("status") or "final",
            code=orm.get("code") or {"text": "unknown"},
            subject=orm.get("subject") or {"reference": "Patient/unknown"},
            patient_id=coerce_patient_id(None, orm.get("subject")),
            effective_datetime=_parse_dt(orm.get("effective_datetime")),
            issued=_parse_dt(orm.get("issued")),
            performer=orm.get("performer"),
            conclusion=orm.get("conclusion"),
        )
        self.db.add(dr)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    async def _upsert_organization(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(
            OrganizationModel, old_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(OrganizationModel)
                .where(OrganizationModel.id == existing_id)
                .values(
                    name=orm.get("name") or "Imported",
                    type=orm.get("type"),
                    telecom=orm.get("telecom"),
                    address=orm.get("address"),
                )
            )
            if old_id_str:
                id_remap[old_id_str] = str(existing_id)
            return "updated"
        org = OrganizationModel(
            id=new_id,
            tenant_id=tenant_id,
            name=orm.get("name") or "Imported Organization",
            type=orm.get("type"),
            telecom=orm.get("telecom"),
            address=orm.get("address"),
        )
        self.db.add(org)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    async def _upsert_practitioner(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> str:
        from app.models.doctor_model import DoctorModel
        from app.services.doctor_service import _resolve_specialty_concept

        existing_id, new_id, action = await self._resolve_id(
            DoctorModel, old_id_str, tenant_id, force_id=force_id
        )
        specialty_concept_id = await _resolve_specialty_concept(
            self.db, orm.get("specialty"), tenant_id=tenant_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(DoctorModel)
                .where(DoctorModel.id == existing_id)
                .values(
                    name=orm.get("name") or "Imported",
                    specialty_concept_id=specialty_concept_id,
                    license_number=orm.get("license_number"),
                    email=orm.get("email"),
                    phone=orm.get("phone"),
                    telecom=orm.get("telecom"),
                    address=orm.get("address"),
                )
            )
            if old_id_str:
                id_remap[old_id_str] = str(existing_id)
            return "updated"
        doc = DoctorModel(
            id=new_id,
            tenant_id=tenant_id,
            name=orm.get("name") or "Imported Doctor",
            specialty_concept_id=specialty_concept_id,
            license_number=orm.get("license_number"),
            email=orm.get("email"),
            phone=orm.get("phone"),
            telecom=orm.get("telecom"),
            address=orm.get("address"),
        )
        self.db.add(doc)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    # ---------------- G8: the 5 resource types added in Phase 6.2 ----------------

    async def _upsert_condition(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(
            ClinicalEvent, old_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(ClinicalEvent)
                .where(ClinicalEvent.id == existing_id)
                .values(
                    status=orm.get("status"),
                    title=orm.get("title") or "Untitled Condition",
                    description=orm.get("description"),
                    onset_date=_parse_dt(orm.get("onset_date")),
                    resolved_date=_parse_dt(orm.get("resolved_date")),
                    code=orm.get("code"),
                    coding_system=orm.get("coding_system"),
                )
            )
            if old_id_str:
                id_remap[old_id_str] = str(existing_id)
            return "updated"
        patient_id = _uuid(orm.get("patient_id"))
        ev = ClinicalEvent(
            id=new_id,
            tenant_id=tenant_id,
            patient_id=patient_id or uuid4(),
            status=orm.get("status"),
            title=orm.get("title") or "Untitled Condition",
            description=orm.get("description"),
            onset_date=_parse_dt(orm.get("onset_date")),
            resolved_date=_parse_dt(orm.get("resolved_date")),
            code=orm.get("code"),
            coding_system=orm.get("coding_system"),
        )
        self.db.add(ev)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    async def _upsert_encounter(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> str:
        # ExaminationModel declares tenant_id manually (NOT via TenantMixin) but it's NOT NULL.
        existing_id, new_id, action = await self._resolve_id(
            ExaminationModel, old_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(ExaminationModel)
                .where(ExaminationModel.id == existing_id)
                .values(
                    examination_date=_parse_date(orm.get("examination_date")),
                    organization_id=_uuid(orm.get("organization_id")),
                    notes=orm.get("notes"),
                    diagnoses=orm.get("diagnoses"),
                )
            )
            if old_id_str:
                id_remap[old_id_str] = str(existing_id)
            return "updated"
        patient_id = _uuid(orm.get("patient_id"))
        exam = ExaminationModel(
            id=new_id,
            tenant_id=tenant_id,
            patient_id=patient_id,
            examination_date=_parse_date(orm.get("examination_date")),
            organization_id=_uuid(orm.get("organization_id")),
            notes=orm.get("notes"),
            diagnoses=orm.get("diagnoses"),
        )
        self.db.add(exam)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    async def _upsert_device(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(
            DeviceModel, old_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(DeviceModel)
                .where(DeviceModel.id == existing_id)
                .values(
                    identifier=orm.get("identifier"),
                    device_name=orm.get("device_name"),
                    type=orm.get("type"),
                    manufacturer=orm.get("manufacturer"),
                    model_number=orm.get("model_number"),
                    serial_number=orm.get("serial_number"),
                    status=orm.get("status") or "active",
                    owner_integration_id=_uuid(orm.get("owner_integration_id")),
                    patient_id=_uuid(orm.get("patient_id")),
                )
            )
            if old_id_str:
                id_remap[old_id_str] = str(existing_id)
            return "updated"
        dev = DeviceModel(
            id=new_id,
            tenant_id=tenant_id,
            identifier=orm.get("identifier"),
            device_name=orm.get("device_name"),
            type=orm.get("type"),
            manufacturer=orm.get("manufacturer"),
            model_number=orm.get("model_number"),
            serial_number=orm.get("serial_number"),
            status=orm.get("status") or "active",
            owner_integration_id=_uuid(orm.get("owner_integration_id")),
            patient_id=_uuid(orm.get("patient_id")),
        )
        self.db.add(dev)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    async def _upsert_communication(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(
            CommunicationModel, old_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            await self.db.execute(
                sa_update(CommunicationModel)
                .where(CommunicationModel.id == existing_id)
                .values(
                    status=orm.get("status") or "completed",
                    category=orm.get("category"),
                    priority=orm.get("priority"),
                    topic=orm.get("topic"),
                    payload=orm.get("payload"),
                    sent=_parse_dt(orm.get("sent")),
                    received=_parse_dt(orm.get("received")),
                    sender=orm.get("sender"),
                    recipient=orm.get("recipient"),
                    subject_patient_id=_uuid(orm.get("subject_patient_id")),
                    encounter_id=_uuid(orm.get("encounter_id")),
                )
            )
            if old_id_str:
                id_remap[old_id_str] = str(existing_id)
            return "updated"
        comm = CommunicationModel(
            id=new_id,
            tenant_id=tenant_id,
            status=orm.get("status") or "completed",
            category=orm.get("category"),
            priority=orm.get("priority"),
            topic=orm.get("topic"),
            payload=orm.get("payload"),
            sent=_parse_dt(orm.get("sent")),
            received=_parse_dt(orm.get("received")),
            sender=orm.get("sender"),
            recipient=orm.get("recipient"),
            subject_patient_id=_uuid(orm.get("subject_patient_id")),
            encounter_id=_uuid(orm.get("encounter_id")),
        )
        self.db.add(comm)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    async def _upsert_provenance(
        self,
        orm: Dict[str, Any],
        old_id_str: Optional[str],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        *,
        force_id: Optional[UUID] = None,
    ) -> str:
        # Provenance is immutable (no VersionedMixin, no SoftDeleteMixin). Upsert
        # is create-only: if the id already exists, leave the original untouched
        # and count as "updated" (idempotent no-op) rather than overwriting history.
        existing_id, new_id, action = await self._resolve_id(
            ProvenanceModel, old_id_str, tenant_id, force_id=force_id
        )
        if existing_id:
            if old_id_str:
                id_remap[old_id_str] = str(existing_id)
            return "updated"
        prov = ProvenanceModel(
            id=new_id,
            tenant_id=tenant_id,
            target=orm.get("target") or [],
            recorded=_parse_dt(orm.get("recorded")) or datetime.now(timezone.utc),
            activity=orm.get("activity"),
            agent=orm.get("agent") or [],
            entity=orm.get("entity"),
        )
        self.db.add(prov)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    # ---------------- non-FHIR sidecar restore ----------------

    async def restore_sidecar(
        self, name: str, payload: Any, tenant_id: UUID, id_remap: Dict[str, str]
    ) -> Tuple[Dict[str, int], List[str], List[str]]:
        created: Dict[str, int] = {}
        errors: List[str] = []
        warnings: List[str] = []

        if name == "telemetry.json":
            created["telemetry"] = await self._restore_telemetry(payload, tenant_id)
        elif name == "integrations.json":
            created["integrations"], w = await self._restore_integrations(
                payload, tenant_id, id_remap
            )
            warnings.extend(w)
        elif name == "notification_triggers.json":
            created["notification_triggers"] = await self._restore_triggers(
                payload, tenant_id, id_remap
            )
        elif name == "examinations.json":
            created["examinations"] = await self._restore_examinations(
                payload, tenant_id, id_remap
            )
        elif name == "clinical_events.json":
            created["clinical_events"] = await self._restore_clinical_events(
                payload, tenant_id, id_remap
            )
        elif name == "biomarker_definitions.json":
            created["biomarker_definitions"] = await self._restore_biomarker_catalog(
                payload, tenant_id
            )
        elif name == "medication_catalog.json":
            created["medication_catalog"] = await self._restore_medication_catalog(
                payload, tenant_id
            )
        elif name == "allergy_catalog.json":
            created["allergy_catalog"] = await self._restore_allergy_catalog(
                payload, tenant_id
            )
        elif name == "clinical_event_types.json":
            created["clinical_event_types"] = await self._restore_clinical_event_types(
                payload, tenant_id
            )
        elif name == "concepts.json":
            created["concepts"] = await self._restore_concepts(
                payload, tenant_id, id_remap
            )
        elif name == "anatomy.json":
            created["anatomy_structures"], created[
                "anatomy_relations"
            ] = await self._restore_anatomy(payload, tenant_id, id_remap)
        elif name == "concept_edges.json":
            created["concept_edges"] = await self._restore_concept_edges(
                payload, tenant_id, id_remap
            )
        elif name == "ai_config.json":
            warnings.append("AI config restore is not supported in v1 (export-only).")
        elif name == "documents.json":
            pass
        else:
            warnings.append(f"Unknown sidecar {name}; skipped")
        return created, errors, warnings

    async def _restore_telemetry(
        self, payload: List[Dict[str, Any]], tenant_id: UUID
    ) -> int:
        count = 0
        for item in payload:
            try:
                row = TelemetryDataModel(
                    tenant_id=tenant_id,
                    device_id=item.get("device_id") or "imported",
                    timestamp=_parse_dt(item.get("timestamp"))
                    or datetime.now(timezone.utc),
                    data=item.get("data"),
                    heart_rate=item.get("heart_rate"),
                    steps=item.get("steps"),
                    calories=item.get("calories"),
                )
                self.db.add(row)
                count += 1
            except Exception as e:
                logger.warning(f"telemetry row skipped: {e}")
        await self.db.flush()
        return count

    async def _restore_integrations(
        self, payload: List[Dict[str, Any]], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> Tuple[int, List[str]]:
        warnings: List[str] = []
        if not settings.INTEGRATION_SECRET_KEY:
            warnings.append(
                "INTEGRATION_SECRET_KEY not set; imported integration secrets will not decrypt."
            )
        count = 0
        for item in payload:
            try:
                old_id = item.get("id")
                old_uuid = _uuid(old_id)
                existing = None
                if old_uuid:
                    res = await self.db.execute(
                        select(UserIntegration).where(UserIntegration.id == old_uuid)
                    )
                    existing = res.scalar_one_or_none()
                patient_id = _uuid(item.get("patient_id"))
                if patient_id and str(item.get("patient_id")) in id_remap:
                    patient_id = _uuid(id_remap[str(item.get("patient_id"))])
                if existing and existing.tenant_id == tenant_id:
                    await self.db.execute(
                        sa_update(UserIntegration)
                        .where(UserIntegration.id == existing.id)
                        .values(
                            status=item.get("status"),
                            access_token=item.get("access_token"),
                            refresh_token=item.get("refresh_token"),
                            expires_at=_parse_dt(item.get("expires_at")),
                            scopes=item.get("scopes"),
                            user_config=item.get("user_config"),
                            instance_name=item.get("instance_name"),
                        )
                    )
                else:
                    from app.models.enums import IntegrationStatus

                    status_val = item.get("status")
                    try:
                        status_enum = (
                            IntegrationStatus(status_val)
                            if status_val
                            else IntegrationStatus.PENDING
                        )
                    except ValueError:
                        status_enum = IntegrationStatus.PENDING
                    integ = UserIntegration(
                        tenant_id=tenant_id,
                        user_id=_uuid(item.get("user_id")) or uuid4(),
                        patient_id=patient_id or uuid4(),
                        provider=item.get("provider") or "unknown",
                        status=status_enum,
                        access_token=item.get("access_token"),
                        refresh_token=item.get("refresh_token"),
                        expires_at=_parse_dt(item.get("expires_at")),
                        scopes=item.get("scopes"),
                        provider_account_id=item.get("provider_account_id"),
                        instance_name=item.get("instance_name"),
                        is_debug_enabled=bool(item.get("is_debug_enabled")),
                        user_config=item.get("user_config"),
                    )
                    self.db.add(integ)
                count += 1
            except Exception as e:
                logger.warning(f"integration row skipped: {e}")
        await self.db.flush()
        return count, warnings

    async def _restore_triggers(
        self, payload: List[Dict[str, Any]], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> int:
        from app.models.enums import NotificationType, TriggerType

        count = 0
        for item in payload:
            try:
                pid = item.get("patient_id")
                if pid and str(pid) in id_remap:
                    pid = id_remap[str(pid)]
                patient_uuid = _uuid(pid)
                if not patient_uuid:
                    continue
                try:
                    tt = TriggerType(item.get("trigger_type"))
                    nt = NotificationType(item.get("notification_type"))
                except (ValueError, TypeError):
                    continue
                trig = NotificationTrigger(
                    tenant_id=tenant_id,
                    patient_id=patient_uuid,
                    trigger_type=tt,
                    notification_type=nt,
                    config=item.get("config") or {},
                    title=item.get("title") or "Imported trigger",
                    body=item.get("body"),
                    enabled=bool(item.get("enabled", True)),
                    next_trigger=_parse_dt(item.get("next_trigger")),
                    reference_id=_uuid(item.get("reference_id")),
                )
                self.db.add(trig)
                count += 1
            except Exception as e:
                logger.warning(f"trigger row skipped: {e}")
        await self.db.flush()
        return count

    async def _restore_examinations(
        self, payload: List[Dict[str, Any]], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> int:
        count = 0
        for item in payload:
            try:
                pid = item.get("patient_id")
                if pid and str(pid) in id_remap:
                    pid = id_remap[str(pid)]
                # category_concept_id: prefer a concept that was just imported
                # (recorded in id_remap by _restore_concepts); else resolve by
                # slug against the target's visible taxonomy (covers the common
                # case of an exam pointing at a global/seeded concept we did
                # not export). Accept legacy `category_id`/`category_details`
                # keys from older backups, plus the new `category_concept_id`/
                # `category_concept` keys.
                category_concept_id = await self._resolve_concept_fk(
                    item.get("category_concept_id", item.get("category_id")),
                    (
                        item.get("category_concept")
                        or item.get("category_details")
                        or {}
                    ).get("slug"),
                    ConceptKind.EXAMINATION_CATEGORY,
                    tenant_id,
                    id_remap,
                )
                # organization_id: remap if the org was created during FHIR
                # restore; else carry through only if it exists in-tenant.
                org_id_raw = item.get("organization_id")
                organization_id = _uuid(
                    id_remap.get(str(org_id_raw), org_id_raw)
                ) if org_id_raw else None
                exam = ExaminationModel(
                    tenant_id=tenant_id,
                    patient_id=_uuid(pid),
                    examination_date=_parse_date(item.get("examination_date")),
                    notes=item.get("notes"),
                    patient_notes=item.get("patient_notes"),
                    category_concept_id=category_concept_id,
                    organization_id=organization_id,
                    source_integration_id=_uuid(item.get("source_integration_id")),
                    external_id=item.get("external_id"),
                    auto_extract_metadata=bool(item.get("auto_extract_metadata", False)),
                    diagnoses=item.get("diagnoses"),
                    impressions=item.get("impressions"),
                    extraction_status=item.get("extraction_status"),
                )
                self.db.add(exam)
                count += 1
            except Exception as e:
                logger.warning(f"examination row skipped: {e}")
        await self.db.flush()
        return count

    async def _restore_clinical_events(
        self, payload: List[Dict[str, Any]], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> int:
        from app.models.enums import ClinicalEventStatus

        count = 0
        for item in payload:
            try:
                pid = item.get("patient_id")
                if pid and str(pid) in id_remap:
                    pid = id_remap[str(pid)]
                try:
                    status = ClinicalEventStatus(item.get("status", "ACTIVE"))
                except ValueError:
                    status = ClinicalEventStatus.ACTIVE
                ev = ClinicalEvent(
                    tenant_id=tenant_id,
                    patient_id=_uuid(pid) or uuid4(),
                    type_id=_uuid(item.get("type_id")),
                    status=status,
                    title=item.get("title") or "Imported event",
                    description=item.get("description"),
                    onset_date=_parse_dt(item.get("onset_date")),
                    resolved_date=_parse_dt(item.get("resolved_date")),
                    occurrences=item.get("occurrences"),
                    event_metadata=item.get("event_metadata"),
                )
                self.db.add(ev)
                count += 1
            except Exception as e:
                logger.warning(f"clinical_event row skipped: {e}")
        await self.db.flush()
        return count

    async def _restore_biomarker_catalog(
        self, payload: Dict[str, Any], tenant_id: UUID
    ) -> int:
        from app.services.catalog_import_service import CatalogImportService
        from app.schemas.biomarker import (
            BiomarkerCreate,
            CatalogImportPayload,
            UnitCreate,
        )

        units = [
            UnitCreate(
                symbol=u["symbol"],
                name=u["name"],
                quantity_type=u.get("quantity_type", "OTHER"),
            )
            for u in payload.get("units", [])
        ]
        biomarkers = []
        for b in payload.get("biomarkers", []):
            try:
                biomarkers.append(
                    BiomarkerCreate(
                        slug=b["slug"],
                        name=b["name"],
                        coding_system=b.get("coding_system", "loinc"),
                        code=b.get("code"),
                        category=b.get("category"),
                        class_concept_slug=b.get("class_concept_slug"),
                        aliases=b.get("aliases", []),
                        info=b.get("info"),
                        reference_range_min=b.get("reference_range_min"),
                        reference_range_max=b.get("reference_range_max"),
                        is_telemetry=b.get("is_telemetry", False),
                        preferred_unit_symbol=b.get("preferred_unit_symbol"),
                    )
                )
            except Exception as e:
                logger.warning(f"biomarker skipped: {e}")
        cat_payload = CatalogImportPayload(
            metadata=None,
            units=units,
            biomarkers=biomarkers,
        )
        svc = CatalogImportService(self.db)
        stats = await svc.import_catalog(cat_payload)
        return stats.get("biomarkers_added", 0) + stats.get("biomarkers_updated", 0)

    async def _restore_clinical_event_types(
        self, payload: Dict[str, Any], tenant_id: UUID
    ) -> int:
        from app.models.concept_model import Concept, ConceptKindTag
        from app.models.enums import ConceptKind, ConceptStatus
        from app.services.concept_service import concepts_with_kind

        count = 0
        # Event categories are now Concepts (kind=event_category). Upsert by slug.
        for c in payload.get("categories", []):
            try:
                slug = c.get("slug")
                if not slug:
                    continue
                res = await self.db.execute(
                    select(Concept).where(
                        Concept.slug == slug,
                        concepts_with_kind(ConceptKind.EVENT_CATEGORY),
                        or_(
                            Concept.tenant_id == tenant_id,
                            Concept.tenant_id.is_(None),
                        ),
                    )
                )
                if not res.scalar_one_or_none():
                    new_cat = Concept(
                        tenant_id=tenant_id,
                        name=c.get("name") or slug,
                        slug=slug,
                        primary_kind=ConceptKind.EVENT_CATEGORY,
                        description=c.get("description"),
                        icon=c.get("icon"),
                        color=c.get("color"),
                        status=ConceptStatus.ACTIVE,
                    )
                    new_cat.kind_tags.append(
                        ConceptKindTag(kind=ConceptKind.EVENT_CATEGORY)
                    )
                    self.db.add(new_cat)
                    count += 1
            except Exception as e:
                logger.warning(f"category skipped: {e}")
        for t in payload.get("types", []):
            try:
                res = await self.db.execute(
                    select(ClinicalEventType).where(
                        ClinicalEventType.slug == t.get("slug")
                    )
                )
                if not res.scalar_one_or_none():
                    self.db.add(
                        ClinicalEventType(
                            tenant_id=tenant_id,
                            name=t.get("name") or t.get("slug"),
                            slug=t.get("slug"),
                            description=t.get("description"),
                            icon=t.get("icon"),
                            color=t.get("color"),
                            metadata_schema=t.get("metadata_schema"),
                        )
                    )
                    count += 1
            except Exception as e:
                logger.warning(f"event type skipped: {e}")
        await self.db.flush()
        return count

    # ---------------- taxonomy + anatomy restore ----------------

    async def _resolve_concept_fk(
        self,
        old_id: Any,
        slug: Optional[str],
        kind: Optional[ConceptKind],
        tenant_id: UUID,
        id_remap: Dict[str, str],
    ) -> Optional[UUID]:
        """Resolve an exported concept FK to a target-tenant concept id.

        Order: (1) id_remap (a concept just imported via ``_restore_concepts``),
        (2) the source id verbatim if a matching in-tenant row already exists,
        (3) slug lookup against the target's visible taxonomy (covers global/
        seeded concepts we deliberately did not export). Returns ``None`` if
        nothing resolves — callers leave the FK NULL (soft-fail policy)."""
        if not old_id:
            return None
        key = str(old_id)
        if key in id_remap:
            return _uuid(id_remap[key])
        cand = _uuid(key)
        if cand is not None:
            res = await self.db.execute(
                select(Concept.id).where(
                    Concept.id == cand,
                    or_(
                        Concept.tenant_id == tenant_id,
                        Concept.tenant_id.is_(None),
                    ),
                    Concept.deleted_at.is_(None),
                )
            )
            row = res.first()
            if row:
                return row[0]
        if slug:
            from app.services.concept_service import resolve_concept_by_slug

            return await resolve_concept_by_slug(
                self.db, slug, kind, tenant_id=tenant_id
            )
        return None

    async def _restore_concepts(
        self,
        payload: Dict[str, Any],
        tenant_id: UUID,
        id_remap: Dict[str, str],
    ) -> int:
        """Upsert tenant-scoped concepts and record old→new ids in id_remap.

        Idempotent: upsert by ``(slug, tenant_id)``. Kind tags are reconciled
        additively (missing tags added; existing extras preserved — matches the
        seed behavior). ``parent_id`` is resolved via id_remap, with a deferred
        second pass for children that appear before their parent in the payload.
        """
        raw = payload.get("concepts", []) if isinstance(payload, dict) else []
        # Two-pass: parents may follow children in the export ordering.
        deferred: List[Dict[str, Any]] = []
        count = 0

        async def _upsert_one(c: Dict[str, Any]) -> Optional[str]:
            slug = c.get("slug")
            if not slug:
                return None
            try:
                res = await self.db.execute(
                    select(Concept).where(
                        Concept.slug == slug,
                        or_(
                            Concept.tenant_id == tenant_id,
                            Concept.tenant_id.is_(None),
                        ),
                        Concept.deleted_at.is_(None),
                    )
                )
                existing = res.scalar_one_or_none()
                kind_strs = c.get("kinds") or []
                if not kind_strs and c.get("primary_kind"):
                    kind_strs = [c.get("primary_kind")]

                parent_id: Optional[UUID] = None
                parent_raw = c.get("parent_id")
                if parent_raw:
                    parent_key = str(parent_raw)
                    if parent_key in id_remap:
                        parent_id = _uuid(id_remap[parent_key])
                    else:
                        return "__defer__"

                if existing and existing.tenant_id == tenant_id:
                    # Update tenant-scoped row in place; reconcile kind tags.
                    existing.name = c.get("name") or existing.name
                    existing.description = c.get("description", existing.description)
                    existing.coding_system = c.get(
                        "coding_system", existing.coding_system
                    )
                    existing.code = c.get("code", existing.code)
                    existing.aliases = c.get("aliases") or existing.aliases
                    existing.icon = c.get("icon", existing.icon)
                    existing.color = c.get("color", existing.color)
                    existing.display_order = c.get(
                        "display_order", existing.display_order
                    )
                    if c.get("meta_data") is not None:
                        existing.meta_data = c.get("meta_data")
                    if parent_id is not None:
                        existing.parent_id = parent_id
                    await self._reconcile_kind_tags(existing, kind_strs)
                    await self.db.flush()
                    id_remap[str(c["id"])] = str(existing.id)
                    return str(existing.id)
                if existing:
                    # Global row already present; remap to it without mutating.
                    id_remap[str(c["id"])] = str(existing.id)
                    return str(existing.id)
                primary_raw = c.get("primary_kind")
                try:
                    primary_kind = (
                        ConceptKind(primary_raw) if primary_raw else None
                    )
                except ValueError:
                    primary_kind = None
                new_c = Concept(
                    tenant_id=tenant_id,
                    name=c.get("name") or slug,
                    slug=slug,
                    primary_kind=primary_kind,
                    parent_id=parent_id,
                    description=c.get("description"),
                    coding_system=c.get("coding_system"),
                    code=c.get("code"),
                    aliases=c.get("aliases") or [],
                    icon=c.get("icon"),
                    color=c.get("color"),
                    status=ConceptStatus.ACTIVE,
                    display_order=c.get("display_order", 0),
                    meta_data=c.get("meta_data"),
                )
                await self._reconcile_kind_tags(new_c, kind_strs)
                self.db.add(new_c)
                await self.db.flush()
                id_remap[str(c["id"])] = str(new_c.id)
                return str(new_c.id)
            except Exception as e:
                logger.warning(f"concept skipped: {e}")
                return None

        for c in raw:
            if not isinstance(c, dict) or not c.get("id") or not c.get("slug"):
                continue
            result = await _upsert_one(c)
            if result == "__defer__":
                deferred.append(c)
            elif result:
                count += 1
        for c in deferred:
            result = await _upsert_one(c)
            if result and result != "__defer__":
                count += 1
            else:
                # Parent still unresolved: insert with parent_id NULL rather
                # than drop the concept entirely.
                c = {**c, "parent_id": None}
                result = await _upsert_one(c)
                if result:
                    count += 1
        return count

    async def _reconcile_kind_tags(
        self, concept: Concept, kind_strs: List[str]
    ) -> None:
        """Add missing kind tags; preserve extras (additive reconciliation)."""
        existing = {t.kind for t in (concept.kind_tags or [])}
        for k in kind_strs:
            if not k:
                continue
            try:
                ke = ConceptKind(k)
            except ValueError:
                continue
            if ke not in existing:
                concept.kind_tags.append(ConceptKindTag(kind=ke))
                existing.add(ke)

    async def _restore_anatomy(
        self,
        payload: Dict[str, Any],
        tenant_id: UUID,
        id_remap: Dict[str, str],
    ) -> Tuple[int, int]:
        """Upsert custom/tenant anatomy structures + the relations between
        them. ``class_concept_id`` is remapped via id_remap (concepts restored
        first) with slug fallback. Returns (structures_count, relations_count)."""
        structures = payload.get("structures", []) if isinstance(payload, dict) else []
        relations = payload.get("relations", []) if isinstance(payload, dict) else []
        struct_count = 0
        for s in structures:
            if not isinstance(s, dict) or not s.get("slug"):
                continue
            try:
                res = await self.db.execute(
                    select(AnatomyStructure).where(
                        AnatomyStructure.slug == s["slug"],
                        or_(
                            AnatomyStructure.tenant_id == tenant_id,
                            AnatomyStructure.tenant_id.is_(None),
                        ),
                    )
                )
                existing = res.scalar_one_or_none()
                class_concept_id = await self._resolve_concept_fk(
                    s.get("class_concept_id"),
                    None,
                    ConceptKind.ANATOMY_CLASS,
                    tenant_id,
                    id_remap,
                )
                std_system = None
                ss = s.get("standard_system")
                if ss:
                    try:
                        std_system = CodingSystem(ss)
                    except ValueError:
                        std_system = None
                if existing and existing.tenant_id == tenant_id:
                    existing.name = s.get("name") or existing.name
                    existing.description = s.get("description", existing.description)
                    if class_concept_id is not None:
                        existing.class_concept_id = class_concept_id
                    existing.display = s.get("display", existing.display)
                    existing.is_custom = bool(s.get("is_custom", existing.is_custom))
                    existing.standard_system = std_system
                    existing.standard_code = s.get("standard_code")
                    id_remap[str(s["id"])] = str(existing.id)
                    struct_count += 1
                elif existing:
                    id_remap[str(s["id"])] = str(existing.id)
                else:
                    new_s = AnatomyStructure(
                        tenant_id=tenant_id,
                        name=s.get("name") or s["slug"],
                        slug=s["slug"],
                        class_concept_id=class_concept_id,
                        standard_system=std_system,
                        standard_code=s.get("standard_code"),
                        description=s.get("description"),
                        is_custom=bool(s.get("is_custom", True)),
                        display=s.get("display"),
                    )
                    self.db.add(new_s)
                    await self.db.flush()
                    id_remap[str(s["id"])] = str(new_s.id)
                    struct_count += 1
            except Exception as e:
                logger.warning(f"anatomy structure skipped: {e}")
        await self.db.flush()

        rel_count = 0
        for r in relations:
            if not isinstance(r, dict):
                continue
            try:
                src = self._remap_anatomy_endpoint(r.get("source_id"), id_remap)
                dst = self._remap_anatomy_endpoint(r.get("target_id"), id_remap)
                if src is None or dst is None:
                    logger.warning(
                        "anatomy relation skipped: unresolved endpoint (%s,%s)",
                        r.get("source_id"),
                        r.get("target_id"),
                    )
                    continue
                rel_type = r.get("relation_type")
                if not rel_type:
                    continue
                try:
                    rt = ConceptRelationType(rel_type)
                except ValueError:
                    continue
                res = await self.db.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.src_type == EdgeEndpointType.ANATOMY,
                        ConceptEdge.src_id == src,
                        ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
                        ConceptEdge.dst_id == dst,
                        ConceptEdge.relation == rt,
                    )
                )
                if res.scalar_one_or_none():
                    continue
                self.db.add(
                    ConceptEdge(
                        src_type=EdgeEndpointType.ANATOMY,
                        src_id=src,
                        dst_type=EdgeEndpointType.ANATOMY,
                        dst_id=dst,
                        relation=rt,
                        status=EdgeApprovalStatus.APPROVED,
                    )
                )
                rel_count += 1
            except Exception as e:
                logger.warning(f"anatomy relation skipped: {e}")
        await self.db.flush()
        return struct_count, rel_count

    @staticmethod
    def _remap_anatomy_endpoint(
        raw: Any, id_remap: Dict[str, str]
    ) -> Optional[UUID]:
        if not raw:
            return None
        key = str(raw)
        if key in id_remap:
            return _uuid(id_remap[key])
        return _uuid(key)

    async def _restore_concept_edges(
        self,
        payload: Dict[str, Any],
        tenant_id: UUID,
        id_remap: Dict[str, str],
    ) -> int:
        """Upsert polymorphic concept edges (the knowledge graph).

        Endpoints are remapped via id_remap first. For CONCEPT/ANATOMY
        endpoints not present in the remap (e.g. an edge pointing at a global
        concept we did not export), fall back to the source id only if a
        matching in-tenant/global row exists; otherwise skip the edge with a
        warning. Edges referencing biomarkers/examinations/doctors are remapped
        through id_remap when present (those sidecars/FHIR entries are restored
        before edges). Upsert by the natural key
        ``(src_type, src_id, dst_type, dst_id, relation)`` for idempotency."""
        edges = payload.get("edges", []) if isinstance(payload, dict) else []
        count = 0
        for e in edges:
            if not isinstance(e, dict):
                continue
            try:
                src = await self._resolve_edge_endpoint(
                    e.get("src_type"), e.get("src_id"), tenant_id, id_remap
                )
                dst = await self._resolve_edge_endpoint(
                    e.get("dst_type"), e.get("dst_id"), tenant_id, id_remap
                )
                if src is None or dst is None:
                    logger.warning(
                        "concept edge skipped: unresolved endpoint src=%s/%s dst=%s/%s",
                        e.get("src_type"),
                        e.get("src_id"),
                        e.get("dst_type"),
                        e.get("dst_id"),
                    )
                    continue
                try:
                    relation = ConceptRelationType(e.get("relation"))
                except (ValueError, TypeError):
                    continue
                res = await self.db.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.src_type == src[0],
                        ConceptEdge.src_id == src[1],
                        ConceptEdge.dst_type == dst[0],
                        ConceptEdge.dst_id == dst[1],
                        ConceptEdge.relation == relation,
                    )
                )
                existing = res.scalar_one_or_none()
                source_val = e.get("source")
                try:
                    prov = (
                        ConceptProvenance(source_val)
                        if source_val
                        else ConceptProvenance.MANUAL
                    )
                except ValueError:
                    prov = ConceptProvenance.MANUAL
                status_val = e.get("status")
                try:
                    status = (
                        EdgeApprovalStatus(status_val)
                        if status_val
                        else EdgeApprovalStatus.APPROVED
                    )
                except ValueError:
                    status = EdgeApprovalStatus.APPROVED
                if existing:
                    existing.status = status
                    existing.properties = e.get("properties", existing.properties)
                    existing.evidence = e.get("evidence", existing.evidence)
                    count += 1
                    continue
                self.db.add(
                    ConceptEdge(
                        tenant_id=tenant_id,
                        src_type=src[0],
                        src_id=src[1],
                        dst_type=dst[0],
                        dst_id=dst[1],
                        relation=relation,
                        properties=e.get("properties"),
                        evidence=e.get("evidence"),
                        source=prov,
                        status=status,
                    )
                )
                count += 1
            except Exception as ex:
                logger.warning(f"concept edge skipped: {ex}")
        await self.db.flush()
        return count

    async def _resolve_edge_endpoint(
        self,
        type_str: Optional[str],
        id_raw: Any,
        tenant_id: UUID,
        id_remap: Dict[str, str],
    ) -> Optional[Tuple[EdgeEndpointType, UUID]]:
        """Resolve an edge endpoint to ``(type, uuid)``.

        Remaps via id_remap when the source id was carried in the backup
        (concept/anatomy/biomarker/exam restored earlier). For CONCEPT and
        ANATOMY endpoints absent from id_remap, accepts the bare id if a
        matching visible row exists (global/seeded target). Returns None if the
        endpoint cannot be resolved — the caller skips the edge."""
        if not type_str or not id_raw:
            return None
        try:
            etype = EdgeEndpointType(type_str)
        except ValueError:
            return None
        key = str(id_raw)
        if key in id_remap:
            resolved = _uuid(id_remap[key])
            if resolved is not None:
                return (etype, resolved)
        cand = _uuid(key)
        if cand is None:
            return None
        # Existence check for concept/anatomy (the types whose rows may be
        # global and thus absent from id_remap). Biomarker/exam/doctor endpoints
        # must have been remapped if they were exported; otherwise the edge is
        # genuinely dangling and skipped.
        if etype == EdgeEndpointType.CONCEPT:
            res = await self.db.execute(
                select(Concept.id).where(
                    Concept.id == cand,
                    or_(
                        Concept.tenant_id == tenant_id,
                        Concept.tenant_id.is_(None),
                    ),
                    Concept.deleted_at.is_(None),
                )
            )
            if res.first():
                return (etype, cand)
            return None
        if etype == EdgeEndpointType.ANATOMY:
            res = await self.db.execute(
                select(AnatomyStructure.id).where(
                    AnatomyStructure.id == cand,
                    or_(
                        AnatomyStructure.tenant_id == tenant_id,
                        AnatomyStructure.tenant_id.is_(None),
                    ),
                )
            )
            if res.first():
                return (etype, cand)
            return None
        return (etype, cand)

    async def _restore_medication_catalog(
        self, payload: Dict[str, Any], tenant_id: UUID
    ) -> int:
        """Upsert MedicationCatalog entries by name (idempotent with seeds)."""
        count = 0
        for m in payload.get("medications", []):
            name = m.get("name")
            if not name:
                continue
            try:
                res = await self.db.execute(
                    select(MedicationCatalog).where(
                        func.lower(MedicationCatalog.name) == func.lower(name)
                    )
                )
                existing = res.scalar_one_or_none()
                if existing:
                    existing.description = m.get("description", existing.description)
                    existing.indications = m.get("indications", existing.indications)
                    existing.side_effects = m.get("side_effects", existing.side_effects)
                    existing.contraindications = m.get(
                        "contraindications", existing.contraindications
                    )
                    existing.dosage_info = m.get("dosage_info", existing.dosage_info)
                else:
                    self.db.add(
                        MedicationCatalog(
                            tenant_id=tenant_id,
                            name=name,
                            description=m.get("description"),
                            indications=m.get("indications"),
                            side_effects=m.get("side_effects"),
                            contraindications=m.get("contraindications"),
                            dosage_info=m.get("dosage_info"),
                        )
                    )
                count += 1
            except Exception as e:
                logger.warning(f"medication catalog row skipped: {e}")
        await self.db.flush()
        return count

    async def _restore_allergy_catalog(
        self, payload: Dict[str, Any], tenant_id: UUID
    ) -> int:
        """Upsert AllergyCatalog entries by name (idempotent with seeds)."""
        count = 0
        for a in payload.get("allergies", []):
            name = a.get("name")
            if not name:
                continue
            try:
                category_raw = (a.get("category") or "").strip().upper()
                try:
                    category = AllergyCategory(category_raw) if category_raw else None
                except ValueError:
                    category = AllergyCategory.OTHER if category_raw else None
                res = await self.db.execute(
                    select(AllergyCatalog).where(
                        func.lower(AllergyCatalog.name) == func.lower(name)
                    )
                )
                existing = res.scalar_one_or_none()
                if existing:
                    if category is not None:
                        existing.category = category
                    existing.description = a.get("description", existing.description)
                    existing.typical_reactions = a.get(
                        "typical_reactions", existing.typical_reactions
                    )
                else:
                    self.db.add(
                        AllergyCatalog(
                            tenant_id=tenant_id,
                            name=name,
                            category=category or AllergyCategory.OTHER,
                            description=a.get("description"),
                            typical_reactions=a.get("typical_reactions"),
                        )
                    )
                count += 1
            except Exception as e:
                logger.warning(f"allergy catalog row skipped: {e}")
        await self.db.flush()
        return count

    # ---------------- document restore ----------------

    async def restore_documents(
        self,
        documents_meta: List[Dict[str, Any]],
        archive: Optional[zipfile.ZipFile],
        tenant_id: UUID,
        id_remap: Dict[str, str],
        owner_id: UUID,
    ) -> int:
        from app.services.document_service import UPLOAD_DIR as RESOLVED_UPLOAD_DIR

        tenant_dir = Path(str(RESOLVED_UPLOAD_DIR)) / str(tenant_id)
        tenant_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for meta in documents_meta:
            try:
                pid = meta.get("patient_id")
                if pid and str(pid) in id_remap:
                    pid = id_remap[str(pid)]
                archive_path = meta.get("_archive_path")
                target_name = f"{uuid4()}{os.path.splitext(meta.get('filename', ''))[1] or '.bin'}"
                target_path = tenant_dir / target_name
                if archive and archive_path:
                    try:
                        data = archive.read(archive_path)
                        target_path.write_bytes(data)
                    except KeyError:
                        target_path = None
                else:
                    target_path = None
                doc = DocumentModel(
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    patient_id=_uuid(pid),
                    filename=meta.get("filename") or "imported",
                    file_path=str(target_path) if target_path else "",
                    status=meta.get("status") or "uploaded",
                    extracted_text=meta.get("extracted_text"),
                    entities=meta.get("entities"),
                    include_in_extraction=bool(
                        meta.get("include_in_extraction", False)
                    ),
                    is_edited=bool(meta.get("is_edited", False)),
                )
                self.db.add(doc)
                count += 1
            except Exception as e:
                logger.warning(f"document skipped: {e}")
        await self.db.flush()
        return count

    # ---------------- orchestrator ----------------

    async def run_import(
        self,
        job_id: UUID,
        archive_path: str,
        owner_id: UUID,
        config: Optional[FHIRImportConfig] = None,
    ) -> RestoreResult:
        job = await self.get_job(job_id)
        if not job:
            raise ValueError(f"Import job {job_id} not found")
        tenant_id = job.tenant_id
        if not tenant_id:
            raise ValueError("Import job has no tenant_id")

        result = RestoreResult(job_id=str(job_id), status=JobStatus.PROCESSING)
        self._job_id = job_id
        try:
            await self._update_progress(job_id, 5, JobStatus.PROCESSING)

            if zipfile.is_zipfile(archive_path):
                (
                    created,
                    updated,
                    errors,
                    warnings,
                    manifest_verified,
                    fhir_validated,
                ) = await self._restore_from_zip(
                    archive_path, tenant_id, owner_id, job_id, config
                )
            else:
                (
                    created,
                    updated,
                    errors,
                    warnings,
                    manifest_verified,
                    fhir_validated,
                ) = await self._restore_from_bare_json(
                    archive_path, tenant_id, job_id, config
                )

            result.created_resources = created
            result.updated_resources = updated
            result.errors = errors
            result.warnings = warnings
            result.manifest_verified = manifest_verified
            result.fhir_validated = fhir_validated
            result.processed_records = sum(created.values()) + sum(updated.values())
            result.failed_records = len(errors)
            result.total_records = result.processed_records + result.failed_records
            result.status = JobStatus.COMPLETED if not errors else JobStatus.PARTIAL

            await self._complete_job(job_id, result)
            return result
        except Exception as e:
            logger.exception(f"Import job {job_id} failed")
            await self._fail_job(job_id, str(e))
            result.status = JobStatus.FAILED
            result.errors = [str(e)]
            raise

    async def _restore_from_zip(
        self,
        archive_path: str,
        tenant_id: UUID,
        owner_id: UUID,
        job_id: UUID,
        config: Optional[FHIRImportConfig] = None,
    ) -> Tuple[Dict[str, int], Dict[str, int], List[str], List[str], bool, bool]:
        created: Dict[str, int] = {}
        updated: Dict[str, int] = {}
        errors: List[str] = []
        warnings: List[str] = []
        manifest_verified = False
        fhir_validated = False

        with zipfile.ZipFile(archive_path, "r") as zf:
            ok, manifest, merrors = self.verify_manifest_from_zip(zf)
            manifest_verified = ok
            if not ok:
                errors.extend(merrors)
                warnings.append(
                    "Manifest verification failed; continuing with best-effort restore."
                )

            await self._update_progress(job_id, 20)

            try:
                bundle_bytes = zf.read("fhir/bundle.json")
                bundle = json.loads(bundle_bytes)
            except KeyError:
                bundle = None
                warnings.append(
                    "No fhir/bundle.json in archive (catalog/sidecar-only backup)."
                )

            id_remap: Dict[str, str] = {}
            if bundle:
                ok, verrors = validate_bundle(bundle)
                fhir_validated = ok
                if not ok:
                    errors.extend(verrors)
                _brr = await self.restore_fhir_bundle(
                    bundle,
                    tenant_id,
                    validate=False,
                    config=config,
                    actor_user_id=owner_id,
                    source_job_id=job_id,
                )
                created.update(_brr.created)
                updated.update(_brr.updated)
                errors.extend(_brr.errors)
                warnings.extend(_brr.warnings)
                id_remap = _brr.id_remap
                await self._update_progress(job_id, 50)

            # Order matters: concepts + anatomy are FK targets for biomarker
            # classes, examination categories, and edge endpoints, so they must
            # materialize first. Edges are polymorphic and reference
            # concepts/anatomy/biomarkers/examinations, so they go last.
            for name in [
                "concepts.json",
                "anatomy.json",
                "biomarker_definitions.json",
                "clinical_event_types.json",
                "examinations.json",
                "clinical_events.json",
                "notification_triggers.json",
                "telemetry.json",
                "integrations.json",
                "ai_config.json",
                "concept_edges.json",
            ]:
                try:
                    payload = json.loads(zf.read(f"nonfhir/{name}"))
                except KeyError:
                    continue
                c, e, w = await self.restore_sidecar(name, payload, tenant_id, id_remap)
                created.update(c)
                errors.extend(e)
                warnings.extend(w)
                await self._update_progress(job_id, 70)

            try:
                documents_meta = json.loads(zf.read("nonfhir/documents.json"))
                n = await self.restore_documents(
                    documents_meta, zf, tenant_id, id_remap, owner_id
                )
                created["documents"] = n
            except KeyError:
                pass

        await self.db.commit()
        return created, updated, errors, warnings, manifest_verified, fhir_validated

    async def _restore_from_bare_json(
        self,
        path: str,
        tenant_id: UUID,
        job_id: UUID,
        config: Optional[FHIRImportConfig] = None,
    ) -> Tuple[Dict[str, int], Dict[str, int], List[str], List[str], bool, bool]:
        created: Dict[str, int] = {}
        updated: Dict[str, int] = {}
        errors: List[str] = []
        warnings: List[str] = []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if data.get("resourceType") == "Bundle":
            ok, verrors = validate_bundle(data)
            if not ok:
                errors.extend(verrors)
            _brr = await self.restore_fhir_bundle(
                data,
                tenant_id,
                validate=False,
                config=config,
                source_job_id=job_id,
            )
            created.update(_brr.created)
            updated.update(_brr.updated)
            errors.extend(_brr.errors)
            warnings.extend(_brr.warnings)
            await self.db.commit()
            return created, updated, errors, warnings, False, ok

        if "biomarkers" in data or "units" in data:
            n = await self._restore_biomarker_catalog(data, tenant_id)
            created["biomarker_definitions"] = n
            await self.db.commit()
            return created, updated, errors, warnings, False, False

        errors.append("Unrecognized JSON: not a FHIR Bundle and not a catalog payload.")
        return created, updated, errors, warnings, False, False

    # ---------------- legacy CSV/OCR (kept for endpoint compat) ----------------

    async def import_from_csv(
        self,
        file_path: Path,
        tenant_id: str,
        patient_id: Optional[str] = None,
        config: Optional[CSVImportConfig] = None,
    ) -> ImportResult:
        from app.processors.importers.csv_importer import CSVImporter

        importer = CSVImporter(config)
        result = await importer.import_from_file(file_path, tenant_id, patient_id)
        return result

    async def import_from_ocr(
        self,
        file_path: Path,
        tenant_id: str,
        patient_id: Optional[str] = None,
        config: Optional[OCRImportConfig] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
    ) -> ImportResult:
        try:
            from app.ai.processors.ocr import get_ocr_processor

            config = config or OCRImportConfig()
            ocr_processor = get_ocr_processor(
                provider=config.provider,
                api_key=api_key,
                api_base=api_base,
                model=model,
            )
            _text = await ocr_processor.extract_text(file_path)
            return ImportResult(
                job_id="",
                status=ImportStatus.COMPLETED,
                total_records=1,
                processed_records=1,
                failed_records=0,
                created_resources={"documents": 1},
                summary=f"Extracted text from {file_path.name}",
            )
        except Exception as e:
            return ImportResult(
                job_id="",
                status=ImportStatus.FAILED,
                total_records=0,
                processed_records=0,
                failed_records=0,
                errors=[str(e)],
            )

    async def import_from_fhir(
        self,
        file_path: Path,
        tenant_id: str,
        patient_id: Optional[str] = None,
        config: Optional[FHIRImportConfig] = None,
    ) -> ImportResult:
        from uuid import UUID as _UUID

        try:
            tid = _UUID(tenant_id)
        except (ValueError, TypeError):
            return ImportResult(
                job_id="",
                status=ImportStatus.FAILED,
                total_records=0,
                processed_records=0,
                failed_records=0,
                errors=["Invalid tenant_id"],
            )
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("resourceType") != "Bundle":
            data = {
                "resourceType": "Bundle",
                "type": "collection",
                "entry": [{"resource": data}] if data.get("resourceType") else [],
            }
        if patient_id:
            for entry in data.get("entry", []):
                r = entry.get("resource") or {}
                if r.get("resourceType") in (
                    "Observation",
                    "DiagnosticReport",
                    "MedicationStatement",
                ):
                    r.setdefault("subject", {})["reference"] = f"Patient/{patient_id}"
        _brr = await self.restore_fhir_bundle(data, tid, validate=True, config=config)
        await self.db.commit()
        c, u, e, w = _brr.created, _brr.updated, _brr.errors, _brr.warnings
        total = sum(c.values()) + sum(u.values())
        return ImportResult(
            job_id="",
            status=ImportStatus.COMPLETED if not e else ImportStatus.PARTIAL,
            total_records=total,
            processed_records=total,
            failed_records=len(e),
            created_resources=c,
            updated_resources=u,
            errors=e,
            warnings=w,
        )
