import json
import os
import uuid
import zipfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.enums import ExportScope, ExportType, JobStatus
from app.models.export_import_job import ExportJobModel
from app.services.export_service import ExportService, _patient_filter_conditions


def _make_patient(pid, tenant_id):
    p = MagicMock()
    p.id = pid
    p.to_dict.return_value = {
        "id": str(pid),
        "tenant_id": str(tenant_id),
        "name": [{"family": "Doe", "given": ["J"]}],
        "gender": "FEMALE",
        "birth_date": "1990-01-01",
        "mrn": "MRN1",
    }
    p.to_fhir_dict.return_value = {
        "resourceType": "Patient",
        "id": str(pid),
        "name": [{"family": "Doe", "given": ["J"]}],
        "gender": "female",
        "birthDate": "1990-01-01",
        "meta": {"versionId": "1"},
    }
    return p


def _make_observation(oid, tenant_id, pid):
    o = MagicMock()
    o.id = oid
    o.to_dict.return_value = {
        "id": str(oid),
        "tenant_id": str(tenant_id),
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}], "text": "HR"},
        "subject": {"reference": f"Patient/{pid}"},
        "effective_datetime": "2026-06-18T10:00:00+00:00",
        "value_quantity": {"value": 72, "unit": "bpm"},
    }
    o.to_fhir_dict.return_value = {
        "resourceType": "Observation",
        "id": str(oid),
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}], "text": "HR"},
        "subject": {"reference": f"Patient/{pid}"},
        "effectiveDateTime": "2026-06-18T10:00:00+00:00",
        "valueQuantity": {"value": 72, "unit": "bpm"},
        "meta": {"versionId": "1"},
    }
    return o


def _make_medication(mid, tenant_id, pid):
    m = MagicMock()
    m.id = mid
    m.to_dict.return_value = {
        "id": str(mid),
        "patient_id": str(pid),
        "status": "ACTIVE",
        "code": {"text": "Aspirin"},
        "start_date": "2026-01-01",
        "dosage": "100mg",
    }
    m.to_fhir_dict.return_value = {
        "resourceType": "MedicationStatement",
        "id": str(mid),
        "status": "active",
        "medicationCodeableConcept": {"text": "Aspirin"},
        "subject": {"reference": f"Patient/{pid}"},
        "effectivePeriod": {"start": "2026-01-01"},
        "dosage": [{"text": "100mg"}],
        "meta": {"versionId": "1"},
    }
    return m


def _make_doc(did, tenant_id, pid, tmp_path, filename="report.pdf"):
    d = MagicMock()
    d.id = did
    d.filename = filename
    d.file_path = str(tmp_path / filename)
    d.status = "completed"
    d.to_dict.return_value = {
        "id": str(did),
        "filename": filename,
        "file_path": str(tmp_path / filename),
        "tenant_id": str(tenant_id),
        "patient_id": str(pid),
        "status": "completed",
    }
    (tmp_path / filename).write_bytes(b"PDFCONTENT")
    return d


# ---------- helpers ----------

def test_patient_filter_conditions_returns_in_clause():
    import uuid as _u
    from app.models.fhir.patient import Patient

    cond = _patient_filter_conditions(Patient, [str(_u.uuid4()), str(_u.uuid4())], "id")
    assert cond is not None


def test_patient_filter_conditions_empty_returns_none():
    from app.models.fhir.patient import Patient

    assert _patient_filter_conditions(Patient, [], "id") is None


# ---------- pure build methods ----------

def test_build_fhir_bundle_counts_and_validates():
    pid = uuid.uuid4()
    tid = uuid.uuid4()
    svc = ExportService.__new__(ExportService)
    bundle, counts = svc.build_fhir_bundle(
        tid,
        [str(pid)],
        [_make_patient(pid, tid)],
        [_make_observation(uuid.uuid4(), tid, pid)],
        [_make_medication(uuid.uuid4(), tid, pid)],
        [],
        [],
        [],
        [],
        documents=[],
    )
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "transaction"
    assert counts["Patient"] == 1
    assert counts["Observation"] == 1
    assert counts["MedicationStatement"] == 1
    assert len(bundle["entry"]) == 3
    rts = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert set(rts) == {"Patient", "Observation", "MedicationStatement"}


