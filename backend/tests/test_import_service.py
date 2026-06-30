import hashlib
import uuid
import zipfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.enums import JobStatus
from app.models.export_import_job import ImportJobModel
from app.models.fhir.patient import Patient
from app.schemas.backup import BackupManifest, ManifestFile
from app.services.import_service import BundleRestoreResult, ImportService


@pytest.fixture(autouse=True)
def _silence_import_provenance(monkeypatch):
    """G6: every created/updated entry records a Provenance via
    ``_record_import_provenance``. By default we spy on it (no-op) so tests
    that assert on ``db.add`` aren't coupled to the Provenance row count.
    G6-specific tests access ``ImportService._record_import_provenance`` (the
    AsyncMock) to assert the call was made."""
    monkeypatch.setattr(ImportService, "_record_import_provenance", AsyncMock())


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


# ---------- G11: _apply_remap reference routing ----------

def test_apply_remap_routes_encounter_urn_uuid_to_encounter():
    """G11 headline bug: a bare urn:uuid in an 'encounter' field must map to
    Encounter, not Patient (the old default for every non-performer/partOf hint)."""
    d = {
        "resourceType": "Observation",
        "id": "obs1",
        "subject": {"reference": "urn:uuid:pid"},
        "encounter": {"reference": "urn:uuid:eid"},
    }
    out = ImportService._apply_remap(d, {"pid": "new-pid", "eid": "new-eid"})
    assert out["encounter"]["reference"] == "Encounter/new-eid"
    assert out["subject"]["reference"] == "Patient/new-pid"


def test_apply_remap_routes_sender_recipient_via_bundle_lookahead():
    """G11: ambiguous hints (sender/recipient) resolved via the urn_type_index."""
    d = {
        "resourceType": "Communication",
        "id": "c1",
        "sender": {"reference": "urn:uuid:dev1"},
        "recipient": {"reference": "urn:uuid:prac1"},
    }
    urn_type_index = {"dev1": "Device", "prac1": "Practitioner"}
    out = ImportService._apply_remap(d, {"dev1": "new-dev", "prac1": "new-prac"}, urn_type_index=urn_type_index)
    assert out["sender"]["reference"] == "Device/new-dev"
    assert out["recipient"]["reference"] == "Practitioner/new-prac"


def test_apply_remap_sender_without_index_falls_back_to_patient():
    """G11: when no type can be determined (ambiguous hint + no bundle index),
    fall back to Patient rather than crash."""
    d = {"resourceType": "Communication", "id": "c1", "sender": {"reference": "urn:uuid:unknown"}}
    out = ImportService._apply_remap(d, {"unknown": "new-id"})
    assert out["sender"]["reference"] == "Patient/new-id"


def test_apply_remap_author_routes_to_practitioner():
    """G11: 'author' field is now recursed (was previously ignored)."""
    d = {"resourceType": "DocumentReference", "id": "d1", "author": [{"reference": "urn:uuid:doc1"}]}
    out = ImportService._apply_remap(d, {"doc1": "new-doc"})
    assert out["author"][0]["reference"] == "Practitioner/new-doc"


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

    result = await svc.restore_fhir_bundle(
        bundle, tid, validate=False
    )
    assert result.created["Patient"] == 1
    assert result.created["Observation"] == 1
    assert result.errors == []
    assert str(old_pid) in result.id_remap
    assert str(old_oid) in result.id_remap
    assert result.id_remap[str(old_pid)] != str(old_pid)
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

    result = await svc.restore_fhir_bundle(
        bundle, tid, validate=False
    )
    created, updated = result.created, result.updated
    assert updated["Patient"] == 1
    assert created == {}
    assert result.id_remap[str(pid)] == str(pid)
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

    result = await svc.restore_fhir_bundle(
        bundle, tid, validate=False
    )
    created = result.created
    assert created["Patient"] == 1
    assert result.id_remap[str(pid)] != str(pid)
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
    result = await svc.restore_fhir_bundle(
        bundle, tid, validate=True
    )
    assert result.errors
    assert result.created == {}


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

    result = await svc.restore_fhir_bundle(
        bundle, tid, validate=False
    )
    created, errors = result.created, result.errors
    # Valid resource still created
    assert created["Patient"] == 1
    # Invalid resource skipped + recorded, not crashing
    assert errors and any("Observation" in e for e in errors)
    assert "Observation" not in created


@pytest.mark.asyncio
async def test_restore_fhir_bundle_unsupported_type_skipped():
    """Types not in the _TO_ORM converter map are surfaced as a warning (not silent)."""
    tid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            # Coverage is genuinely unsupported (no converter, no model)
            {"resource": {"resourceType": "Coverage", "id": "cov1"}},
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

    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    warnings = result.warnings

    # Coverage is unsupported → surfaced as a warning (no longer silently dropped)
    assert any("Coverage" in w for w in warnings)

    # DocumentReference has an explicit bypass that skips it and logs a warning
    assert any("Skipped DocumentReference" in w for w in warnings)


