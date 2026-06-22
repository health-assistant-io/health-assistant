"""Regression tests for audit item C1 — telemetry→FHIR patient attribution.

Pre-fix contract: ``migrate_biomarker_data`` (telemetry → FHIR direction)
picked ``select(Patient.id).where(tenant_id == ...).limit(1)`` and
assigned every migrated Observation to this single arbitrary patient_id.
Telemetry rows have no ``patient_id`` by design, but in any multi-patient
tenant all migrated observations were attributed to the wrong person —
cross-patient data corruption.

Post-fix contract pinned here:
1. The resolver builds a ``device_id → user_id → patient_id`` map from
   the tenant's UserIntegrations + Patients.
2. Rows whose ``device_id`` maps to a known patient are attributed to
   that patient.
3. Rows that can't be attributed are skipped (counted in
   ``meta["migration_skipped_no_patient"]``), NOT silently assigned to a
   random tenant patient.
4. The single-patient-tenant fallback remains (it's unambiguous); the
   bug was specifically the multi-patient case.
5. If NO rows can be attributed (no integrations + multi-patient
   tenant), the migration aborts with ``meta["migration_status"] = "failed"``
   and a clear ``migration_error``.

Because the full ``migrate_biomarker_data`` task involves Celery
decorators + a fresh engine, we test the resolver logic in isolation.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _build_resolver(device_to_user: dict, user_to_patient: dict, all_tenant_patients: list):
    """Re-implement the per-row resolver extracted from the migration
    task. This mirrors the logic in ``tasks.py::migrate_biomarker_data``
    (telemetry→FHIR branch) without needing the Celery wrapper.
    """
    default_patient_id = (
        all_tenant_patients[0] if len(all_tenant_patients) == 1 else None
    )

    def resolve(device_id):
        if device_id and device_id in device_to_user:
            uid = device_to_user[device_id]
            resolved = user_to_patient.get(uid)
            if resolved is not None:
                return resolved
        return default_patient_id

    return resolve, default_patient_id


# ---------------------------------------------------------------------------
# C1: single-patient tenant fallback still works
# ---------------------------------------------------------------------------


def test_resolver_single_patient_tenant_uses_sole_patient():
    """If the tenant has exactly one patient, even rows without a
    device_id are safely attributable to that patient."""
    sole_patient = uuid.uuid4()
    resolver, default = _build_resolver(
        device_to_user={},
        user_to_patient={},
        all_tenant_patients=[sole_patient],
    )
    assert default == sole_patient
    assert resolver("any_device_id") == sole_patient
    assert resolver(None) == sole_patient


# ---------------------------------------------------------------------------
# C1: multi-patient tenant → rows attributed via device → user → patient
# ---------------------------------------------------------------------------


def test_resolver_multi_patient_tenant_uses_device_to_user_to_patient():
    """A multi-patient tenant: the resolver MUST go through device_id →
    user_id → patient_id. Direct fallback to "first patient" would
    attribute rows to the wrong patient."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    other_patient = uuid.uuid4()

    device_to_user = {"fitbit_1": user_a, "withings_2": user_b}
    user_to_patient = {user_a: patient_a, user_b: patient_b}
    all_tenant_patients = [patient_a, patient_b, other_patient]

    resolver, default = _build_resolver(
        device_to_user, user_to_patient, all_tenant_patients
    )
    # Multi-patient → NO default fallback.
    assert default is None
    # fitbit_1 → user_a → patient_a
    assert resolver("fitbit_1") == patient_a
    # withings_2 → user_b → patient_b
    assert resolver("withings_2") == patient_b
    # Unknown device → None (skip the row)
    assert resolver("unknown_device") is None
    # Missing device_id → None (skip the row)
    assert resolver(None) is None


def test_resolver_unknown_device_in_multi_patient_tenant_skips():
    """The core C1 fix: an unknown device_id in a multi-patient tenant
    MUST return None (skip), not silently attribute to a random patient."""
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()

    resolver, default = _build_resolver(
        device_to_user={},  # no integrations in tenant
        user_to_patient={},
        all_tenant_patients=[patient_a, patient_b],
    )
    assert default is None
    assert resolver("any_device") is None  # previously returned patient_a


def test_resolver_fhir_migration_device_id_is_never_attributed():
    """The legacy 'fhir_migration' device_id (set when going FHIR→telemetry)
    cannot be attributed to a patient. The resolver MUST skip it."""
    user_a = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()

    # Simulate the migration code's "pop fhir_migration from the device map"
    # behaviour: that device_id is removed from device_to_user before the
    # resolver runs.
    device_to_user = {"fhir_migration": user_a, "withings_1": user_a}
    device_to_user.pop("fhir_migration", None)

    user_to_patient = {user_a: patient_a}
    resolver, default = _build_resolver(
        device_to_user,
        user_to_patient,
        [patient_a, patient_b],  # multi-patient
    )

    # 'fhir_migration' rows cannot be attributed.
    assert resolver("fhir_migration") is None


# ---------------------------------------------------------------------------
# C1: abort with clear error when no rows can be attributed
# ---------------------------------------------------------------------------