def test_build_fhir_bundle_fails_loud_on_invalid_resource():
    """Fail-loud policy: a resource whose to_fhir_dict() raises
    FhirSerializationError fails the whole export (raises ExportError naming the
    bad resource) — backups must never silently drop data."""
    from app.services.export_service import ExportError
    from app.services.fhir_helpers import FhirSerializationError

    tid = uuid.uuid4()
    pid = uuid.uuid4()
    good_patient = _make_patient(pid, tid)

    bad_observation = MagicMock()
    bad_observation.id = uuid.uuid4()
    # Simulate a resource that cannot be serialized to valid FHIR
    bad_observation.to_fhir_dict.side_effect = FhirSerializationError("boom")

    good_med = _make_medication(uuid.uuid4(), tid, pid)

    svc = ExportService.__new__(ExportService)
    with pytest.raises(ExportError) as exc_info:
        svc.build_fhir_bundle(
            tid,
            [str(pid)],
            [good_patient],
            [bad_observation],
            [good_med],
            [],
            [],
            [],
            [],
            documents=[],
        )

    # The error report names the failing resource and the underlying cause
    msg = str(exc_info.value)
    assert "Observation" in msg
    assert "boom" in msg
    assert "failed FHIR validation" in msg


def test_build_nonfhir_sidecars_patient_scope_excludes_telemetry_with_note():
    tid = uuid.uuid4()
    svc = ExportService.__new__(ExportService)
    sidecars, counts, notes = svc.build_nonfhir_sidecars(
        tid,
        None,
        ExportScope.PATIENT,
        {"include_documents": True, "include_telemetry": True, "include_integrations": True, "include_ai_config": False},
        examinations=[],
        clinical_events=[],
        clinical_event_types={"types": [], "categories": []},
        biomarker_catalog={"units": [], "biomarkers": []},
        medication_catalog={"medications": []},
        allergy_catalog={"allergies": []},
        documents=[],
        telemetry=None,
        integrations=[],
        notification_triggers=[],
        ai_config=None,
    )
    assert "telemetry.json" not in sidecars
    assert any("Telemetry excluded" in n for n in notes)
    assert "examinations.json" in sidecars
    assert "documents.json" in sidecars
    assert "medication_catalog.json" in sidecars
    assert "allergy_catalog.json" in sidecars


def test_build_nonfhir_sidecars_system_scope_includes_telemetry():
    tid = uuid.uuid4()
    t = MagicMock()
    t.to_dict.return_value = {"id": "x", "heart_rate": 72}
    svc = ExportService.__new__(ExportService)
    sidecars, counts, notes = svc.build_nonfhir_sidecars(
        tid,
        None,
        ExportScope.SYSTEM,
        {"include_documents": True, "include_telemetry": True, "include_integrations": True, "include_ai_config": False},
        examinations=[],
        clinical_events=[],
        clinical_event_types={"types": [], "categories": []},
        biomarker_catalog={"units": [], "biomarkers": []},
        medication_catalog={"medications": [{"name": "Metformin"}, {"name": "Aspirin"}]},
        allergy_catalog={"allergies": [{"name": "Peanuts"}]},
        documents=[],
        telemetry=[t],
        integrations=[],
        notification_triggers=[],
        ai_config=None,
    )
    assert "telemetry.json" in sidecars
    assert counts["telemetry"] == 1
    assert notes == []
    assert counts["medication_catalog"] == 2
    assert counts["allergy_catalog"] == 1
    assert sidecars["medication_catalog.json"]["medications"][0]["name"] == "Metformin"
    assert sidecars["allergy_catalog.json"]["allergies"][0]["name"] == "Peanuts"


def test_integration_to_export_dict_keeps_user_config_and_tokens():
    tid = uuid.uuid4()
    integ = MagicMock()
    integ.id = uuid.uuid4()
    integ.tenant_id = tid
    integ.user_id = uuid.uuid4()
    integ.patient_id = uuid.uuid4()
    integ.provider = "google_fit"
    integ.status = MagicMock()
    integ.status.value = "ACTIVE"
    integ.access_token = "tok"
    integ.refresh_token = "ref"
    integ.expires_at = datetime(2026, 6, 18, tzinfo=timezone.utc)
    integ.scopes = "scope1"
    integ.provider_account_id = "acc"
    integ.instance_name = "My Phone"
    integ.is_debug_enabled = False
    integ.last_synced_at = None
    integ.user_config = {"_encrypted": {"_encrypted": "abc"}, "foo": "bar"}
    svc = ExportService.__new__(ExportService)
    d = svc._integration_to_export_dict(integ)
    assert d["access_token"] == "tok"
    assert d["user_config"]["_encrypted"] == {"_encrypted": "abc"}
    assert d["provider"] == "google_fit"
    assert d["status"] == "ACTIVE"