# ---------- G7/I4: entry.request.method + ifNoneExist (verb routing) ----------

def test_parse_request_id_extracts_id_from_type_id_url():
    assert ImportService._parse_request_id("Observation/abc-123") == "abc-123"
    assert ImportService._parse_request_id("Patient/00000000-0000-0000-0000-000000000000") == \
        "00000000-0000-0000-0000-000000000000"


def test_parse_request_id_returns_none_for_conditional_or_malformed():
    # Conditional URL (no literal id) — used by POST + ifNoneExist / conditional update
    assert ImportService._parse_request_id("Observation?identifier=foo|bar") is None
    assert ImportService._parse_request_id(None) is None
    assert ImportService._parse_request_id("") is None
    assert ImportService._parse_request_id("just-a-type") is None
    assert ImportService._parse_request_id("Type/") is None  # empty id segment


@pytest.mark.asyncio
async def test_bundle_put_updates_existing_observation():
    """PUT with url 'Observation/<id>' on an existing same-tenant row → update."""
    tid = uuid.uuid4()
    oid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "request": {"method": "PUT", "url": f"Observation/{oid}"},
                "resource": {
                    "resourceType": "Observation",
                    "id": str(oid),
                    "status": "final",
                    "code": {"text": "HR"},
                    "subject": {"reference": "Patient/x"},
                },
            }
        ],
    }
    existing = MagicMock()
    existing.id = oid
    existing.tenant_id = tid
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = existing
    db.execute.return_value = res
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    assert result.updated.get("Observation") == 1
    assert result.created == {}
    # Update path uses sa_update — no new row added
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_bundle_put_creates_with_supplied_id_when_missing():
    """PUT with url 'Observation/<id>' when the id does not exist → create WITH the supplied id."""
    tid = uuid.uuid4()
    oid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "request": {"method": "PUT", "url": f"Observation/{oid}"},
                "resource": {
                    "resourceType": "Observation",
                    "id": str(oid),
                    "status": "final",
                    "code": {"text": "HR"},
                    "subject": {"reference": "Patient/x"},
                },
            }
        ],
    }
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None  # not found → create-with-id
    db.execute.return_value = res
    added: list = []
    db.add = MagicMock(side_effect=lambda obj: added.append(obj))
    db.flush = AsyncMock()
    svc = ImportService(db)

    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    assert result.created.get("Observation") == 1
    # The new Observation MUST carry the PUT url's id (force_id), not a random uuid4
    assert len(added) == 1
    assert added[0].id == oid


@pytest.mark.asyncio
async def test_bundle_post_with_if_none_exist_skips_when_match_exists():
    """POST + ifNoneExist=identifier=<mrn> on a Patient whose mrn already exists → conditional skip."""
    tid = uuid.uuid4()
    existing_pid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "request": {"method": "POST", "ifNoneExist": "identifier=urn:local|MRN-42"},
                "resource": {
                    "resourceType": "Patient",
                    "name": [{"family": "Doe"}],
                    "gender": "female",
                },
            }
        ],
    }
    db = AsyncMock()
    # First db.execute is the conditional-find SELECT → returns existing id
    find_res = MagicMock()
    find_res.first.return_value = (existing_pid,)
    # Any subsequent execute (the upsert path) should NOT be reached
    db.execute = AsyncMock(return_value=find_res)
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    assert result.created == {}
    assert result.skipped.get("Patient") == 1
    db.add.assert_not_called()
    assert any("ifNoneExist matched" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_bundle_post_with_if_none_exist_creates_when_no_match():
    """POST + ifNoneExist where no match → unconditional create."""
    tid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "request": {"method": "POST", "ifNoneExist": "identifier=urn:local|MRN-NEW"},
                "resource": {
                    "resourceType": "Patient",
                    "name": [{"family": "New"}],
                    "gender": "male",
                },
            }
        ],
    }
    db = AsyncMock()
    # conditional-find returns no row, _resolve_id returns None (not found)
    find_res = MagicMock()
    find_res.first.return_value = None
    resolve_res = MagicMock()
    resolve_res.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(side_effect=[find_res, resolve_res])
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    assert result.created.get("Patient") == 1
    assert result.skipped == {}


