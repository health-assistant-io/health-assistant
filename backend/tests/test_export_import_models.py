import uuid
from app.models.export_import_job import ExportJobModel, ImportJobModel
from app.models.enums import ExportScope, ExportType, JobStatus
from app.schemas.backup import (
    BackupRequest,
    BackupManifest,
    ManifestFile,
    RestoreResult,
    PROVENANCE_CODE,
    BACKUP_SCHEMA_VERSION,
    FHIR_VERSION,
)


def test_export_scope_enum_values():
    assert ExportScope.PATIENT.value == "patient"
    assert ExportScope.GROUP.value == "group"
    assert ExportScope.SYSTEM.value == "system"


def test_export_type_enum_values():
    assert ExportType.FHIR_ONLY.value == "fhir_only"
    assert ExportType.FULL_BACKUP.value == "full_backup"
    assert ExportType.CATALOG_ONLY.value == "catalog_only"


def test_job_status_enum_values():
    assert JobStatus.PENDING.value == "PENDING"
    assert JobStatus.PROCESSING.value == "PROCESSING"
    assert JobStatus.COMPLETED.value == "COMPLETED"
    assert JobStatus.FAILED.value == "FAILED"
    assert JobStatus.PARTIAL.value == "PARTIAL"


def test_export_job_model_instantiation_and_to_dict():
    job = ExportJobModel(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scope=ExportScope.PATIENT,
        export_type=ExportType.FULL_BACKUP,
        status=JobStatus.PENDING,
        progress=0,
        patient_ids=["abc"],
    )
    d = job.to_dict()
    assert d["scope"] == "patient"
    assert d["export_type"] == "full_backup"
    assert d["status"] == "PENDING"
    assert d["patient_ids"] == ["abc"]
    assert "id" in d and "tenant_id" in d and "user_id" in d


def test_import_job_model_instantiation_and_to_dict():
    job = ImportJobModel(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        source_filename="backup.zip",
        status=JobStatus.PROCESSING,
        progress=42,
        total_records=100,
        processed_records=40,
        failed_records=2,
    )
    d = job.to_dict()
    assert d["source_filename"] == "backup.zip"
    assert d["status"] == "PROCESSING"
    assert d["progress"] == 42
    assert d["total_records"] == 100
    assert d["processed_records"] == 40
    assert d["failed_records"] == 2


def test_backup_request_defaults():
    req = BackupRequest()
    assert req.scope == ExportScope.PATIENT
    assert req.export_type == ExportType.FHIR_ONLY
    assert req.include_documents is True
    assert req.include_telemetry is True
    assert req.include_integrations is True
    assert req.include_ai_config is False
    assert req.patient_ids is None


def test_backup_request_full_backup_system():
    req = BackupRequest(scope=ExportScope.SYSTEM, export_type=ExportType.FULL_BACKUP)
    assert req.scope == ExportScope.SYSTEM
    assert req.export_type == ExportType.FULL_BACKUP


def test_backup_manifest_round_trip():
    import datetime as dt
    m = BackupManifest(
        exported_at=dt.datetime(2026, 6, 18, tzinfo=dt.timezone.utc),
        tenant_id=str(uuid.uuid4()),
        scope=ExportScope.PATIENT,
        export_type=ExportType.FULL_BACKUP,
        smart_scope="patient/*.rs",
        counts={"Patient": 1, "Observation": 5},
        files=[ManifestFile(path="fhir/bundle.json", sha256="abc", size=1234)],
        options={"include_telemetry": True},
    )
    dumped = m.model_dump_json()
    m2 = BackupManifest.model_validate_json(dumped)
    assert m2.schema_version == BACKUP_SCHEMA_VERSION
    assert m2.fhir_version == FHIR_VERSION
    assert m2.smart_scope == "patient/*.rs"
    assert m2.counts == {"Patient": 1, "Observation": 5}
    assert m2.files[0].sha256 == "abc"
    assert m2.scope == ExportScope.PATIENT


def test_restore_result_defaults():
    r = RestoreResult(job_id="x", status=JobStatus.COMPLETED)
    assert r.created_resources == {}
    assert r.errors == []
    assert r.warnings == []
    assert r.manifest_verified is False
    assert r.fhir_validated is False


def test_provenance_constants():
    assert PROVENANCE_CODE == "ha-export"
    assert FHIR_VERSION == "4.0.1"
    assert BACKUP_SCHEMA_VERSION == "1.0.0"
