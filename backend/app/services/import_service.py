import hashlib
import json
import logging
import os
import zipfile
from datetime import datetime, timezone
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
from app.models.fhir.allergy import AllergyIntolerance
from app.models.fhir.medication import Medication
from app.models.fhir.organization import OrganizationModel
from app.models.fhir.patient import DiagnosticReport, Observation, Patient
from app.models.notification import NotificationTrigger
from app.models.telemetry_model import TelemetryDataModel
from app.models.user_integration import UserIntegration
from app.schemas.backup import BackupManifest, RestoreResult
from app.services.fhir_converter import (
    fhir_to_orm,
    validate_bundle,
    validate_resource,
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


def _parse_date(v: Any):
    if not v:
        return None
    try:
        return str(v)[:10]
    except Exception:
        return None


class ImportService:
    def __init__(self, db: AsyncSession):
        self.db = db

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
        self, job_id: UUID, progress: int, status: Optional[JobStatus] = None
    ) -> None:
        values: Dict[str, Any] = {"progress": progress}
        if status:
            values["status"] = status
        await self.db.execute(
            sa_update(ImportJobModel).where(ImportJobModel.id == job_id).values(**values)
        )
        await self.db.commit()

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
    ) -> Tuple[Dict[str, int], Dict[str, int], List[str], List[str], Dict[str, str]]:
        created: Dict[str, int] = {}
        updated: Dict[str, int] = {}
        errors: List[str] = []
        warnings: List[str] = []
        id_remap: Dict[str, str] = {}

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

            if validate:
                ok, verrors = validate_resource(resource)
                if not ok:
                    errors.append(f"Invalid {rt}: {verrors[0][:200]}")
                    continue

            try:
                stats_delta = await self._restore_one_fhir_resource(
                    rt, resource, tenant_id, id_remap
                )
                if stats_delta == "created":
                    created[rt] = created.get(rt, 0) + 1
                elif stats_delta == "updated":
                    updated[rt] = updated.get(rt, 0) + 1
            except Exception as e:
                logger.exception(f"Failed to restore {rt}")
                errors.append(f"{rt}: {e}")

        return created, updated, errors, warnings, id_remap

    async def _restore_one_fhir_resource(
        self,
        rt: str,
        fhir_dict: Dict[str, Any],
        tenant_id: UUID,
        id_remap: Dict[str, str],
    ) -> str:
        old_id = fhir_dict.get("id")
        old_id_str = str(old_id) if old_id else None

        remapped = self._apply_remap(fhir_dict, id_remap)
        orm_dict = fhir_to_orm(rt, remapped)
        orm_dict["tenant_id"] = tenant_id

        if rt == "Patient":
            return await self._upsert_patient(orm_dict, old_id_str, tenant_id, id_remap)
        if rt == "Observation":
            return await self._upsert_observation(orm_dict, old_id_str, tenant_id, id_remap)
        if rt == "MedicationStatement":
            return await self._upsert_medication(orm_dict, old_id_str, tenant_id, id_remap)
        if rt == "AllergyIntolerance":
            return await self._upsert_allergy(orm_dict, old_id_str, tenant_id, id_remap)
        if rt == "DiagnosticReport":
            return await self._upsert_diagnostic_report(orm_dict, old_id_str, tenant_id, id_remap)
        if rt == "Organization":
            return await self._upsert_organization(orm_dict, old_id_str, tenant_id, id_remap)
        if rt == "Practitioner":
            return await self._upsert_practitioner(orm_dict, old_id_str, tenant_id, id_remap)
        warnings = f"Unsupported resource type {rt}"
        logger.warning(warnings)
        raise ValueError(warnings)

    @staticmethod
    def _apply_remap(fhir_dict: Dict[str, Any], id_remap: Dict[str, str]) -> Dict[str, Any]:
        if not id_remap:
            return dict(fhir_dict)
        d = json.loads(json.dumps(fhir_dict, default=str))

        def _remap_ref(obj: Any) -> Any:
            if isinstance(obj, dict):
                ref = obj.get("reference")
                if isinstance(ref, str) and "/" in ref:
                    prefix, rid = ref.split("/", 1)
                    if rid in id_remap:
                        return {"reference": f"{prefix}/{id_remap[rid]}"}
                return {k: _remap_ref(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_remap_ref(x) for x in obj]
            return obj

        for field in ("subject", "patient", "partOf", "performer", "context"):
            if field in d:
                d[field] = _remap_ref(d[field])
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
                    mrn=orm.get("mrn"),
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
            mrn=orm.get("mrn"),
        )
        self.db.add(patient)
        await self.db.flush()
        if old_id_str:
            id_remap[old_id_str] = str(new_id)
        return "created"

    async def _upsert_observation(
        self, orm: Dict[str, Any], old_id_str: Optional[str], tenant_id: UUID, id_remap: Dict[str, str]
    ) -> str:
        existing_id, new_id, action = await self._resolve_id(Observation, old_id_str, tenant_id)
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
            return "updated"
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
        return "created"

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

    async def run_import(self, job_id: UUID, archive_path: str, owner_id: UUID) -> RestoreResult:
        job = await self.get_job(job_id)
        if not job:
            raise ValueError(f"Import job {job_id} not found")
        tenant_id = job.tenant_id
        if not tenant_id:
            raise ValueError("Import job has no tenant_id")

        result = RestoreResult(job_id=str(job_id), status=JobStatus.PROCESSING)
        try:
            await self._update_progress(job_id, 5, JobStatus.PROCESSING)

            if zipfile.is_zipfile(archive_path):
                created, updated, errors, warnings, manifest_verified, fhir_validated = (
                    await self._restore_from_zip(archive_path, tenant_id, owner_id, job_id)
                )
            else:
                created, updated, errors, warnings, manifest_verified, fhir_validated = (
                    await self._restore_from_bare_json(archive_path, tenant_id, job_id)
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
        self, archive_path: str, tenant_id: UUID, owner_id: UUID, job_id: UUID
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
                    bundle, tenant_id, validate=False
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
        self, path: str, tenant_id: UUID, job_id: UUID
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
            c, u, e, w, _ = await self.restore_fhir_bundle(data, tenant_id, validate=False)
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
        c, u, e, w, _ = await self.restore_fhir_bundle(data, tid, validate=True)
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
