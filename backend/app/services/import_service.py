import hashlib
import json
import logging
import os
import zipfile
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.clinical_event import (
    ClinicalEvent,
    ClinicalEventCategory,
    ClinicalEventType,
)
from app.models.document_model import DocumentModel
from app.models.enums import (
    AllergyCategory,
    AllergyClinicalStatus,
    AllergyCriticality,
    Gender,
    JobStatus,
    MedicationStatus,
)
from app.models.examination_model import ExaminationModel
from app.models.export_import_job import ImportJobModel
from app.models.fhir.allergy import AllergyCatalog, AllergyIntolerance
from app.models.fhir.medication import Medication, MedicationCatalog
from app.models.fhir.organization import OrganizationModel
from app.models.fhir.patient import DiagnosticReport, Observation, Patient
from app.models.notification import NotificationTrigger
from app.models.telemetry_model import TelemetryDataModel
from app.models.user_integration import UserIntegration
from app.schemas.backup import BackupManifest, RestoreResult
from app.services.fhir_converter import (
    fhir_to_orm,
    validate_bundle,
)
from app.schemas.import_data import (
    CSVImportConfig,
    FHIRImportConfig,
    ImportResult,
    ImportStatus,
    OCRImportConfig,
)

logger = logging.getLogger(__name__)


def _uuid(v: Any) -> Optional[UUID]:
    if v is None:
        return None
    if isinstance(v, UUID):
        return v
    try:
        return UUID(str(v))
    except (ValueError, AttributeError):
        return None


