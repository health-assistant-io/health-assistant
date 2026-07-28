"""Regression tests for telemetry→FHIR patient attribution — long-format edition.

Historical context (audit C1): the original ``migrate_biomarker_data``
(telemetry → FHIR direction) picked ``select(Patient.id).where(tenant_id
== ...).limit(1)`` and assigned every migrated Observation to this single
arbitrary patient_id. In any multi-patient tenant all migrated observations
were attributed to the wrong person — cross-patient data corruption.

The first fix built a ``device_id → user_id → patient_id`` resolver from
the tenant's UserIntegrations + Patients. The **long-format rewrite**
(migration ``t1e2l3o4n5g6``) makes that resolver obsolete: ``patient_id``
is now persisted directly on every telemetry row at insert time (apply_telemetry_split,
the upload endpoint, the FHIR→telemetry migration direction).

Post-fix contract pinned here:
1. ``patient_id`` is resolved per row from ``tr.patient_id`` directly.
2. The single-patient-tenant fallback remains for rows that lack a
   persisted patient_id (legacy mobile uploads without patient_id).
3. Rows that can't be attributed (no ``patient_id`` + multi-patient
   tenant) are skipped (counted in ``meta["migration_skipped_no_patient"]``),
   NOT silently assigned to a random tenant patient.
4. The fragile ``device_id → UserIntegration → user_id → Patient`` chain
   is GONE — the resolver helper is no longer in the migration task.

Because the full ``migrate_biomarker_data`` task involves Celery decorators
+ a fresh engine, the integration test exercises the actual task with a
mocked session.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _build_resolver(all_tenant_patients: list):
    """Re-implement the per-row resolver extracted from the migration task.

    This mirrors the logic in ``tasks.py::migrate_biomarker_data``
    (telemetry→FHIR branch, long-format edition): use the row's persisted
    ``patient_id``; fall back to the single-patient-tenant default; skip
    otherwise.
    """
    default_patient_id = (
        all_tenant_patients[0] if len(all_tenant_patients) == 1 else None
    )

    def resolve(patient_id_on_row):
        if patient_id_on_row is not None:
            return patient_id_on_row
        return default_patient_id

    return resolve, default_patient_id


# ---------------------------------------------------------------------------
# Single-patient tenant fallback still works
# ---------------------------------------------------------------------------


def test_resolver_single_patient_tenant_uses_sole_patient():
    """If the tenant has exactly one patient, even rows without a persisted
    patient_id are safely attributable to that patient."""
    sole_patient = uuid.uuid4()
    resolver, default = _build_resolver(all_tenant_patients=[sole_patient])
    assert default == sole_patient
    # Row with patient_id set → use it directly.
    other_patient = uuid.uuid4()
    assert resolver(other_patient) == other_patient
    # Row without patient_id → single-patient fallback.
    assert resolver(None) == sole_patient


# ---------------------------------------------------------------------------
# Multi-patient tenant → rows attributed via persisted patient_id
# ---------------------------------------------------------------------------


def test_resolver_multi_patient_tenant_uses_persisted_patient_id():
    """A multi-patient tenant: the resolver MUST use the row's persisted
    ``patient_id``. No default fallback when the row lacks it."""
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()
    other_patient = uuid.uuid4()

    resolver, default = _build_resolver(
        all_tenant_patients=[patient_a, patient_b, other_patient]
    )
    # Multi-patient → NO default fallback.
    assert default is None
    # Row with patient_id → use it directly.
    assert resolver(patient_a) == patient_a
    assert resolver(patient_b) == patient_b
    # Row without patient_id → None (skip the row).
    assert resolver(None) is None


def test_resolver_unknown_patient_in_multi_patient_tenant_skips():
    """The core C1 guarantee preserved: a row that can't be attributed
    (no persisted patient_id + multi-patient tenant) MUST return None
    (skip), not silently attribute to a random patient."""
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()

    resolver, default = _build_resolver(
        all_tenant_patients=[patient_a, patient_b]
    )
    assert default is None
    assert resolver(None) is None  # previously returned patient_a


# ---------------------------------------------------------------------------
# Integration test of the actual migration task (long-format)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_biomarker_data_telemetry_to_fhir_uses_persisted_patient_id(monkeypatch):
    """End-to-end: a telemetry row carrying ``patient_id`` is migrated to a
    FHIR Observation attributed to that patient. A row WITHOUT ``patient_id``
    in a multi-patient tenant is skipped (counted in
    ``migration_skipped_no_patient``)."""
    from app.workers import tasks as worker_tasks

    tenant_id = uuid.uuid4()
    biomarker_id = uuid.uuid4()
    patient_a = uuid.uuid4()
    patient_b = uuid.uuid4()

    fake_biomarker = MagicMock()
    fake_biomarker.id = biomarker_id
    fake_biomarker.slug = "heart-rate"
    fake_biomarker.code = "8867-4"
    fake_biomarker.name = "Heart Rate"
    fake_biomarker.coding_system = None
    fake_biomarker.preferred_unit_id = None
    fake_biomarker.meta_data = {}

    # Long-format telemetry rows: one with patient_id, one without.
    import datetime as _dt

    class _Tel:
        def __init__(self, value, patient_id, ts, unit="bpm"):
            self.id = uuid.uuid4()
            self.device_id = "withings_1"
            self.timestamp = ts
            self.tenant_id = tenant_id
            self.slug = "heart-rate"
            self.value = value
            self.unit = unit
            self.patient_id = patient_id

    rows = [
        _Tel(70.0, patient_a, _dt.datetime.now(_dt.timezone.utc)),
        _Tel(80.0, None, _dt.datetime.now(_dt.timezone.utc)),  # no patient_id
    ]

    # Build a sequence of db.execute responses:
    # 1. select biomarker → return fake_biomarker
    # 2. telemetry count → 2 rows
    # 3. preferred unit symbol → "" (None selected)
    # 4. All tenant patients → [patient_a, patient_b]  (multi-patient)
    # 5. telemetry rows (first batch) → the two rows
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

    raw_fn = worker_tasks.migrate_biomarker_data.__wrapped__.__wrapped__

    with monkeypatch.context() as m:
        m.setattr(worker_tasks, "get_async_session", lambda: (db, MagicMock(dispose=AsyncMock())))
        await raw_fn(None, str(biomarker_id), str(tenant_id), False)

    # Migration completes overall; the no-patient-id row is skipped.
    assert fake_biomarker.meta_data.get("migration_status") == "completed"
    assert fake_biomarker.meta_data.get("migration_skipped_no_patient") == 1

    # Exactly ONE Observation was added — attributed to patient_a (the row
    # that carried patient_id).
    added = db.add_all.call_args_list
    all_obs = []
    for call in added:
        for obj in call.args[0]:
            all_obs.append(obj)
    assert len(all_obs) == 1, (
        f"Expected exactly 1 attributed observation, got {len(all_obs)} "
        "(the no-patient-id row must be skipped in a multi-patient tenant)"
    )
    assert all_obs[0].subject == {"reference": f"Patient/{patient_a}"}
    assert all_obs[0].patient_id == patient_a


# ---------------------------------------------------------------------------
# Source-level guard: the device→UserIntegration→Patient resolver is gone
# ---------------------------------------------------------------------------


def test_migration_task_has_no_device_to_user_resolver():
    """Long-format contract: the migration task must NOT rebuild the
    ``device_id → UserIntegration → user_id → Patient`` resolver. The
    ``patient_id`` is now persisted on the row at insert time."""
    import inspect

    from app.workers import tasks as worker_tasks

    src = inspect.getsource(worker_tasks.migrate_biomarker_data)
    assert "device_to_user" not in src, (
        "The device_to_user resolver must be deleted — patient_id is now "
        "persisted on the telemetry row at insert time."
    )
    assert "user_to_patient" not in src