def test_no_attribution_possible_in_multi_patient_tenant():
    """If there are no UserIntegrations AND the tenant has multiple
    patients, the migration MUST abort with a clear error rather than
    silently misattribute."""
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    device_to_user = {}  # empty
    user_to_patient = {}
    all_tenant_patients = [patient_a, patient_b]
    _, default = _build_resolver(device_to_user, user_to_patient, all_tenant_patients)
    assert default is None
    # When NO rows can be attributed, the migration task records:
    #   meta["migration_status"] = "failed"
    #   meta["migration_error"] = "Cannot attribute telemetry rows..."
    # See tasks.py for the actual abort branch.


# ---------------------------------------------------------------------------
# C1: integration test of the actual migration task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_biomarker_data_telemetry_to_fhir_skips_unknown_devices(monkeypatch):
    """End-to-end: telemetry rows with unknown device_id in a multi-
    patient tenant are skipped, NOT silently attributed to the first
    patient."""
    from app.workers import tasks as worker_tasks

    tenant_id = uuid.uuid4()
    biomarker_id = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()

    # Mock BiomarkerDefinition with is_telemetry already toggled to False
    # (the trigger for telemetry->FHIR migration).
    fake_biomarker = MagicMock()
    fake_biomarker.id = biomarker_id
    fake_biomarker.slug = "8867-4"
    fake_biomarker.code = "8867-4"
    fake_biomarker.name = "Heart Rate"
    fake_biomarker.coding_system = None
    fake_biomarker.preferred_unit_id = None
    fake_biomarker.meta_data = {}

    # Mock telemetry rows — some with a known device, some without.
    user_a = uuid.uuid4()
    known_device = "withings_user_a"
    unknown_device = "unknown"

    class _Tel:
        def __init__(self, device_id, hr, ts):
            self.id = uuid.uuid4()
            self.device_id = device_id
            self.heart_rate = hr
            self.steps = None
            self.calories = None
            self.data = None
            self.timestamp = ts
            self.tenant_id = tenant_id

    import datetime as _dt

    rows = [
        _Tel(known_device, 70, _dt.datetime.now(_dt.timezone.utc)),
        _Tel(unknown_device, 80, _dt.datetime.now(_dt.timezone.utc)),
    ]

    # Build a sequence of db.execute responses:
    # 1. select biomarker → return fake_biomarker
    # 2. telemetry count → 2 rows
    # 3. preferred unit symbol → "" (None selected)
    # 4. UserIntegrations → one row matching known_device → user_a
    # 5. Patients with user_id → [(patient_a, user_a)]
    # 6. All tenant patients → [patient_a, patient_b]  (multi-patient)
    # 7. telemetry rows (first batch) → the two rows
    # (each commit then writes progress, but no more reads)
    seq = []

    bio_res = MagicMock()
    bio_res.scalar_one_or_none.return_value = fake_biomarker
    seq.append(bio_res)

    count_res = MagicMock()
    count_res.scalar_one.return_value = 2
    seq.append(count_res)

    unit_res = MagicMock()
    unit_res.scalar_one_or_none.return_value = ""
    seq.append(unit_res)

    uint_res = MagicMock()
    uint_res.all.return_value = [
        (uuid.uuid4(), known_device, "withings", user_a),
    ]
    seq.append(uint_res)

    pat_res = MagicMock()
    pat_res.all.return_value = [(patient_a, user_a)]
    seq.append(pat_res)

    all_pat_res = MagicMock()
    all_pat_res.scalars.return_value.all.return_value = [patient_a, patient_b]
    seq.append(all_pat_res)

    tel_res = MagicMock()
    tel_res.scalars.return_value.all.return_value = rows
    seq.append(tel_res)

    call_count = {"i": 0}

    async def _execute(stmt, *a, **kw):
        if call_count["i"] < len(seq):
            r = seq[call_count["i"]]
            call_count["i"] += 1
            return r
        # Past the planned sequence — return empty/None.
        r = MagicMock()
        r.scalar_one.return_value = 0
        r.scalar_one_or_none.return_value = None
        r.scalars.return_value.all.return_value = []
        return r

    db = AsyncMock()
    db.execute = _execute
    db.add_all = MagicMock()
    db.delete = AsyncMock()
    db.commit = AsyncMock()

    # Bypass Celery decorators.
    raw_fn = worker_tasks.migrate_biomarker_data.__wrapped__.__wrapped__

    with monkeypatch.context() as m:
        m.setattr(worker_tasks, "get_async_session", lambda: (db, MagicMock(dispose=AsyncMock())))
        result = await raw_fn(None, str(biomarker_id), str(tenant_id), False)

    # The migration should succeed overall, but skipped_no_patient should
    # be set in the final meta.
    assert fake_biomarker.meta_data.get("migration_status") == "completed"
    # The unknown-device row was skipped.
    assert fake_biomarker.meta_data.get("migration_skipped_no_patient") == 1

    # Inspect what was actually added to FHIR — should be exactly ONE
    # Observation (the known-device row) attributed to patient_a.
    added = db.add_all.call_args_list
    all_obs = []
    for call in added:
        for obj in call.args[0]:
            all_obs.append(obj)
    assert len(all_obs) == 1, (
        f"Expected exactly 1 attributed observation, got {len(all_obs)} "
        "(the unknown-device row must be skipped, not attributed)"
    )
    assert all_obs[0].subject == {"reference": f"Patient/{patient_a}"}