def _parse_dt(v: Any) -> Optional[datetime]:
    if not v or isinstance(v, datetime):
        return v
    try:
        s = str(v)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _parse_date(v: Any) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date):
        return v if not isinstance(v, datetime) else v.date()
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError, AttributeError):
        return None


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
            sa_update(ImportJobModel).where(ImportJobModel.id == job_id).values(**values)
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
                    "status": status.value if status and hasattr(status, "value") else None,
                    "progress": min(progress, 99),
                    "message": message,
                }
                await publish_message(f"tenant:{tenant_id}:tasks", json.dumps(payload))
        except Exception:
            pass

    async def _complete_job(
        self, job_id: UUID, result: RestoreResult
    ) -> None:
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
                completed_at=datetime.now(timezone.utc).isoformat(),
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
                completed_at=datetime.now(timezone.utc).isoformat(),
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
    def verify_manifest_from_zip(zf: zipfile.ZipFile) -> Tuple[bool, Optional[BackupManifest], List[str]]:
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
    ) -> Tuple[Dict[str, int], Dict[str, int], List[str], List[str], Dict[str, str]]:
        created: Dict[str, int] = {}
        updated: Dict[str, int] = {}
        errors: List[str] = []
        warnings: List[str] = []
        id_remap: Dict[str, str] = {}
        imported_obs_ids: List[UUID] = []

        if validate:
            ok, verrors = validate_bundle(bundle)
            if not ok:
                errors.extend(verrors)
                return created, updated, errors, warnings, id_remap

        entries = bundle.get("entry", [])
        if bundle.get("resourceType") == "Bundle" and not entries:
            return created, updated, errors, warnings, id_remap

        if bundle.get("resourceType") != "Bundle":
            entries = [{"resource": bundle}]

        for entry in entries:
            resource = entry.get("resource") or {}
            rt = resource.get("resourceType")
            if not rt:
                errors.append("Entry missing resource.resourceType; skipped")
                continue
                
            if rt == "DocumentReference":
                # We skip DocumentReference because Health Assistant exports it for FHIR
                # completeness, but actually restores documents via the nonfhir/documents.json sidecar.
                warnings.append(f"Skipped {rt} (handled via documents.json sidecar if present).")
                continue

            # Per-resource validation happens inside fhir_to_orm() (via
            # fhir.resources); an invalid resource raises FhirSerializationError
            # which is caught below → skipped + logged (skip-and-log policy).
            try:
                stats_delta, obs_id = await self._restore_one_fhir_resource(
                    rt, resource, tenant_id, id_remap
                )
                if stats_delta == "created":
                    created[rt] = created.get(rt, 0) + 1
                elif stats_delta == "updated":
                    updated[rt] = updated.get(rt, 0) + 1
                elif stats_delta == "skipped":
                    w = f"Skipped unsupported FHIR resource type: {rt}"
                    if w not in warnings:
                        warnings.append(w)
                if obs_id and rt == "Observation":
                    imported_obs_ids.append(obs_id)
            except Exception as e:
                logger.exception(f"Failed to restore {rt}")
                errors.append(f"{rt}: {e}")

        # Deduplicate DocumentReference warnings
        doc_ref_warning = "Skipped DocumentReference (handled via documents.json sidecar if present)."
        doc_ref_count = warnings.count(doc_ref_warning)
        if doc_ref_count > 1:
            warnings = [w for w in warnings if w != doc_ref_warning]
            warnings.append(doc_ref_warning)

        # Run biomarker mapping for newly imported observations
        if imported_obs_ids:
            if self._job_id:
                await self._update_progress(
                    self._job_id, 52, message=f"Mapping {len(imported_obs_ids)} biomarker observation(s)"
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
                    auto_map = getattr(config, 'auto_map_biomarkers', True) if config else True
                    use_ai = getattr(config, 'use_ai_normalization', False) if config else False
                    
                    if auto_map:
                        # map_observations_to_biomarkers does basic string/code mapping
                        # If use_ai is enabled, DO NOT auto-create missing entries yet so the AI can handle them
                        await map_observations_to_biomarkers(self.db, obs_to_map, auto_create_missing=not use_ai)
                        
                        if use_ai:
                            unmapped = [o for o in obs_to_map if not o.biomarker_id]
                            if unmapped:
                                from app.services.medical_processing_service import MedicalProcessingService
                                from app.services.ai_provider_service import AIProviderService
                                from app.schemas.ai_nlp import UnknownBiomarkerExtract

                                logger.info(
                                    "AI normalization: resolving %d unmapped observation(s) via NLP task",
                                    len(unmapped),
                                )
                                if self._job_id:
                                    await self._update_progress(
                                        self._job_id, 55, message="AI normalization: generating biomarker definitions"
                                    )

                                ai_service = AIProviderService(self.db)
                                nlp_extractor = await ai_service.get_nlp_extractor(tenant_id)
                                med_service = MedicalProcessingService(self.db)

                                unknown_bios: List[Any] = []
                                seen_names: set = set()
                                for o in unmapped:
                                    text = o.code.get("text") or next(
                                        (c.get("display") or c.get("code") for c in o.code.get("coding", [])),
                                        "Unknown",
                                    )
                                    name_key = text.lower().strip()
                                    if not name_key or name_key == "unknown" or name_key in seen_names:
                                        continue
                                    seen_names.add(name_key)
                                    try:
                                        value = float(
                                            o.raw_value
                                            or (o.value_quantity.get("value") if o.value_quantity else 0)
                                        )
                                    except (TypeError, ValueError):
                                        value = 0.0
                                    unit_symbol = o.value_quantity.get("unit") if o.value_quantity else None
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

                                    await map_observations_to_biomarkers(self.db, unmapped)

                    # Telemetry fan-out for newly mapped observations
                    from app.models.biomarker_model import BiomarkerDefinition
                    from app.models.telemetry_model import TelemetryDataModel
                    
                    # Ensure we have the biomarkers loaded
                    mapped_obs = [o for o in obs_to_map if o.biomarker_id]
                    if mapped_obs:
                        b_ids = {o.biomarker_id for o in mapped_obs}
                        b_res = await self.db.execute(
                            select(BiomarkerDefinition).where(BiomarkerDefinition.id.in_(b_ids))
                        )
                        b_dict = {b.id: b for b in b_res.scalars().all()}
                        
                        telemetry_records = []
                        for o in mapped_obs:
                            b_def = b_dict.get(o.biomarker_id)
                            if b_def and b_def.is_telemetry:
                                slug = b_def.slug.lower() if b_def.slug else ""
                                val = getattr(o, "normalized_value", None) or getattr(o, "raw_value", None) or (o.value_quantity.get("value") if o.value_quantity else None)
                                if val is not None:
                                    hr = val if slug == "8867-4" or "heart-rate" in slug else None
                                    steps = val if slug == "41950-7" or "steps" in slug else None
                                    cal = val if "calories" in slug else None
                                    
                                    data_payload = {}
                                    if not hr and not steps and not cal:
                                        data_payload[slug] = val
                                        if getattr(o, "value_quantity", None):
                                            data_payload[f"{slug}_unit"] = o.value_quantity.get("unit", "")

                                    telemetry_records.append(TelemetryDataModel(
                                        tenant_id=o.tenant_id,
                                        device_id="fhir_import",
                                        timestamp=o.effective_datetime,
                                        heart_rate=hr,
                                        steps=steps,
                                        calories=cal,
                                        data=data_payload if data_payload else None
                                    ))
                        
                        if telemetry_records:
                            self.db.add_all(telemetry_records)
                            await self.db.flush()

            except Exception as e:
                logger.exception("Failed to map biomarkers for imported observations")
                warnings.append(f"Failed to map biomarkers for imported observations: {e}")

        return created, updated, list(set(errors)), warnings, id_remap

    async def _restore_one_fhir_resource(
        self,
        rt: str,
        fhir_dict: Dict[str, Any],
        tenant_id: UUID,
        id_remap: Dict[str, str],
    ) -> Tuple[str, Optional[UUID]]:
        old_id = fhir_dict.get("id")
        old_id_str = str(old_id) if old_id else None

        remapped = self._apply_remap(fhir_dict, id_remap)
        
        # Check if we actually support this resource type before attempting to convert it
        # This prevents the ValueError from fhir_converter.py from bubbling up as an error
        from app.services.fhir_converter import _TO_ORM
        if rt not in _TO_ORM:
            logger.warning(f"Unsupported resource type {rt}")
            return "skipped", None
            
        orm_dict = fhir_to_orm(rt, remapped)
        orm_dict["tenant_id"] = tenant_id

        if rt == "Patient":
            return await self._upsert_patient(orm_dict, old_id_str, tenant_id, id_remap), None
        if rt == "Observation":
            return await self._upsert_observation(orm_dict, old_id_str, tenant_id, id_remap)
        if rt == "MedicationStatement":
            return await self._upsert_medication(orm_dict, old_id_str, tenant_id, id_remap), None
        if rt == "AllergyIntolerance":
            return await self._upsert_allergy(orm_dict, old_id_str, tenant_id, id_remap), None
        if rt == "DiagnosticReport":
            return await self._upsert_diagnostic_report(orm_dict, old_id_str, tenant_id, id_remap), None
        if rt == "Organization":
            return await self._upsert_organization(orm_dict, old_id_str, tenant_id, id_remap), None
        if rt == "Practitioner":
            return await self._upsert_practitioner(orm_dict, old_id_str, tenant_id, id_remap), None
            
        warnings = f"Unsupported resource type {rt}"
        logger.warning(warnings)
        return "skipped", None

    @staticmethod
    def _apply_remap(fhir_dict: Dict[str, Any], id_remap: Dict[str, str]) -> Dict[str, Any]:
        if not id_remap:
            return dict(fhir_dict)
        d = json.loads(json.dumps(fhir_dict, default=str))

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
                            prefix = "Patient"
                            if field_hint == "performer":
                                prefix = "Practitioner"
                            elif field_hint == "partOf":
                                prefix = "Organization"
                            return {"reference": f"{prefix}/{id_remap[rid]}"}
                return {k: _remap_ref(v, field_hint) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_remap_ref(x, field_hint) for x in obj]
            return obj

        for field in ("subject", "patient", "partOf", "performer", "context"):
            if field in d:
                d[field] = _remap_ref(d[field], field)
        return d

    async def _resolve_id(
        self, model, old_id_str: Optional[str], tenant_id: UUID
    ) -> Tuple[Optional[UUID], UUID, str]:
        if old_id_str:
            old_uuid = _uuid(old_id_str)
            if old_uuid:
                res = await self.db.execute(
                    select(model).where(model.id == old_uuid)
                )
                existing = res.scalar_one_or_none()
                if existing:
                    if existing.tenant_id == tenant_id:
                        return old_uuid, old_uuid, "updated"
                    new_id = uuid4()
                    return None, new_id, "created"
        return None, uuid4(), "created"

    async def _upsert_patient(
        self, orm: Dict[str, Any], old_id_str: Optional[str], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(Patient, old_id_str, tenant_id)
        mrn = (orm.get("mrn") or "").strip() or None
        if existing_id:
            await self.db.execute(
                sa_update(Patient).where(Patient.id == existing_id).values(
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
        self, orm: Dict[str, Any], old_id_str: Optional[str], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> Tuple[str, Optional[UUID]]:
        existing_id, new_id, action = await self._resolve_id(Observation, old_id_str, tenant_id)
        
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
                        Observation.effective_datetime == effective_dt
                    )
                )
                res = await self.db.execute(stmt)
                for existing_obs in res.scalars().all():
                    existing_code = existing_obs.code.get("text")
                    if existing_code == code_text:
                        # Found a match, merge instead of create
                        existing_id = existing_obs.id
                        action = "updated"
                        break

        if existing_id:
            await self.db.execute(
                sa_update(Observation).where(Observation.id == existing_id).values(
                    status=orm.get("status") or "final",
                    code=orm.get("code"),
                    subject=orm.get("subject"),
                    effective_datetime=_parse_dt(orm.get("effective_datetime")),
                    value_quantity=orm.get("value_quantity"),
                    value_string=orm.get("value_string"),
                    reference_range=orm.get("reference_range"),
                    performer=orm.get("performer"),
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
            effective_datetime=_parse_dt(orm.get("effective_datetime")),
            value_quantity=orm.get("value_quantity"),
            value_string=orm.get("value_string"),
            reference_range=orm.get("reference_range"),
            performer=orm.get("performer"),
        )
        self.db.add(obs)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created", new_id

    async def _upsert_medication(
        self, orm: Dict[str, Any], old_id_str: Optional[str], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(Medication, old_id_str, tenant_id)
        patient_id = _uuid(orm.get("patient_id"))
        try:
            status = MedicationStatus(orm.get("status", "ACTIVE").upper())
        except ValueError:
            status = MedicationStatus.ACTIVE
        if existing_id:
            await self.db.execute(
                sa_update(Medication).where(Medication.id == existing_id).values(
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
        self, orm: Dict[str, Any], old_id_str: Optional[str], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(AllergyIntolerance, old_id_str, tenant_id)
        patient_id = _uuid(orm.get("patient_id"))
        try:
            clinical = AllergyClinicalStatus(orm.get("clinical_status", "ACTIVE").upper())
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
                sa_update(AllergyIntolerance).where(AllergyIntolerance.id == existing_id).values(
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
        self, orm: Dict[str, Any], old_id_str: Optional[str], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(DiagnosticReport, old_id_str, tenant_id)
        if existing_id:
            await self.db.execute(
                sa_update(DiagnosticReport).where(DiagnosticReport.id == existing_id).values(
                    status=orm.get("status") or "final",
                    code=orm.get("code"),
                    subject=orm.get("subject"),
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
        self, orm: Dict[str, Any], old_id_str: Optional[str], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(OrganizationModel, old_id_str, tenant_id)
        if existing_id:
            await self.db.execute(
                sa_update(OrganizationModel).where(OrganizationModel.id == existing_id).values(
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
        self, orm: Dict[str, Any], old_id_str: Optional[str], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> str:
        from app.models.doctor_model import DoctorModel

        existing_id, new_id, action = await self._resolve_id(DoctorModel, old_id_str, tenant_id)
        if existing_id:
            await self.db.execute(
                sa_update(DoctorModel).where(DoctorModel.id == existing_id).values(
                    name=orm.get("name") or "Imported",
                    specialty=orm.get("specialty"),
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
            specialty=orm.get("specialty"),
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
            created["integrations"], w = await self._restore_integrations(payload, tenant_id, id_remap)
            warnings.extend(w)
        elif name == "notification_triggers.json":
            created["notification_triggers"] = await self._restore_triggers(payload, tenant_id, id_remap)
        elif name == "examinations.json":
            created["examinations"] = await self._restore_examinations(payload, tenant_id, id_remap)
        elif name == "clinical_events.json":
            created["clinical_events"] = await self._restore_clinical_events(payload, tenant_id, id_remap)
        elif name == "biomarker_definitions.json":
            created["biomarker_definitions"] = await self._restore_biomarker_catalog(payload, tenant_id)
        elif name == "medication_catalog.json":
            created["medication_catalog"] = await self._restore_medication_catalog(payload, tenant_id)
        elif name == "allergy_catalog.json":
            created["allergy_catalog"] = await self._restore_allergy_catalog(payload, tenant_id)
        elif name == "clinical_event_types.json":
            created["clinical_event_types"] = await self._restore_clinical_event_types(payload, tenant_id)
        elif name == "ai_config.json":
            warnings.append("AI config restore is not supported in v1 (export-only).")
        elif name == "documents.json":
            pass
        else:
            warnings.append(f"Unknown sidecar {name}; skipped")
        return created, errors, warnings

    async def _restore_telemetry(self, payload: List[Dict[str, Any]], tenant_id: UUID) -> int:
        count = 0
        for item in payload:
            try:
                row = TelemetryDataModel(
                    tenant_id=tenant_id,
                    device_id=item.get("device_id") or "imported",
                    timestamp=_parse_dt(item.get("timestamp")) or datetime.now(timezone.utc),
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
                        sa_update(UserIntegration).where(UserIntegration.id == existing.id).values(
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
                        status_enum = IntegrationStatus(status_val) if status_val else IntegrationStatus.PENDING
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
                exam = ExaminationModel(
                    tenant_id=tenant_id,
                    patient_id=_uuid(pid),
                    examination_date=_parse_date(item.get("examination_date")),
                    notes=item.get("notes"),
                    patient_notes=item.get("patient_notes"),
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
        count = 0
        for c in payload.get("categories", []):
            try:
                res = await self.db.execute(
                    select(ClinicalEventCategory).where(
                        ClinicalEventCategory.slug == c.get("slug")
                    )
                )
                if not res.scalar_one_or_none():
                    self.db.add(
                        ClinicalEventCategory(
                            tenant_id=tenant_id,
                            name=c.get("name") or c.get("slug"),
                            slug=c.get("slug"),
                            description=c.get("description"),
                            icon=c.get("icon"),
                            color=c.get("color"),
                        )
                    )
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
                        MedicationCatalog.name.ilike(name)
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
                        AllergyCatalog.name.ilike(name)
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
        from app.services.document_service_db import UPLOAD_DIR as RESOLVED_UPLOAD_DIR

        tenant_dir = Path(str(RESOLVED_UPLOAD_DIR)) / str(tenant_id)
        tenant_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for meta in documents_meta:
            try:
                pid = meta.get("patient_id")
                if pid and str(pid) in id_remap:
                    pid = id_remap[str(pid)]
                archive_path = meta.get("_archive_path")
                target_name = f"{uuid4()}{os.path.splitext(meta.get('filename',''))[1] or '.bin'}"
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
                    include_in_extraction=bool(meta.get("include_in_extraction", False)),
                    is_edited=bool(meta.get("is_edited", False)),
                )
                self.db.add(doc)
                count += 1
            except Exception as e:
                logger.warning(f"document skipped: {e}")
        await self.db.flush()
        return count

    # ---------------- orchestrator ----------------

    async def run_import(self, job_id: UUID, archive_path: str, owner_id: UUID, config: Optional[FHIRImportConfig] = None) -> RestoreResult:
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
                created, updated, errors, warnings, manifest_verified, fhir_validated = (
                    await self._restore_from_zip(archive_path, tenant_id, owner_id, job_id, config)
                )
            else:
                created, updated, errors, warnings, manifest_verified, fhir_validated = (
                    await self._restore_from_bare_json(archive_path, tenant_id, job_id, config)
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
        self, archive_path: str, tenant_id: UUID, owner_id: UUID, job_id: UUID, config: Optional[FHIRImportConfig] = None
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
                warnings.append("Manifest verification failed; continuing with best-effort restore.")

            await self._update_progress(job_id, 20)

            try:
                bundle_bytes = zf.read("fhir/bundle.json")
                bundle = json.loads(bundle_bytes)
            except KeyError:
                bundle = None
                warnings.append("No fhir/bundle.json in archive (catalog/sidecar-only backup).")

            id_remap: Dict[str, str] = {}
            if bundle:
                ok, verrors = validate_bundle(bundle)
                fhir_validated = ok
                if not ok:
                    errors.extend(verrors)
                c, u, e, w, id_remap = await self.restore_fhir_bundle(
                    bundle, tenant_id, validate=False, config=config
                )
                created.update(c)
                updated.update(u)
                errors.extend(e)
                warnings.extend(w)
                await self._update_progress(job_id, 50)

            for name in [
                "biomarker_definitions.json",
                "clinical_event_types.json",
                "examinations.json",
                "clinical_events.json",
                "notification_triggers.json",
                "telemetry.json",
                "integrations.json",
                "ai_config.json",
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
                n = await self.restore_documents(documents_meta, zf, tenant_id, id_remap, owner_id)
                created["documents"] = n
            except KeyError:
                pass

        await self.db.commit()
        return created, updated, errors, warnings, manifest_verified, fhir_validated

    async def _restore_from_bare_json(
        self, path: str, tenant_id: UUID, job_id: UUID, config: Optional[FHIRImportConfig] = None
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
            c, u, e, w, _ = await self.restore_fhir_bundle(data, tenant_id, validate=False, config=config)
            created.update(c)
            updated.update(u)
            errors.extend(e)
            warnings.extend(w)
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
            from app.processors.ocr import get_ocr_processor

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
                if r.get("resourceType") in ("Observation", "DiagnosticReport", "MedicationStatement"):
                    r.setdefault("subject", {})["reference"] = f"Patient/{patient_id}"
        c, u, e, w, _ = await self.restore_fhir_bundle(data, tid, validate=True, config=config)
        await self.db.commit()
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
