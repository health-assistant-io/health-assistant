"""Tests for audit items A6, B3, F8 (telemetry endpoints/service) —
long-format hypertable edition.

A6: ``/telemetry/anomalies`` previously called
    ``await detector.detect_biomarker_anomalies(device_id, metric, period)``
    but ``AnomalyDetector.detect_biomarker_anomalies`` is synchronous and
    takes ``(historical_values, new_value)``. Every call raised TypeError.

B3: ``/telemetry/data``, ``/data/summary``, ``/anomalies`` took only
    ``device_id`` — no tenant_id filter. A user who guessed/enumerated
    another tenant's device_id could read its telemetry.

F8: ``telemetry_service.get_telemetry_data`` and ``.get_telemetry_summary``
    were stubs returning ``[]`` / a zero dict.

Long-format rewrite (migration ``t1e2l3o4n5g6``): the service queries
``WHERE slug = :slug`` directly — no ``_METRIC_COLUMNS`` alias map, no
column-attribute lookup. The endpoint ``metric`` parameter is now treated
as a biomarker slug.
"""
import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest


TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")
DEVICE_X = "device-xxx"
DEVICE_Y = "device-yyy"


class MockUser:
    def __init__(self, tenant_id, role="USER"):
        self.user_id = uuid4()
        self.tenant_id = tenant_id
        self.role = role
        self.sub = "test"

    def get(self, key, default=None):
        return getattr(self, key, default)


@pytest.fixture
def tenant_a_user():
    return MockUser(TENANT_A)


@pytest.fixture
def tenant_b_user():
    return MockUser(TENANT_B)


def _override_user(user):
    from app.core.security import get_current_user
    from app.main import app

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override
    return _override


def _clear_overrides():
    from app.main import app
    app.dependency_overrides = {}


# ---------------------------------------------------------------------------
# A6: signature regression test
# ---------------------------------------------------------------------------


def test_anomaly_detector_method_is_sync():
    """AnomalyDetector.detect_biomarker_anomalies must remain synchronous."""
    from app.services.anomaly_detector import AnomalyDetector

    assert not inspect.iscoroutinefunction(
        AnomalyDetector.detect_biomarker_anomalies
    ), (
        "detect_biomarker_anomalies must NOT be async — endpoint uses it "
        "synchronously via get_telemetry_anomalies"
    )


def test_get_telemetry_anomalies_is_async_and_takes_tenant():
    """The wrapper must be async, return a list, and require tenant_id."""
    from app.services.telemetry_service import get_telemetry_anomalies

    sig = inspect.signature(get_telemetry_anomalies)
    assert inspect.iscoroutinefunction(get_telemetry_anomalies)
    for required in ("db", "tenant_id", "device_id", "metric"):
        assert required in sig.parameters, (
            f"get_telemetry_anomalies must accept {required!r}"
        )