@pytest.mark.asyncio
async def test_bundle_delete_soft_deletes_existing():
    """DELETE with url 'Patient/<id>' on an existing row → soft-delete (deleted_at set)."""
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "request": {"method": "DELETE", "url": f"Patient/{pid}"},
                "resource": {"resourceType": "Patient", "id": str(pid)},
            }
        ],
    }
    # sa_update returns a CursorResult with rowcount
    delete_result = MagicMock()
    delete_result.rowcount = 1
    db = AsyncMock()
    db.execute = AsyncMock(return_value=delete_result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    assert result.deleted.get("Patient") == 1
    assert result.created == {}
    # DELETE never adds a row
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_bundle_delete_is_idempotent_on_missing():
    """DELETE on a missing id → skipped_idempotent (no error, not counted as deleted)."""
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "request": {"method": "DELETE", "url": f"Patient/{pid}"},
                "resource": {"resourceType": "Patient", "id": str(pid)},
            }
        ],
    }
    # rowcount == 0 (nothing matched the WHERE clause)
    delete_result = MagicMock()
    delete_result.rowcount = 0
    db = AsyncMock()
    db.execute = AsyncMock(return_value=delete_result)
    svc = ImportService(db)

    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    assert result.deleted == {}
    assert result.errors == []


@pytest.mark.asyncio
async def test_bundle_post_without_request_block_defaults_to_create_new():
    """An entry with no request block at all keeps the historical POST (create-new) behaviour."""
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": str(pid), "name": [{"family": "Doe"}], "gender": "female"}},
        ],
    }
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute.return_value = res
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    assert result.created.get("Patient") == 1
    assert result.skipped == {}
    assert result.deleted == {}


# ---------- G8: import all 15 resource types (dispatcher branches) ----------

def _mock_db_for_create():
    """Mocked DB that reports 'no existing row' so every entry creates fresh."""
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute.return_value = res
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rt, resource",
    [
        ("Condition", {"resourceType": "Condition", "id": "c1", "subject": {"reference": "Patient/x"},
                       "code": {"text": "Hypertension"}, "clinicalStatus": {"coding": [{"code": "active"}]}}),
        ("Encounter", {"resourceType": "Encounter", "id": "e1", "status": "finished",
                       "subject": {"reference": "Patient/x"},
                       "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
                       "period": {"start": "2026-01-15T10:00:00Z"}}),
        ("Device", {"resourceType": "Device", "id": "dev1", "status": "active",
                    "type": {"coding": [{"system": "http://snomed.info/sct", "code": "257"}]},
                    "patient": {"reference": "Patient/x"}}),
        ("Communication", {"resourceType": "Communication", "id": "comm1", "status": "completed",
                           "subject": {"reference": "Patient/x"}}),
        ("MedicationRequest", {"resourceType": "MedicationRequest", "id": "mr1", "status": "active",
                               "intent": "order", "subject": {"reference": "Patient/x"},
                               "medicationCodeableConcept": {"text": "Lisinopril"}}),
    ],
)
async def test_import_round_trips_each_newly_supported_type(rt, resource):
    """G8: each previously-dropped resource type now routes through its _upsert_* branch."""
    tid = uuid.uuid4()
    bundle = {"resourceType": "Bundle", "type": "transaction", "entry": [{"resource": resource}]}
    db = _mock_db_for_create()
    svc = ImportService(db)
    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    # The resource was created (not silently dropped, not errored)
    assert result.created.get(rt) == 1, f"{rt} not created; errors={result.errors}, warnings={result.warnings}"
    assert rt not in result.skipped
    assert db.add.called, f"{rt}: no row added"


@pytest.mark.asyncio
async def test_import_provenance_creates_with_target_and_agent():
    """G8: Provenance upserts with the canonical target/agent/recorded fields."""
    tid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{
            "resource": {
                "resourceType": "Provenance", "id": "p1",
                "target": [{"reference": "Observation/abc"}],
                "recorded": "2026-06-30T12:00:00Z",
                "activity": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ProvenanceEventType", "code": "CREATE"}]},
                "agent": [{"who": {"reference": "Device/dev1"}}],
            }
        }],
    }
    db = _mock_db_for_create()
    svc = ImportService(db)
    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    assert result.created.get("Provenance") == 1
    added = db.add.call_args[0][0]
    assert added.target == [{"reference": "Observation/abc"}]
    assert added.agent == [{"who": {"reference": "Device/dev1"}}]


@pytest.mark.asyncio
async def test_import_unknown_resource_type_surfaces_warning():
    """G8 cleanup: a truly unsupported type (no converter) surfaces a warning, not silence."""
    tid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{"resource": {"resourceType": "Coverage", "id": "cov1"}}],
    }
    db = _mock_db_for_create()
    svc = ImportService(db)
    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    assert result.created == {}
    assert any("Coverage" in w for w in result.warnings)


# ---------- G6: record Provenance per imported entry ----------

