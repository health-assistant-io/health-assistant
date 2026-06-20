import hashlib
import uuid
import zipfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.enums import JobStatus
from app.models.export_import_job import ImportJobModel
from app.schemas.backup import BackupManifest, ManifestFile
from app.services.import_service import ImportService


# ---------- _apply_remap (pure) ----------

def test_apply_remap_rewrites_patient_reference():
    d = {
        "resourceType": "Observation",
        "id": "obs1",
        "subject": {"reference": "Patient/old-pid"},
        "performer": [{"reference": "Organization/old-org"}],
    }
    out = ImportService._apply_remap(d, {"old-pid": "new-pid"})
    assert out["subject"]["reference"] == "Patient/new-pid"
    assert out["performer"][0]["reference"] == "Organization/old-org"


def test_apply_remap_no_remap_returns_copy():
    d = {"resourceType": "Patient", "id": "p1", "name": [{"family": "Doe"}]}
    out = ImportService._apply_remap(d, {})
    assert out == d
    assert out is not d


def test_apply_remap_nested_context():
    d = {"resourceType": "Encounter", "context": {"reference": "Patient/abc"}}
    out = ImportService._apply_remap(d, {"abc": "xyz"})
    assert out["context"]["reference"] == "Patient/xyz"


# ---------- manifest verification ----------

def test_verify_manifest_from_zip_valid(tmp_path):
    payload = b'{"resourceType":"Bundle","type":"transaction","entry":[]}'
    sha = hashlib.sha256(payload).hexdigest()
    manifest = BackupManifest(
        exported_at=datetime.now(timezone.utc),
        scope="patient",
        export_type="fhir_only",
        smart_scope="patient/*.rs",
        files=[ManifestFile(path="fhir/bundle.json", sha256=sha, size=len(payload))],
    )
    zip_path = tmp_path / "b.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", manifest.model_dump_json())
        zf.writestr("fhir/bundle.json", payload)
    with zipfile.ZipFile(zip_path, "r") as zf:
        ok, m, errs = ImportService.verify_manifest_from_zip(zf)
    assert ok
    assert errs == []
    assert m is not None
    assert m.files[0].sha256 == sha


def test_verify_manifest_from_zip_mismatch(tmp_path):
    payload = b'{"x":1}'
    manifest = BackupManifest(
        exported_at=datetime.now(timezone.utc),
        scope="patient",
        export_type="fhir_only",
        smart_scope="patient/*.rs",
        files=[ManifestFile(path="fhir/bundle.json", sha256="000", size=1)],
    )
    zip_path = tmp_path / "b.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", manifest.model_dump_json())
        zf.writestr("fhir/bundle.json", payload)
    with zipfile.ZipFile(zip_path, "r") as zf:
        ok, m, errs = ImportService.verify_manifest_from_zip(zf)
    assert not ok
    assert any("mismatch" in e for e in errs)


def test_verify_manifest_from_zip_missing_manifest(tmp_path):
    zip_path = tmp_path / "b.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("foo.txt", b"x")
    with zipfile.ZipFile(zip_path, "r") as zf:
        ok, m, errs = ImportService.verify_manifest_from_zip(zf)
    assert not ok
    assert m is None
    assert any("manifest" in e for e in errs)


# ---------- restore_fhir_bundle (mocked DB) ----------

@pytest.mark.asyncio
async def test_restore_fhir_bundle_creates_patient_and_remaps_observation():
    tid = uuid.uuid4()
    old_pid = uuid.uuid4()
    old_oid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": str(old_pid), "name": [{"family": "Doe"}], "gender": "female"}},
            {"resource": {"resourceType": "Observation", "id": str(old_oid), "status": "final",
                          "code": {"text": "HR"}, "subject": {"reference": f"Patient/{old_pid}"}}},
        ],
    }
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute.return_value = res
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    created, updated, errors, warnings, id_remap = await svc.restore_fhir_bundle(
        bundle, tid, validate=False
    )
    assert created["Patient"] == 1
    assert created["Observation"] == 1
    assert errors == []
    assert str(old_pid) in id_remap
    assert str(old_oid) in id_remap
    assert id_remap[str(old_pid)] != str(old_pid)
    assert db.add.call_count == 2