@pytest.mark.asyncio
async def test_anomalies_endpoint_does_not_crash(tenant_a_user, async_client):
    """A6 regression: the endpoint must not raise TypeError on every call."""
    _override_user(tenant_a_user)
    try:
        with patch(
            "app.api.v1.endpoints.telemetry.get_telemetry_anomalies",
            new=AsyncMock(return_value=[]),
        ):
            response = await async_client.get(
                "/api/v1/telemetry/anomalies",
                params={"device_id": DEVICE_X, "metric": "heart-rate"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["device_id"] == DEVICE_X
        assert body["metric"] == "heart-rate"
        assert body["anomalies"] == []
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_anomalies_endpoint_passes_tenant_id(tenant_a_user, async_client):
    """B3: endpoint must forward current_user.tenant_id to the service."""
    _override_user(tenant_a_user)
    try:
        captured = {}

        async def fake_anomalies(db, tenant_id, device_id, metric, period_days=30):
            captured["tenant_id"] = tenant_id
            captured["device_id"] = device_id
            return []

        with patch(
            "app.api.v1.endpoints.telemetry.get_telemetry_anomalies",
            new=fake_anomalies,
        ):
            await async_client.get(
                "/api/v1/telemetry/anomalies",
                params={"device_id": DEVICE_X, "metric": "heart-rate"},
            )
        assert captured["tenant_id"] == TENANT_A, (
            "Telemetry anomalies endpoint did not pass the caller's tenant_id"
        )
    finally:
        _clear_overrides()


# ---------------------------------------------------------------------------
# B3: tenant scoping on /data and /data/summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_data_endpoint_passes_tenant_id(tenant_a_user, async_client):
    _override_user(tenant_a_user)
    try:
        captured = {}

        async def fake_get(db, tenant_id, device_id, start_date, end_date, metrics=None):
            captured.update(
                tenant_id=tenant_id, device_id=device_id
            )
            return []

        with patch(
            "app.api.v1.endpoints.telemetry.get_telemetry_data", new=fake_get
        ):
            await async_client.get(
                "/api/v1/telemetry/data",
                params={
                    "device_id": DEVICE_X,
                    "start_date": "2026-01-01T00:00:00Z",
                    "end_date": "2026-01-02T00:00:00Z",
                },
            )
        assert captured["tenant_id"] == TENANT_A
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_get_summary_endpoint_passes_tenant_id(tenant_a_user, async_client):
    _override_user(tenant_a_user)
    try:
        captured = {}

        async def fake_summary(db, tenant_id, target_date, device_id=None, metrics=None):
            captured.update(tenant_id=tenant_id, device_id=device_id)
            return {"date": target_date}

        with patch(
            "app.api.v1.endpoints.telemetry.get_telemetry_summary", new=fake_summary
        ):
            await async_client.get(
                "/api/v1/telemetry/data/summary",
                params={"date": "2026-01-01"},
            )
        assert captured["tenant_id"] == TENANT_A
    finally:
        _clear_overrides()


# ---------------------------------------------------------------------------
# F8: service-level tests with a fake session (long-format queries)
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows=None, scalar_value=None, one_row=None):
        self._rows = rows or []
        self._scalar = scalar_value
        self._one = one_row

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar

    def one(self):
        return self._one


class FakeAsyncSession:
    """Minimal AsyncSession fake: records the query, returns canned results."""

    def __init__(self, rows=None, aggregate_rows=None):
        self._rows = rows or []
        self._aggregate_rows = aggregate_rows
        self.last_query = None
        self.added: list = []
        self.committed = False
        self.rolled_back = False
        self._rowcount = None

    async def execute(self, query):
        self.last_query = query
        compiled = str(query)
        # Summary aggregates use GROUP BY slug + min/max/avg/sum/count.
        if "group by" in compiled.lower() and "slug" in compiled.lower() and "count" in compiled.lower():
            return _FakeResult(rows=self._aggregate_rows or [])
        return _FakeResult(rows=self._rows)

    def add_all(self, records):
        self.added.extend(records)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _make_telemetry_row(tenant_id, device_id, ts, slug="heart-rate", value=70.0, unit=None):
    from app.models.telemetry_model import TelemetryDataModel

    return TelemetryDataModel(
        tenant_id=tenant_id,
        device_id=device_id,
        timestamp=ts,
        slug=slug,
        value=value,
        unit=unit,
    )


@pytest.mark.asyncio
async def test_get_telemetry_data_filters_by_tenant_and_device():
    """F8+B3: the query must include tenant_id and device_id predicates."""
    from app.services.telemetry_service import get_telemetry_data

    session = FakeAsyncSession(rows=[])
    await get_telemetry_data(
        session,
        tenant_id=TENANT_A,
        device_id=DEVICE_X,
        start_date="2026-01-01T00:00:00Z",
        end_date="2026-01-02T00:00:00Z",
    )
    sql = str(session.last_query)
    assert "tenant_id" in sql.lower()
    assert "device_id" in sql.lower()
    assert "timestamp" in sql.lower()


@pytest.mark.asyncio
async def test_get_telemetry_data_rejects_invalid_tenant():
    """B3: an invalid tenant_id returns an empty list rather than hitting the DB."""
    from app.services.telemetry_service import get_telemetry_data

    session = FakeAsyncSession(rows=[])
    result = await get_telemetry_data(
        session,
        tenant_id="not-a-uuid",
        device_id=DEVICE_X,
        start_date="2026-01-01T00:00:00Z",
        end_date="2026-01-02T00:00:00Z",
    )
    assert result == []
    assert session.last_query is None


@pytest.mark.asyncio
async def test_get_telemetry_data_rejects_bad_dates():
    """F8: bad dates return [] instead of querying."""
    from app.services.telemetry_service import get_telemetry_data

    session = FakeAsyncSession(rows=[])
    result = await get_telemetry_data(
        session,
        tenant_id=TENANT_A,
        device_id=DEVICE_X,
        start_date="garbage",
        end_date="alsogarbage",
    )
    assert result == []
    assert session.last_query is None


@pytest.mark.asyncio
async def test_get_telemetry_data_metrics_filter_uses_slug():
    """Long-format: the ``metrics`` param filters by ``slug IN (...)`` —
    not by JSONB keys."""
    from app.services.telemetry_service import get_telemetry_data

    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row1 = _make_telemetry_row(TENANT_A, DEVICE_X, ts, slug="heart-rate", value=70.0)
    row2 = _make_telemetry_row(TENANT_A, DEVICE_X, ts, slug="stress-level", value=5.0)
    session = FakeAsyncSession(rows=[row1, row2])

    result = await get_telemetry_data(
        session,
        tenant_id=TENANT_A,
        device_id=DEVICE_X,
        start_date="2026-01-01T00:00:00Z",
        end_date="2026-01-02T00:00:00Z",
        metrics="heart-rate",
    )
    sql = str(session.last_query).lower()
    # The filter is now a slug IN (...) predicate against a real column.
    assert "slug" in sql
    # Result serializes via to_dict() (long-format shape).
    assert all("slug" in r and "value" in r for r in result)


@pytest.mark.asyncio
async def test_get_telemetry_summary_aggregates_per_slug():
    """F8: summary groups by slug and returns ``{slug: {min,max,avg,sum,count}}``."""
    from app.services.telemetry_service import get_telemetry_summary

    # Fake aggregated rows — one per slug (the new GROUP BY slug shape).
    class _AggRow:
        def __init__(self, slug, mn, mx, avg, sm, cnt):
            self.slug = slug
            self.mn = mn
            self.mx = mx
            self.avg = avg
            self.sm = sm
            self.cnt = cnt

    aggregate_rows = [
        _AggRow("heart-rate", 60.0, 90.0, 75.0, 0.0, 10),
        _AggRow("steps", 100.0, 5000.0, 2500.0, 5400.0, 3),
    ]
    session = FakeAsyncSession(aggregate_rows=aggregate_rows)

    summary = await get_telemetry_summary(
        session,
        tenant_id=TENANT_A,
        target_date="2026-01-01",
    )
    assert summary["date"] == "2026-01-01"
    metrics = summary["metrics"]
    assert "heart-rate" in metrics
    assert metrics["heart-rate"] == {"min": 60.0, "max": 90.0, "avg": 75.0, "sum": 0.0, "count": 10}
    assert metrics["steps"]["sum"] == 5400.0
    assert metrics["steps"]["count"] == 3


# ---------------------------------------------------------------------------
# A6: AnomalyDetector integration — the wrapper feeds real historical data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_telemetry_anomalies_unknown_slug_returns_empty():
    """A slug with no rows returns [] (no error)."""
    from app.services.telemetry_service import get_telemetry_anomalies

    session = FakeAsyncSession(rows=[])
    result = await get_telemetry_anomalies(
        session, tenant_id=TENANT_A, device_id=DEVICE_X, metric="unknown-slug"
    )
    assert result == []


@pytest.mark.asyncio
async def test_get_telemetry_anomalies_invokes_detector_correctly():
    """A6: the wrapper must call the sync detector with (historical, new)."""
    from app.services import telemetry_service as svc

    ts1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ts2 = datetime(2026, 1, 2, tzinfo=timezone.utc)

    # Long-format: the anomalies query selects (timestamp, value) tuples.
    fake_rows = [(ts1, 70.0), (ts2, 195.0)]
    session = FakeAsyncSession(rows=fake_rows)

    captured = {}

    def fake_detect(self, historical, new_value):
        captured["historical"] = historical
        captured["new_value"] = new_value
        return [{"type": "statistical_anomaly", "severity": "critical"}]

    with patch.object(svc.AnomalyDetector, "detect_biomarker_anomalies", fake_detect):
        result = await svc.get_telemetry_anomalies(
            session, tenant_id=TENANT_A, device_id=DEVICE_X, metric="heart-rate"
        )

    assert result == [{"type": "statistical_anomaly", "severity": "critical"}]
    assert captured["historical"] == [{"value": 70.0}]
    assert captured["new_value"] == {"value": 195.0}


@pytest.mark.asyncio
async def test_get_telemetry_anomalies_is_tenant_scoped():
    """B3: the query for anomaly history must filter by tenant_id."""
    from app.services.telemetry_service import get_telemetry_anomalies

    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = FakeAsyncSession(rows=[(ts, 70.0), (ts, 72.0)])
    await get_telemetry_anomalies(
        session, tenant_id=TENANT_B, device_id=DEVICE_Y, metric="heart-rate"
    )
    sql = str(session.last_query).lower()
    assert "tenant_id" in sql
    # Long-format: query filters WHERE slug = :slug, not column-specific.
    assert "slug" in sql


@pytest.mark.asyncio
async def test_get_telemetry_anomalies_query_filters_by_slug():
    """Long-format contract: the anomalies query must filter ``slug = :slug``
    rather than resolving a column name from an alias map."""
    from app.services.telemetry_service import get_telemetry_anomalies

    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = FakeAsyncSession(rows=[(ts, 70.0), (ts, 72.0)])
    await get_telemetry_anomalies(
        session, tenant_id=TENANT_A, device_id=DEVICE_X, metric="spo2"
    )
    sql = str(session.last_query).lower()
    assert "slug" in sql, "Anomalies query must use the slug column directly"


# ---------------------------------------------------------------------------
# B3 + A6 combined: upload endpoint still works with tenant_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_endpoint_passes_tenant_id(tenant_a_user, async_client):
    _override_user(tenant_a_user)
    try:
        captured = {}

        async def fake_upload(db, device_id, points, tenant_id):
            captured["tenant_id"] = tenant_id
            captured["device_id"] = device_id
            return len(points)

        with patch(
            "app.api.v1.endpoints.telemetry.upload_telemetry_data",
            new=fake_upload,
        ):
            response = await async_client.post(
                "/api/v1/telemetry/data",
                json={"device_id": DEVICE_X, "points": []},
            )
        assert response.status_code == 200
        assert captured["tenant_id"] == TENANT_A
    finally:
        _clear_overrides()


# ---------------------------------------------------------------------------
# Source-level guard: no _METRIC_COLUMNS alias map (long-format contract)
# ---------------------------------------------------------------------------


def test_telemetry_service_has_no_metric_columns_alias_map():
    """Long-format contract: the service must not carry the ``_METRIC_COLUMNS``
    alias map or ``_column_for`` resolver — slugs are queried directly."""
    from app.services import telemetry_service as svc

    assert not hasattr(svc, "_METRIC_COLUMNS"), (
        "_METRIC_COLUMNS alias map must be deleted — long-format queries slugs directly"
    )
    assert not hasattr(svc, "_column_for"), (
        "_column_for resolver must be deleted — long-format queries slugs directly"
    )
