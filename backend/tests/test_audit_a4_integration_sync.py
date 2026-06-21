"""Tests for audit item A4 (telemetry/FHIR split in sync_active_integrations).

A4: The background Celery task ``sync_active_integrations`` (runs every 60s)
    wrote ALL pulled Observations into ``fhir_observations`` regardless of
    ``BiomarkerDefinition.is_telemetry``. Three other code paths (manual
    sync, webhook, bridge) did the split correctly. Result: telemetry-class
    biomarkers synced via the background loop landed in the FHIR table where
    the AI telemetry tools couldn't see them.

The fix introduces ``integration_sync_service.apply_telemetry_split`` and
wires it into both the background task and the manual sync endpoint.
"""
import datetime
import inspect
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest


TENANT_A = UUID("11111111-1111-1111-1111-111111111111")


class _FakeObservation:
    """Stand-in for the ORM Observation class so test routing checks can
    distinguish it from TelemetryDataModel via ``type(x).__name__``."""

    def __init__(self, biomarker_id, value=70.0, code_loinc="8867-4"):
        self.id = uuid4()
        self.biomarker_id = biomarker_id
        self.effective_datetime = datetime.datetime(
            2026, 1, 1, tzinfo=datetime.timezone.utc
        )
        self.raw_value = value
        self.normalized_value = value
        self.value_quantity = {"value": value, "unit": "{beats}/min"}
        self.performer = None
        self.subject = {
            "reference": "Patient/00000000-0000-0000-0000-000000000000"
        }


# Patch the helper's type-check by giving the fake class the real name
_FakeObservation.__name__ = "Observation"
_FakeObservation.__qualname__ = "Observation"


def _make_obs(biomarker_id, value=70.0, code_loinc="8867-4"):
    """Build a fake Observation ORM object for the split helper."""
    return _FakeObservation(biomarker_id, value, code_loinc)