# ---------- write methods ----------

def test_compute_sha256_matches_known_value():
    import tempfile
    import hashlib
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"hello")
        path = f.name
    expected = hashlib.sha256(b"hello").hexdigest()
    assert ExportService.compute_sha256(path) == expected
    os.unlink(path)


def test_write_fhir_only_file_writes_bundle_and_manifest(tmp_path, monkeypatch):
    tid = uuid.uuid4()
    jid = uuid.uuid4()
    svc = ExportService.__new__(ExportService)
    monkeypatch.setattr(
        "app.services.document_service_db.UPLOAD_DIR", str(tmp_path)
    )
    bundle = {"resourceType": "Bundle", "type": "transaction", "entry": []}
    manifest = svc.build_manifest(tid, ExportScope.PATIENT, ExportType.FHIR_ONLY, {})
    path, size, manifest = svc.write_fhir_only_file(bundle, tid, jid, manifest)
    assert os.path.exists(path)
    assert path.endswith(".fhir.json")
    loaded = json.loads(open(path).read())
    assert loaded["resourceType"] == "Bundle"
    assert size > 0
    assert len(manifest.files) == 1
    assert manifest.files[0].path == "fhir/bundle.json"


def test_write_catalog_file_writes_catalog(tmp_path, monkeypatch):
    tid = uuid.uuid4()
    jid = uuid.uuid4()
    svc = ExportService.__new__(ExportService)
    monkeypatch.setattr(
        "app.services.document_service_db.UPLOAD_DIR", str(tmp_path)
    )
    catalog = {
        "units": [{"symbol": "mg/dL", "name": "mg/dL"}],
        "biomarkers": [{"slug": "glucose", "name": "Glucose"}],
        "clinical_event_types": {"types": [{"slug": "pregnancy"}], "categories": []},
    }
    manifest = svc.build_manifest(tid, ExportScope.SYSTEM, ExportType.CATALOG_ONLY, {})
    path, size, manifest = svc.write_catalog_file(catalog, tid, jid, manifest)
    assert os.path.exists(path)
    assert path.endswith(".catalog.json")
    loaded = json.loads(open(path).read())
    assert loaded["biomarkers"][0]["slug"] == "glucose"
    assert manifest.counts["biomarkers"] == 1
    assert manifest.counts["units"] == 1


def test_write_full_backup_zip_creates_bagit_structure(tmp_path, monkeypatch):
    tid = uuid.uuid4()
    jid = uuid.uuid4()
    pid = uuid.uuid4()
    svc = ExportService.__new__(ExportService)
    monkeypatch.setattr(
        "app.services.document_service_db.UPLOAD_DIR", str(tmp_path)
    )
    bundle = {"resourceType": "Bundle", "type": "transaction", "entry": []}
    doc = _make_doc(uuid.uuid4(), tid, pid, tmp_path)
    sidecars = {
        "examinations.json": [],
        "documents.json": [{"id": str(doc.id), "_archive_path": f"documents/{doc.id}.pdf"}],
    }
    manifest = svc.build_manifest(tid, ExportScope.SYSTEM, ExportType.FULL_BACKUP, {})
    path, size, manifest = svc.write_full_backup_zip(
        bundle, sidecars, [doc], tid, jid, manifest
    )
    assert os.path.exists(path)
    assert path.endswith(".zip")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert "fhir/bundle.json" in names
        assert "nonfhir/examinations.json" in names
        assert "manifest.json" in names
        assert "manifest-sha256.txt" in names
        assert "bag-info.txt" in names
        assert any(n.startswith("documents/") for n in names)
        manifest_json = json.loads(zf.read("manifest.json"))
        assert manifest_json["schema_version"] == "1.0.0"
        assert manifest_json["smart_scope"] == "system/*.cruds"
        sha_txt = zf.read("manifest-sha256.txt").decode()
        assert "fhir/bundle.json" in sha_txt
        bag_info = zf.read("bag-info.txt").decode()
        assert str(tid) in bag_info
    assert size > 0
    assert len(manifest.files) >= 4