@pytest.mark.asyncio
async def test_restore_fhir_bundle_updates_existing_same_tenant():
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": str(pid), "name": [{"family": "Updated"}], "gender": "male"}},
        ],
    }
    existing = MagicMock()
    existing.id = pid
    existing.tenant_id = tid
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = existing
    db.execute.return_value = res
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    created, updated, errors, warnings, id_remap = await svc.restore_fhir_bundle(
        bundle, tid, validate=False
    )
    assert updated["Patient"] == 1
    assert created == {}
    assert id_remap[str(pid)] == str(pid)
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_restore_fhir_bundle_remaps_when_id_in_other_tenant():
    tid = uuid.uuid4()
    other_tid = uuid.uuid4()
    pid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": str(pid), "name": [{"family": "X"}], "gender": "female"}},
        ],
    }
    existing = MagicMock()
    existing.id = pid
    existing.tenant_id = other_tid
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = existing
    db.execute.return_value = res
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    created, updated, errors, warnings, id_remap = await svc.restore_fhir_bundle(
        bundle, tid, validate=False
    )
    assert created["Patient"] == 1
    assert id_remap[str(pid)] != str(pid)
    db.add.assert_called()


@pytest.mark.asyncio
async def test_restore_fhir_bundle_invalid_resource_records_error():
    # Validation happens inside fhir_to_orm() via fhir.resources (regardless of
    # the `validate` flag); an invalid resource raises FhirSerializationError,
    # which restore_fhir_bundle catches → recorded in `errors`, no crash.
    tid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"resource": {"resourceType": "Observation"}},
        ],
    }
    db = AsyncMock()
    svc = ImportService(db)
    created, updated, errors, warnings, id_remap = await svc.restore_fhir_bundle(
        bundle, tid, validate=True
    )
    assert errors
    assert created == {}


@pytest.mark.asyncio
async def test_restore_fhir_bundle_skips_invalid_keeps_valid():
    """Skip-and-log contract: an invalid resource is skipped (recorded in
    `errors`) while valid siblings are still created — one bad entry does not
    abort the whole import."""
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {  # valid Patient
                "resource": {
                    "resourceType": "Patient",
                    "id": str(pid),
                    "name": [{"family": "Doe"}],
                    "gender": "female",
                }
            },
            {  # invalid Observation (missing required status/code)
                "resource": {"resourceType": "Observation", "id": "bad-obs"},
            },
        ],
    }
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None  # Patient does not exist yet → create
    db.execute.return_value = res
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    created, updated, errors, warnings, id_remap = await svc.restore_fhir_bundle(
        bundle, tid, validate=False
    )
    # Valid resource still created
    assert created["Patient"] == 1
    # Invalid resource skipped + recorded, not crashing
    assert errors and any("Observation" in e for e in errors)
    assert "Observation" not in created


@pytest.mark.asyncio
async def test_restore_fhir_bundle_unsupported_type_skipped():
    tid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"resource": {"resourceType": "Condition", "id": "c1", "subject": {"reference": "Patient/x"}}},
            {"resource": {"resourceType": "DocumentReference", "id": "d1", "status": "current"}},
        ],
    }
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute.return_value = res
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    c, u, errors, warnings, remap = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    
    # Condition is completely unsupported, raises a ValueError inside _restore_one_fhir_resource
    # so it gets caught and appended to errors OR skipped with warning
    assert any("Condition" in e for e in errors) or any("Condition" in w for w in warnings)
    
    # DocumentReference has an explicit bypass that skips it and logs a warning
    assert any("Skipped DocumentReference" in w for w in warnings) or any("Unsupported resource type DocumentReference" in w for w in warnings)


# ---------- restore_sidecar (mocked DB) ----------

@pytest.mark.asyncio
async def test_restore_sidecar_telemetry_inserts_rows():
    tid = uuid.uuid4()
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)
    payload = [
        {"device_id": "d1", "timestamp": "2026-06-18T10:00:00+00:00", "heart_rate": 72},
        {"device_id": "d1", "timestamp": "2026-06-18T10:01:00+00:00", "steps": 10},
    ]
    created, errors, warnings = await svc.restore_sidecar("telemetry.json", payload, tid, {})
    assert created["telemetry"] == 2
    assert errors == []
    assert db.add.call_count == 2


