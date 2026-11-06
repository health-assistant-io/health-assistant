"""Regression: a brand-new telemetry biomarker needs no DDL or branching.

The long-format contract (``0001_baseline`` + ``0005_telemetry_unified``) means
a new metric is a ``BiomarkerDefinition`` row with ``is_telemetry=True`` — no
dedicated column, no service-layer dispatch, no JSONB catch-all. Ingest then
resolves the slug through the registry, canonicalizes the unit, and stores a
uniform row.
"""

import datetime
import inspect
import uuid
from unittest.mock import MagicMock

import pytest

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
PATIENT = uuid.UUID("22222222-2222-2222-2222-222222222222")
DEVICE = "spo2-ring"


@pytest.mark.asyncio
async def test_spo2_routes_to_telemetry_row(monkeypatch):
    """A telemetry-flagged ``spo2`` observation becomes a uniform row."""
    from app.services import integration_sync_service as svc

    b_id = uuid.uuid4()
    b_def = MagicMock()
    b_def.id = b_id
    b_def.slug = "spo2"
    b_def.is_telemetry = True
    b_def.preferred_unit = None

    class _FakeResult:
        def scalars(self):
            return self

        def all(self):
            return [b_def]

    class _FakeSession:
        def __init__(self):
            self.added_telemetry = []

        async def execute(self, query):
            return _FakeResult()

        def add_all(self, records):
            self.added_telemetry.extend(records)

    class _FakeObs:
        def __init__(self):
            self.id = uuid.uuid4()
            self.biomarker_id = b_id
            self.effective_datetime = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
            self.raw_value = 97.0
            self.normalized_value = 97.0
            self.value_quantity = {"value": 97.0, "unit": "%"}
            self.performer = None
            self.patient_id = uuid.uuid4()

    _FakeObs.__name__ = "Observation"
    _FakeObs.__qualname__ = "Observation"

    async def _resolve(_db, **_kwargs):
        return uuid.uuid4()

    monkeypatch.setattr(svc, "resolve_telemetry_device", _resolve)

    session = _FakeSession()
    telemetry_records, fhir_records = await svc.apply_telemetry_split(
        session,
        [_FakeObs()],
        tenant_id=TENANT,
        instance_name=DEVICE,
        provider_name="ring",
        integration_id=uuid.uuid4(),
    )
    assert len(telemetry_records) == 1
    assert len(fhir_records) == 0
    row = telemetry_records[0]
    # Uniform long-format shape — no dedicated spo2 column, no JSONB.
    assert row.slug == "spo2"
    assert row.value == 97.0
    assert row.unit == "%"
    assert not hasattr(row, "data"), (
        "Long-format TelemetryDataModel must not carry a JSONB data column"
    )
    assert not hasattr(row, "spo2"), (
        "Long-format TelemetryDataModel must not need a dedicated spo2 column"
    )


def test_ingest_row_carries_canonical_and_provenance_measurements():
    from app.models.biomarker_model import BiomarkerDefinition, Unit
    from app.services.telemetry.telemetry_ingest import build_telemetry_row

    definition = BiomarkerDefinition(slug="body-temperature", name="Body Temperature")
    definition.preferred_unit = Unit(symbol="°C", name="Celsius")

    row = build_telemetry_row(
        tenant_id=TENANT,
        patient_id=PATIENT,
        device_id=uuid.uuid4(),
        timestamp=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        definition=definition,
        value=98.6,
        unit="°F",
    )
    assert round(row["value"], 4) == 37.0
    assert row["unit"] == "°C"
    assert row["raw_value"] == 98.6
    assert row["raw_unit"] == "°F"


def test_analytics_sql_uses_slug_filter_for_arbitrary_biomarker():
    """The OHLC SQL template must filter ``slug = :slug`` so that spo2 (or
    any future telemetry biomarker) is queryable with no code change."""
    from app.services.analytics import trends

    src = inspect.getsource(trends)
    assert "slug = :slug" in src, (
        "Analytics SQL must filter via slug = :slug — required for spo2 "
        "(and any future telemetry biomarker) to be queryable with no code change"
    )
    assert "heart_rate IS NOT NULL" not in src
    assert "\"data ? '{slug}'" not in src
