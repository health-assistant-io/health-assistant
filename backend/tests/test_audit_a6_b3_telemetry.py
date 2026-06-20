"""Tests for audit items A6, B3, F8 (telemetry endpoints/service).

A6: ``/telemetry/anomalies`` previously called
    ``await detector.detect_biomarker_anomalies(device_id, metric, period)``
    but ``AnomalyDetector.detect_biomarker_anomalies`` is synchronous and
    takes ``(historical_values, new_value)``. Every call raised TypeError.

B3: ``/telemetry/data``, ``/data/summary``, ``/anomalies`` took only
    ``device_id`` — no tenant_id filter. A user who guessed/enumerated
    another tenant's device_id could read its telemetry.

F8: ``telemetry_service.get_telemetry_data`` and ``.get_telemetry_summary``
    were stubs returning ``[]`` / a zero dict.
"""
import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
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
    """AnomalyDetector.detect_biomarker_anomalies must remain synchronous.

    Audit A6 was caused by an ``await`` on this method. Regression guard.
    """
    from app.services.anomaly_detector import AnomalyDetector

    assert not inspect.iscoroutinefunction(
        AnomalyDetector.detect_biomarker_anomalies
    ), (
        "detect_biomarker_anomalies must NOT be async — endpoint uses it "
        "synchronously via get_telemetry_anomalies"
    )


def test_get_telemetry_anomalies_is_async_and_takes_tenant():
    """The new wrapper must be async, return a list, and require tenant_id."""
    from app.services.telemetry_service import get_telemetry_anomalies

    sig = inspect.signature(get_telemetry_anomalies)
    assert inspect.iscoroutinefunction(get_telemetry_anomalies)
    for required in ("db", "tenant_id", "device_id", "metric"):
        assert required in sig.parameters, (
            f"get_telemetry_anomalies must accept {required!r} (audit A6+B3)"
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
                params={"device_id": DEVICE_X, "metric": "heart_rate"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["device_id"] == DEVICE_X
        assert body["metric"] == "heart_rate"
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
                params={"device_id": DEVICE_X, "metric": "heart_rate"},
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

        async def fake_summary(db, tenant_id, target_date, device_id=None):
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
# F8: service-level tests with a fake session (no longer stubs)
# ---------------------------------------------------------------------------


class _Scalar:
    """Mimics sqlalchemy Row.scalar() — returns first column."""

    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


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

    def __init__(self, rows=None, aggregate_row=None):
        self._rows = rows or []
        self._aggregate_row = aggregate_row
        self.last_query = None
        self.added: list = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, query):
        self.last_query = query
        # Heuristic: aggregate queries (min/max/sum) use .one()
        compiled = str(query)
        if "min(" in compiled and "max(" in compiled:
            return _FakeResult(one_row=self._aggregate_row)
        return _FakeResult(rows=self._rows)

    def add_all(self, records):
        self.added.extend(records)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _make_telemetry_row(tenant_id, device_id, ts, hr=None, steps=None, cal=None):
    from app.models.telemetry_model import TelemetryDataModel

    row = TelemetryDataModel(
        tenant_id=tenant_id,
        device_id=device_id,
        timestamp=ts,
        heart_rate=hr,
        steps=steps,
        calories=cal,
    )
    return row


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
    assert session.last_query is None  # never queried


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
async def test_get_telemetry_data_metric_filter():
    """The ``metrics`` parameter filters the returned JSONB ``data`` dict."""
    from app.services.telemetry_service import get_telemetry_data

    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = _make_telemetry_row(
        TENANT_A, DEVICE_X, ts, hr=70,
        # ``data`` JSONB carries multiple metrics
    )
    row.data = {"heart_rate": 70, "stress": 5, "resp_rate": 16}
    session = FakeAsyncSession(rows=[row])

    result = await get_telemetry_data(
        session,
        tenant_id=TENANT_A,
        device_id=DEVICE_X,
        start_date="2026-01-01T00:00:00Z",
        end_date="2026-01-02T00:00:00Z",
        metrics="heart_rate,stress",
    )
    assert len(result) == 1
    # Only the requested metrics survive
    assert set(result[0]["data"].keys()) == {"heart_rate", "stress"}


@pytest.mark.asyncio
async def test_get_telemetry_summary_aggregates():
    """F8: summary runs a real aggregate query and returns computed stats."""
    from app.services.telemetry_service import get_telemetry_summary

    # Mock aggregate row returned by SELECT min/max/avg/sum
    class _Agg:
        hr_min = 60.0
        hr_max = 90.0
        hr_avg = 75.0
        steps_sum = 5400
        cal_sum = 1800.0

    session = FakeAsyncSession(aggregate_row=_Agg())
    summary = await get_telemetry_summary(
        session,
        tenant_id=TENANT_A,
        target_date="2026-01-01",
    )
    assert summary["steps"] == 5400
    assert summary["calories"] == 1800.0
    assert summary["heart_rate"] == {"min": 60.0, "max": 90.0, "avg": 75.0}


# ---------------------------------------------------------------------------
# A6: AnomalyDetector integration — the wrapper feeds real historical data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_telemetry_anomalies_invalid_metric_returns_empty():
    """An unknown metric alias returns [] instead of erroring."""
    from app.services.telemetry_service import get_telemetry_anomalies

    session = FakeAsyncSession(rows=[])
    result = await get_telemetry_anomalies(
        session, tenant_id=TENANT_A, device_id=DEVICE_X, metric="unknown_metric"
    )
    assert result == []


@pytest.mark.asyncio
async def test_get_telemetry_anomalies_invokes_detector_correctly():
    """A6: the wrapper must call the sync detector with (historical, new).

    Confirms the previous broken pattern (await + wrong arity) is gone.
    """
    from app.services import telemetry_service as svc

    # Two rows: detector needs len>=2 (1 historical + 1 new)
    ts1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ts2 = datetime(2026, 1, 2, tzinfo=timezone.utc)

    # The anomalies query selects (timestamp, column) tuples, not full rows
    fake_rows = [(ts1, 70.0), (ts2, 195.0)]
    session = FakeAsyncSession(rows=fake_rows)

    captured = {}

    def fake_detect(self, historical, new_value):
        captured["historical"] = historical
        captured["new_value"] = new_value
        return [{"type": "statistical_anomaly", "severity": "critical"}]

    with patch.object(svc.AnomalyDetector, "detect_biomarker_anomalies", fake_detect):
        result = await svc.get_telemetry_anomalies(
            session, tenant_id=TENANT_A, device_id=DEVICE_X, metric="heart_rate"
        )

    assert result == [{"type": "statistical_anomaly", "severity": "critical"}]
    # The wrapper correctly used all-but-last as historical, last as new
    assert captured["historical"] == [{"value": 70.0}]
    assert captured["new_value"] == {"value": 195.0}


@pytest.mark.asyncio
async def test_get_telemetry_anomalies_is_tenant_scoped():
    """B3: the query for anomaly history must filter by tenant_id."""
    from app.services.telemetry_service import get_telemetry_anomalies

    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = FakeAsyncSession(rows=[(ts, 70.0), (ts, 72.0)])
    await get_telemetry_anomalies(
        session, tenant_id=TENANT_B, device_id=DEVICE_Y, metric="heart_rate"
    )
    sql = str(session.last_query)
    assert "tenant_id" in sql.lower()


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