@pytest.mark.asyncio
async def test_restore_sidecar_ai_config_warns_unsupported():
    tid = uuid.uuid4()
    db = AsyncMock()
    svc = ImportService(db)
    created, errors, warnings = await svc.restore_sidecar(
        "ai_config.json", {"providers": []}, tid, {}
    )
    assert created == {}
    assert any("AI config" in w for w in warnings)


@pytest.mark.asyncio
async def test_restore_sidecar_unknown_name_skipped():
    tid = uuid.uuid4()
    db = AsyncMock()
    svc = ImportService(db)
    created, errors, warnings = await svc.restore_sidecar("mystery.json", {}, tid, {})
    assert created == {}
    assert any("mystery" in w for w in warnings)


@pytest.mark.asyncio
async def test_restore_sidecar_medication_catalog_inserts_rows():
    tid = uuid.uuid4()
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    # No existing rows -> every entry takes the insert path.
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    svc = ImportService(db)
    payload = {
        "medications": [
            {"name": "Metformin", "description": "diabetes med", "dosage_info": "500mg"},
            {"name": "Aspirin"},
        ]
    }
    created, errors, warnings = await svc.restore_sidecar(
        "medication_catalog.json", payload, tid, {}
    )
    assert created["medication_catalog"] == 2
    assert errors == []
    assert db.add.call_count == 2


@pytest.mark.asyncio
async def test_restore_sidecar_allergy_catalog_inserts_and_normalizes_category():
    tid = uuid.uuid4()
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    svc = ImportService(db)
    payload = {
        "allergies": [
            {"name": "Peanuts", "category": "food", "typical_reactions": ["Hives"]},
            {"name": "Latex", "category": "WEIRD"},  # unknown -> OTHER fallback
        ]
    }
    created, errors, warnings = await svc.restore_sidecar(
        "allergy_catalog.json", payload, tid, {}
    )
    assert created["allergy_catalog"] == 2
    assert errors == []
    # Inspect the AllergyCatalog instances added: category normalized to the enum.
    added = [call.args[0] for call in db.add.call_args_list]
    categories = {a.name: a.category for a in added}
    assert categories["Peanuts"].value == "FOOD"
    assert categories["Latex"].value == "OTHER"


# ---------- run_import orchestrator (patched) ----------

@pytest.mark.asyncio
async def test_run_import_zip_path_calls_restore_and_completes(tmp_path, monkeypatch):
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    jid = uuid.uuid4()
    job = ImportJobModel(
        id=jid, tenant_id=tid, user_id=uid, source_filename="b.zip",
        status=JobStatus.PENDING, progress=0,
    )
    db = AsyncMock()
    svc = ImportService(db)

    zip_path = tmp_path / "b.zip"
    payload = b'{"resourceType":"Bundle","type":"transaction","entry":[]}'
    sha = hashlib.sha256(payload).hexdigest()
    manifest = BackupManifest(
        exported_at=datetime.now(timezone.utc),
        scope="patient",
        export_type="fhir_only",
        smart_scope="patient/*.rs",
        files=[ManifestFile(path="fhir/bundle.json", sha256=sha, size=len(payload))],
    )
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", manifest.model_dump_json())
        zf.writestr("fhir/bundle.json", payload)

    monkeypatch.setattr(svc, "get_job", AsyncMock(return_value=job))
    monkeypatch.setattr(svc, "_update_progress", AsyncMock())
    monkeypatch.setattr(svc, "_complete_job", AsyncMock())
    monkeypatch.setattr(svc, "_fail_job", AsyncMock())
    monkeypatch.setattr(svc, "restore_fhir_bundle", AsyncMock(
        return_value=({"Patient": 1}, {}, [], [], {"old": "new"})
    ))
    monkeypatch.setattr(svc, "restore_sidecar", AsyncMock(return_value=({}, [], [])))
    monkeypatch.setattr(svc, "restore_documents", AsyncMock(return_value=0))
    monkeypatch.setattr("app.services.import_service.validate_bundle", lambda b: (True, []))

    result = await svc.run_import(jid, str(zip_path), uid)

    assert result.status == JobStatus.COMPLETED
    assert result.manifest_verified is True
    assert result.fhir_validated is True
    assert result.created_resources["Patient"] == 1


