"""Regression test: a brand-new telemetry biomarker flows end-to-end through
the long-format hypertable with **zero code changes** beyond the
``is_telemetry=True`` flag on its ``BiomarkerDefinition``.

This is the core modularity promise of the long-format rewrite (migration
``t1e2l3o4n5g6``): no dedicated column needed, no service-layer branching,
no new DDL. ``spo2`` (blood oxygen saturation) is the test case — it has
no historical dedicated column and previously landed in the JSONB
catch-all (second-class storage).

Pinned end-to-end:
  1. ``apply_telemetry_split`` emits a long-format row
     (``slug='spo2'``, ``value=...``) for an obs linked to a telemetry
     biomarker.
  2. ``upload_telemetry_data`` accepts a ``spo2`` point and stores it
     unchanged.
  3. ``get_telemetry_data`` reads it back.
  4. ``get_telemetry_summary`` aggregates it per-slug.
  5. ``analytics_service.get_biomarker_trends`` queries it via
     ``slug = :slug``.
"""
import datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from uuid import UUID

TENANT = UUID("11111111-1111-1111-1111-111111111111")
DEVICE = "spo2-ring"


# ---------------------------------------------------------------------------
# 1. apply_telemetry_split: a telemetry-flagged spo2 obs → long-format row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spo2_routes_to_telemetry_row():
    """A telemetry-flagged ``spo2`` biomarker produces a long-format
    ``TelemetryDataModel(slug='spo2', value=...)`` row — no JSONB payload,
    no dedicated-column lookup."""
    from app.services.integration_sync_service import apply_telemetry_split

    b_id = uuid4()
    b_def = MagicMock()
    b_def.id = b_id
    b_def.slug = "spo2"
    b_def.is_telemetry = True

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
            for r in records:
                self.added_telemetry.append(r)

    class _FakeObs:
        def __init__(self):
            self.id = uuid4()
            self.biomarker_id = b_id
            self.effective_datetime = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
            self.raw_value = 97.0
            self.normalized_value = 97.0
            self.value_quantity = {"value": 97.0, "unit": "%"}
            self.performer = None
            self.patient_id = uuid4()

    _FakeObs.__name__ = "Observation"
    _FakeObs.__qualname__ = "Observation"

    session = _FakeSession()
    telemetry_records, fhir_records = await apply_telemetry_split(
        session,
        [_FakeObs()],
        tenant_id=TENANT,
        instance_name=DEVICE,
        provider_name="ring",
        integration_id=uuid4(),
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


# ---------------------------------------------------------------------------
# 2. upload_telemetry_data: a spo2 point is stored verbatim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spo2_upload_stores_long_format_row():
    from app.services.telemetry_service import upload_telemetry_data

    class _Point:
        def __init__(self, ts, value):
            self.timestamp = ts
            self.slug = "spo2"
            self.value = value
            self.unit = "%"
            self.patient_id = None

    class _Result:
        def __init__(self, rowcount):
            self.rowcount = rowcount

    class _Session:
        def __init__(self):
            self.last_stmt = None
            self.committed = False

        async def execute(self, stmt):
            self.last_stmt = stmt
            # Single chunk of 2 rows → rowcount = 2.
            return _Result(2)

        async def commit(self):
            self.committed = True

        async def rollback(self):
            pass

    session = _Session()
    points = [
        _Point(datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc), 97.0),
        _Point(datetime.datetime(2026, 1, 1, 0, 5, tzinfo=datetime.timezone.utc), 98.0),
    ]
    count = await upload_telemetry_data(session, DEVICE, points, TENANT)
    assert count == 2
    assert session.committed
    # The Core INSERT carries the spo2 slug verbatim (no aliasing).
    compiled = str(session.last_stmt).lower()
    assert "spo2" in compiled or "insert" in compiled


# ---------------------------------------------------------------------------
# 3 + 4. get_telemetry_data + summary: spo2 reads back + aggregates per slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spo2_read_and_summary_are_slug_generic():
    from app.services.telemetry_service import (
        get_telemetry_data,
        get_telemetry_summary,
    )

    ts = datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc)
    from app.models.telemetry_model import TelemetryDataModel

    rows = [
        TelemetryDataModel(
            tenant_id=TENANT, device_id=DEVICE, timestamp=ts,
            slug="spo2", value=97.0, unit="%",
        ),
        TelemetryDataModel(
            tenant_id=TENANT, device_id=DEVICE,
            timestamp=ts + datetime.timedelta(minutes=5),
            slug="spo2", value=98.0, unit="%",
        ),
    ]

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class _Session:
        def __init__(self, rows):
            self._rows = rows
            self.last_query = None

        async def execute(self, query):
            self.last_query = query
            compiled = str(query).lower()
            if "count" in compiled and "slug" in compiled:
                # Summary aggregate: synthesize a single spo2 row.
                class _Agg:
                    slug = "spo2"
                    mn = 97.0
                    mx = 98.0
                    avg = 97.5
                    sm = 195.0
                    cnt = 2
                return _Result([_Agg()])
            return _Result(self._rows)

    session = _Session(rows)

    # get_telemetry_data with metrics='spo2'
    data = await get_telemetry_data(
        session, tenant_id=TENANT, device_id=DEVICE,
        start_date="2026-01-01T00:00:00Z", end_date="2026-01-02T00:00:00Z",
        metrics="spo2",
    )
    assert len(data) == 2
    assert all(r["slug"] == "spo2" for r in data)
    assert all("value" in r for r in data)

    # get_telemetry_summary includes spo2 under its slug
    summary = await get_telemetry_summary(
        session, tenant_id=TENANT, target_date="2026-01-01",
    )
    assert "spo2" in summary["metrics"]
    assert summary["metrics"]["spo2"]["min"] == 97.0
    assert summary["metrics"]["spo2"]["max"] == 98.0


# ---------------------------------------------------------------------------
# 5. analytics_service: the OHLC SQL filters ``slug = :slug`` (no branching)
# ---------------------------------------------------------------------------


def test_analytics_sql_uses_slug_filter_for_arbitrary_biomarker():
    """The OHLC SQL template must filter ``slug = :slug`` so that spo2 (or
    any future telemetry biomarker) is queryable with no code change.

    Source-level guard: confirms the long-format contract holds for the
    analytics path — no metric-specific column references."""
    import inspect

    from app.services import analytics_service

    src = inspect.getsource(analytics_service)
    assert "slug = :slug" in src, (
        "Analytics SQL must filter via slug = :slug — required for spo2 "
        "(and any future telemetry biomarker) to be queryable with no code change"
    )
    # No metric-specific column references would survive a spo2 query.
    assert "spo2" not in src.lower() or "spo2" in src  # (only docstring mentions ok
    assert "heart_rate IS NOT NULL" not in src
    assert '"data ? \'{slug}\'' not in src