def test_manifest_sha256_matches_file_in_zip(tmp_path, monkeypatch):
    tid = uuid.uuid4()
    jid = uuid.uuid4()
    svc = ExportService.__new__(ExportService)
    monkeypatch.setattr(
        "app.services.document_service_db.UPLOAD_DIR", str(tmp_path)
    )
    bundle = {"resourceType": "Bundle", "type": "transaction", "entry": []}
    sidecars = {"x.json": [{"a": 1}]}
    manifest = svc.build_manifest(tid, ExportScope.PATIENT, ExportType.FULL_BACKUP, {})
    path, _, manifest = svc.write_full_backup_zip(bundle, sidecars, [], tid, jid, manifest)
    with zipfile.ZipFile(path) as zf:
        bundle_bytes = zf.read("fhir/bundle.json")
        import hashlib
        expected = hashlib.sha256(bundle_bytes).hexdigest()
        listed = [f for f in manifest.files if f.path == "fhir/bundle.json"][0]
        assert listed.sha256 == expected
        sha_txt = zf.read("manifest-sha256.txt").decode()
        assert expected in sha_txt


# ---------- run_export (orchestrator, patched gathers) ----------

@pytest.mark.asyncio
async def test_run_export_fhir_only_completes(monkeypatch, tmp_path):
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    jid = uuid.uuid4()
    pid = uuid.uuid4()
    monkeypatch.setattr(
        "app.services.document_service_db.UPLOAD_DIR", str(tmp_path)
    )
    job = ExportJobModel(
        id=jid, tenant_id=tid, user_id=uid,
        scope=ExportScope.PATIENT, export_type=ExportType.FHIR_ONLY,
        status=JobStatus.PENDING, progress=0, patient_ids=[str(pid)],
    )
    db = AsyncMock()
    svc = ExportService(db)

    async def fake_get_job(j, t=None):
        return job

    completed = {}

    async def fake_complete_job(j, file_path, file_size, counts, manifest=None):
        completed["path"] = file_path
        completed["size"] = file_size
        completed["counts"] = counts
        job.status = JobStatus.COMPLETED
        job.progress = 100

    async def fake_update(j, p, s=None):
        job.progress = p

    async def fake_fail(j, e):
        job.status = JobStatus.FAILED
        job.error_message = e

    monkeypatch.setattr(svc, "get_job", fake_get_job)
    monkeypatch.setattr(svc, "update_job_progress", fake_update)
    monkeypatch.setattr(svc, "complete_job", fake_complete_job)
    monkeypatch.setattr(svc, "fail_job", fake_fail)
    monkeypatch.setattr(svc, "gather_patients", AsyncMock(return_value=[_make_patient(pid, tid)]))
    monkeypatch.setattr(svc, "gather_observations", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_medications", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_allergies", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_diagnostic_reports", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_organizations", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_practitioners", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_documents", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.export_service.validate_bundle", lambda b: (True, []))

    await svc.run_export(jid)

    assert job.status == JobStatus.COMPLETED
    assert completed["path"].endswith(".fhir.json")
    assert os.path.exists(completed["path"])
    assert completed["counts"]["Patient"] == 1


@pytest.mark.asyncio
async def test_run_export_full_backup_writes_zip(monkeypatch, tmp_path):
    tid = uuid.uuid4()
    jid = uuid.uuid4()
    pid = uuid.uuid4()
    monkeypatch.setattr(
        "app.services.document_service_db.UPLOAD_DIR", str(tmp_path)
    )
    job = ExportJobModel(
        id=jid, tenant_id=tid, user_id=uuid.uuid4(),
        scope=ExportScope.SYSTEM, export_type=ExportType.FULL_BACKUP,
        status=JobStatus.PENDING, progress=0, patient_ids=None,
    )
    db = AsyncMock()
    svc = ExportService(db)

    completed = {}

    async def fake_complete(j, file_path, file_size, counts, manifest=None):
        completed["path"] = file_path
        completed["counts"] = counts
        job.status = JobStatus.COMPLETED

    monkeypatch.setattr(svc, "get_job", AsyncMock(return_value=job))
    monkeypatch.setattr(svc, "update_job_progress", AsyncMock())
    monkeypatch.setattr(svc, "complete_job", fake_complete)
    monkeypatch.setattr(svc, "fail_job", AsyncMock())
    monkeypatch.setattr(svc, "gather_patients", AsyncMock(return_value=[_make_patient(pid, tid)]))
    monkeypatch.setattr(svc, "gather_observations", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_medications", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_allergies", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_diagnostic_reports", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_organizations", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_practitioners", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_documents", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_examinations", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_clinical_events", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_clinical_event_types", AsyncMock(return_value={"types": [], "categories": []}))
    monkeypatch.setattr(svc, "gather_biomarker_catalog", AsyncMock(return_value={"units": [], "biomarkers": []}))
    monkeypatch.setattr(svc, "gather_medication_catalog", AsyncMock(return_value={"medications": []}))
    monkeypatch.setattr(svc, "gather_allergy_catalog", AsyncMock(return_value={"allergies": []}))
    monkeypatch.setattr(svc, "gather_telemetry", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_integrations", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "gather_notification_triggers", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.services.export_service.validate_bundle", lambda b: (True, []))

    await svc.run_export(jid)

    assert job.status == JobStatus.COMPLETED
    assert completed["path"].endswith(".zip")
    assert os.path.exists(completed["path"])
    assert "Patient" in completed["counts"]


@pytest.mark.asyncio
async def test_run_export_catalog_only_completes(monkeypatch, tmp_path):
    tid = uuid.uuid4()
    jid = uuid.uuid4()
    monkeypatch.setattr(
        "app.services.document_service_db.UPLOAD_DIR", str(tmp_path)
    )
    job = ExportJobModel(
        id=jid, tenant_id=tid, user_id=uuid.uuid4(),
        scope=ExportScope.SYSTEM, export_type=ExportType.CATALOG_ONLY,
        status=JobStatus.PENDING, progress=0, patient_ids=None,
    )
    db = AsyncMock()
    svc = ExportService(db)
    completed = {}

    async def fake_complete(j, file_path, file_size, counts, manifest=None):
        completed["path"] = file_path
        completed["counts"] = counts
        job.status = JobStatus.COMPLETED

    monkeypatch.setattr(svc, "get_job", AsyncMock(return_value=job))
    monkeypatch.setattr(svc, "update_job_progress", AsyncMock())
    monkeypatch.setattr(svc, "complete_job", fake_complete)
    monkeypatch.setattr(svc, "fail_job", AsyncMock())
    monkeypatch.setattr(svc, "gather_biomarker_catalog", AsyncMock(return_value={"units": [{"symbol": "mg/dL"}], "biomarkers": [{"slug": "glucose"}]}))
    monkeypatch.setattr(svc, "gather_clinical_event_types", AsyncMock(return_value={"types": [{"slug": "p"}], "categories": []}))
    monkeypatch.setattr(svc, "gather_medication_catalog", AsyncMock(return_value={"medications": [{"name": "Metformin"}]}))
    monkeypatch.setattr(svc, "gather_allergy_catalog", AsyncMock(return_value={"allergies": [{"name": "Peanuts"}]}))

    await svc.run_export(jid)

    assert job.status == JobStatus.COMPLETED
    assert completed["path"].endswith(".catalog.json")
    assert completed["counts"]["biomarkers"] == 1
    assert completed["counts"]["medication_catalog"] == 1
    assert completed["counts"]["allergy_catalog"] == 1


@pytest.mark.asyncio
async def test_run_export_fail_job_on_exception(monkeypatch, tmp_path):
    tid = uuid.uuid4()
    jid = uuid.uuid4()
    monkeypatch.setattr(
        "app.services.document_service_db.UPLOAD_DIR", str(tmp_path)
    )
    job = ExportJobModel(
        id=jid, tenant_id=tid, user_id=uuid.uuid4(),
        scope=ExportScope.PATIENT, export_type=ExportType.FHIR_ONLY,
        status=JobStatus.PENDING, progress=0, patient_ids=None,
    )
    db = AsyncMock()
    svc = ExportService(db)
    failed = {}

    async def fake_fail(j, e):
        failed["error"] = e
        job.status = JobStatus.FAILED

    monkeypatch.setattr(svc, "get_job", AsyncMock(return_value=job))
    monkeypatch.setattr(svc, "update_job_progress", AsyncMock())
    monkeypatch.setattr(svc, "complete_job", AsyncMock())
    monkeypatch.setattr(svc, "fail_job", fake_fail)
    monkeypatch.setattr(svc, "gather_patients", AsyncMock(side_effect=RuntimeError("db down")))

    with pytest.raises(RuntimeError):
        await svc.run_export(jid)

    assert job.status == JobStatus.FAILED
    assert "db down" in failed["error"]