def _make_biomarker(b_id, slug, is_telemetry):
    b = MagicMock()
    b.id = b_id
    b.slug = slug
    b.is_telemetry = is_telemetry
    return b


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Captures db.add_all(...) calls so the test can verify routing."""

    def __init__(self, biomarker_rows):
        self._biomarker_rows = biomarker_rows
        self.added_telemetry = []
        self.added_fhir = []

    async def execute(self, query):
        # The helper's only DB read is the BiomarkerDefinition select.
        return _FakeResult(self._biomarker_rows)

    def add_all(self, records):
        # Distinguish by class name since both are added via add_all
        for r in records:
            cls = type(r).__name__
            if cls == "TelemetryDataModel":
                self.added_telemetry.append(r)
            elif cls == "Observation":
                self.added_fhir.append(r)
            else:
                raise AssertionError(f"Unexpected record type routed: {cls}")


# ---------------------------------------------------------------------------
# A4: split-helper unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_split_routes_telemetry_flagged_to_hypertable():
    """A4: a telemetry-flagged biomarker must land in telemetry_data only."""
    from app.services.integration_sync_service import apply_telemetry_split

    b_id = uuid4()
    b_def = _make_biomarker(b_id, "heart-rate", is_telemetry=True)
    session = _FakeSession([b_def])

    obs = _make_obs(b_id, value=72.0)

    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [obs],
        tenant_id=TENANT_A,
        instance_name="fitbit-1",
        provider_name="fitbit",
        integration_id=uuid4(),
    )
    assert len(telemetry_records) == 1
    assert len(fhir_records) == 0
    assert session.added_telemetry == telemetry_records
    assert session.added_fhir == []
    # Heart rate slug → dedicated heart_rate column
    assert telemetry_records[0].heart_rate == 72.0
    assert telemetry_records[0].steps is None
    assert telemetry_records[0].calories is None


@pytest.mark.asyncio
async def test_split_routes_non_telemetry_to_fhir():
    """A4: a non-telemetry biomarker must land in fhir_observations only."""
    from app.services.integration_sync_service import apply_telemetry_split

    b_id = uuid4()
    b_def = _make_biomarker(b_id, "cholesterol", is_telemetry=False)
    session = _FakeSession([b_def])

    obs = _make_obs(b_id, value=5.2, code_loinc="2093-3")

    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [obs],
        tenant_id=TENANT_A,
        instance_name="labcorp",
        provider_name="labcorp",
        integration_id=uuid4(),
    )
    assert len(telemetry_records) == 0
    assert len(fhir_records) == 1
    assert session.added_fhir == fhir_records
    # Performer must be stamped with the integration reference
    assert fhir_records[0].performer[0]["reference"].startswith("Integration/")
    assert fhir_records[0].performer[0]["display"] == "labcorp"


@pytest.mark.asyncio
async def test_split_mixed_batch_routes_correctly():
    """A4: a mixed batch (1 telemetry + 1 FHIR) must split cleanly."""
    from app.services.integration_sync_service import apply_telemetry_split

    telemetry_b = _make_biomarker(uuid4(), "steps", is_telemetry=True)
    fhir_b = _make_biomarker(uuid4(), "ldl", is_telemetry=False)
    session = _FakeSession([telemetry_b, fhir_b])

    obs_t = _make_obs(telemetry_b.id, value=5400, code_loinc="41950-7")
    obs_f = _make_obs(fhir_b.id, value=120.0, code_loinc="2089-1")

    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [obs_t, obs_f],
        tenant_id=TENANT_A,
        instance_name="dummy-1",
        provider_name="dev_dummy",
        integration_id=uuid4(),
    )
    assert len(telemetry_records) == 1
    assert len(fhir_records) == 1
    # Steps slug → dedicated steps column
    assert telemetry_records[0].steps == 5400
    assert telemetry_records[0].heart_rate is None
    assert telemetry_records[0].calories is None


@pytest.mark.asyncio
async def test_split_unknown_biomarker_defaults_to_fhir():
    """A4: an observation with no biomarker_id defaults to FHIR.

    This mirrors the behavior of every other code path — only flagged
    biomarkers route to telemetry.
    """
    from app.services.integration_sync_service import apply_telemetry_split

    session = _FakeSession([])  # no biomarker definitions loaded

    obs = _make_obs(biomarker_id=None, value=1.0)

    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [obs],
        tenant_id=TENANT_A,
        instance_name="x",
        provider_name="x",
        integration_id=uuid4(),
    )
    assert len(telemetry_records) == 0
    assert len(fhir_records) == 1


@pytest.mark.asyncio
async def test_split_empty_input_returns_empty():
    """A4: no observations → no rows added, no DB hit."""
    from app.services.integration_sync_service import apply_telemetry_split

    session = _FakeSession([])
    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [],
        tenant_id=TENANT_A,
        instance_name="x",
        provider_name="x",
        integration_id=uuid4(),
    )
    assert telemetry_records == []
    assert fhir_records == []


@pytest.mark.asyncio
async def test_split_telemetry_long_tail_goes_into_data_jsonb():
    """A4: telemetry biomarkers without a dedicated column land in ``data``."""
    from app.services.integration_sync_service import apply_telemetry_split

    b_id = uuid4()
    b_def = _make_biomarker(b_id, "stress-level", is_telemetry=True)
    session = _FakeSession([b_def])

    obs = _make_obs(b_id, value=6.5, code_loinc="custom-stress")

    telemetry_records, _ = await apply_telemetry_split(
        session,
        [obs],
        tenant_id=TENANT_A,
        instance_name="whoop-1",
        provider_name="whoop",
        integration_id=uuid4(),
    )
    assert len(telemetry_records) == 1
    row = telemetry_records[0]
    # No dedicated column matched → all in JSONB ``data``
    assert row.heart_rate is None
    assert row.steps is None
    assert row.calories is None
    assert row.data is not None
    assert "stress-level" in row.data
    assert row.data["stress-level"] == 6.5


# ---------------------------------------------------------------------------
# A4: the background task now actually invokes the helper
# ---------------------------------------------------------------------------


def test_sync_active_integrations_imports_apply_telemetry_split():
    """A4 regression: the background task must call the split helper.

    Catches the bug regressing at source level — if a future edit removes
    the import/call, this test fails.
    """
    from app.workers import tasks

    src = inspect.getsource(tasks.sync_active_integrations)
    assert "apply_telemetry_split" in src, (
        "sync_active_integrations does not invoke apply_telemetry_split — "
        "audit A4 regressed (telemetry would again land in fhir_observations)"
    )


def test_manual_sync_endpoint_uses_shared_helper():
    """A4: the manual sync endpoint should also use the shared helper (DRY)."""
    from app.api.v1.endpoints import integrations

    # The manual_sync_endpoint function name in integrations.py
    src = inspect.getsource(integrations)
    assert "apply_telemetry_split" in src, (
        "manual sync endpoint should also use apply_telemetry_split for DRY"
    )