@pytest.mark.asyncio
async def test_g6_provenance_recorded_for_each_created_entry():
    """Each created entry triggers _record_import_provenance with activity=CREATE."""
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    oid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": str(pid), "name": [{"family": "D"}], "gender": "female"}},
            {"resource": {"resourceType": "Observation", "id": str(oid), "status": "final",
                          "code": {"text": "HR"}, "subject": {"reference": f"Patient/{pid}"}}},
        ],
    }
    db = _mock_db_for_create()
    svc = ImportService(db)
    uid = uuid.uuid4()
    jid = uuid.uuid4()
    await svc.restore_fhir_bundle(bundle, tid, validate=False, actor_user_id=uid, source_job_id=jid)

    spy = ImportService._record_import_provenance
    assert spy.call_count == 2
    # Each call: (rt, target_id, action, tenant_id, actor_user_id, source_job_id)
    activities = sorted([c.args[2] for c in spy.call_args_list])
    assert activities == ["created", "created"]
    rts = sorted([c.args[0] for c in spy.call_args_list])
    assert rts == ["Observation", "Patient"]
    for call in spy.call_args_list:
        assert call.args[3] == tid
        assert call.args[4] == uid
        assert call.args[5] == jid


@pytest.mark.asyncio
async def test_g6_provenance_activity_matches_verb():
    """POST→CREATE, PUT→UPDATE, DELETE→DELETE map to the right activity."""
    tid = uuid.uuid4()
    rid = uuid.uuid4()
    existing = MagicMock()
    existing.id = rid
    existing.tenant_id = tid
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = existing  # PUT finds it → update
    db.execute = AsyncMock(return_value=res)
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"request": {"method": "PUT", "url": f"Patient/{rid}"},
             "resource": {"resourceType": "Patient", "id": str(rid), "name": [{"family": "U"}], "gender": "male"}},
        ],
    }
    await svc.restore_fhir_bundle(bundle, tid, validate=False)

    spy = ImportService._record_import_provenance
    spy.assert_called_once()
    assert spy.call_args.args[2] == "updated"


@pytest.mark.asyncio
async def test_g6_provenance_not_recorded_for_conditional_skip():
    """skipped_conditional entries do NOT record provenance (nothing was created)."""
    tid = uuid.uuid4()
    existing_pid = uuid.uuid4()
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{
            "request": {"method": "POST", "ifNoneExist": "identifier=urn:local|MRN-42"},
            "resource": {"resourceType": "Patient", "name": [{"family": "D"}], "gender": "female"},
        }],
    }
    db = AsyncMock()
    find_res = MagicMock()
    find_res.first.return_value = (existing_pid,)  # conditional match → skip
    db.execute = AsyncMock(return_value=find_res)
    svc = ImportService(db)
    await svc.restore_fhir_bundle(bundle, tid, validate=False)
    ImportService._record_import_provenance.assert_not_called()


@pytest.mark.asyncio
async def test_g9_resolve_id_warns_on_cross_tenant_collision():
    """G9: when a bundle id exists in another tenant, _resolve_id surfaces a
    warning (via self._collision_warnings) instead of silently creating new."""
    tid = uuid.uuid4()
    other_tid = uuid.uuid4()
    pid = uuid.uuid4()
    existing = MagicMock()
    existing.id = pid
    existing.tenant_id = other_tid  # different tenant → collision
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=res)
    svc = ImportService(db)
    svc._collision_warnings = []  # normally initialized by restore_fhir_bundle

    existing_id, new_id, action = await svc._resolve_id(Patient, str(pid), tid)

    assert existing_id is None
    assert action == "created"
    assert new_id != pid  # a fresh id was minted
    assert len(svc._collision_warnings) == 1
    assert str(pid) in svc._collision_warnings[0]
    assert str(other_tid) in svc._collision_warnings[0]


@pytest.mark.asyncio
async def test_g9_collision_warning_surfaces_in_bundle_result():
    """G9 end-to-end: a bundle importing an id that exists in another tenant
    surfaces the collision in the result's warnings list."""
    tid = uuid.uuid4()
    other_tid = uuid.uuid4()
    pid = uuid.uuid4()
    existing = MagicMock()
    existing.id = pid
    existing.tenant_id = other_tid
    db = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=res)
    db.add = MagicMock()
    db.flush = AsyncMock()
    svc = ImportService(db)

    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"resource": {"resourceType": "Patient", "id": str(pid), "name": [{"family": "X"}], "gender": "female"}},
        ],
    }
    result = await svc.restore_fhir_bundle(bundle, tid, validate=False)
    assert result.created.get("Patient") == 1
    assert any("already exists in tenant" in w for w in result.warnings)


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
        return_value=BundleRestoreResult(created={"Patient": 1}, id_remap={"old": "new"})
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
        return_value=BundleRestoreResult(created={"Observation": 2})
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