@pytest.mark.asyncio
async def test_run_import_bare_bundle_json(tmp_path, monkeypatch):
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    jid = uuid.uuid4()
    job = ImportJobModel(
        id=jid, tenant_id=tid, user_id=uid, source_filename="b.json",
        status=JobStatus.PENDING, progress=0,
    )
    db = AsyncMock()
    svc = ImportService(db)

    path = tmp_path / "b.json"
    path.write_text('{"resourceType":"Bundle","type":"transaction","entry":[]}')

    monkeypatch.setattr(svc, "get_job", AsyncMock(return_value=job))
    monkeypatch.setattr(svc, "_update_progress", AsyncMock())
    monkeypatch.setattr(svc, "_complete_job", AsyncMock())
    monkeypatch.setattr(svc, "_fail_job", AsyncMock())
    monkeypatch.setattr(svc, "restore_fhir_bundle", AsyncMock(
        return_value=({"Observation": 2}, {}, [], [], {})
    ))
    monkeypatch.setattr("app.services.import_service.validate_bundle", lambda b: (True, []))

    result = await svc.run_import(jid, str(path), uid)

    assert result.status == JobStatus.COMPLETED
    assert result.created_resources["Observation"] == 2
    assert result.manifest_verified is False


@pytest.mark.asyncio
async def test_run_import_bare_catalog_json(tmp_path, monkeypatch):
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    jid = uuid.uuid4()
    job = ImportJobModel(
        id=jid, tenant_id=tid, user_id=uid, source_filename="c.json",
        status=JobStatus.PENDING, progress=0,
    )
    db = AsyncMock()
    svc = ImportService(db)

    path = tmp_path / "c.json"
    path.write_text('{"units":[],"biomarkers":[{"slug":"glucose","name":"Glucose"}]}')

    monkeypatch.setattr(svc, "get_job", AsyncMock(return_value=job))
    monkeypatch.setattr(svc, "_update_progress", AsyncMock())
    monkeypatch.setattr(svc, "_complete_job", AsyncMock())
    monkeypatch.setattr(svc, "_fail_job", AsyncMock())
    monkeypatch.setattr(svc, "_restore_biomarker_catalog", AsyncMock(return_value=1))

    result = await svc.run_import(jid, str(path), uid)

    assert result.status == JobStatus.COMPLETED
    assert result.created_resources["biomarker_definitions"] == 1


@pytest.mark.asyncio
async def test_run_import_fail_job_on_exception(tmp_path, monkeypatch):
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    jid = uuid.uuid4()
    job = ImportJobModel(
        id=jid, tenant_id=tid, user_id=uid, source_filename="b.zip",
        status=JobStatus.PENDING, progress=0,
    )
    db = AsyncMock()
    svc = ImportService(db)
    failed = {}

    async def fake_fail(j, e):
        failed["err"] = e
        job.status = JobStatus.FAILED

    monkeypatch.setattr(svc, "get_job", AsyncMock(return_value=job))
    monkeypatch.setattr(svc, "_update_progress", AsyncMock())
    monkeypatch.setattr(svc, "_complete_job", AsyncMock())
    monkeypatch.setattr(svc, "_fail_job", fake_fail)
    monkeypatch.setattr(svc, "restore_fhir_bundle", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr("app.services.import_service.validate_bundle", lambda b: (True, []))

    zip_path = tmp_path / "b.zip"
    payload = b'{"resourceType":"Bundle","type":"transaction","entry":[]}'
    sha = hashlib.sha256(payload).hexdigest()
    manifest = BackupManifest(
        exported_at=datetime.now(timezone.utc),
        scope="patient",
        export_type="fhir_only",
        smart_scope="patient/*.rs",
        files=[ManifestFile(path="fhir/bundle.json", sha256=sha, size=len(payload))],
    )
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", manifest.model_dump_json())
        zf.writestr("fhir/bundle.json", payload)

    with pytest.raises(RuntimeError):
        await svc.run_import(jid, str(zip_path), uid)
    assert job.status == JobStatus.FAILED
    assert "boom" in failed["err"]
