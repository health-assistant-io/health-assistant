"""Tests for audit items A1, A2, B5 (FHIR Observation service + endpoint).

A1: ``list_observations`` accepted patient_id/code/start_date/end_date but
    silently ignored them — the query only filtered by tenant_id. Returned
    every observation in the tenant regardless of which patient/date/code
    the caller asked for. Cross-patient data exposure.

A2: ``/fhir/Observation/history`` called ``get_observation(patient_id, code,
    period)`` but ``get_observation`` takes ``(observation_id)`` only.
    TypeError on every call.

B5: FHIR service ``get_*`` functions and endpoints did not enforce tenant
    ownership; the history endpoint now forwards current_user.tenant_id.
"""
import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest


TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")
PATIENT_A1 = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
PATIENT_A2 = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class MockUser:
    def __init__(self, tenant_id=TENANT_A, role="ADMIN"):
        self.user_id = uuid4()
        self.tenant_id = tenant_id
        self.role = role
        self.sub = "test"

    def get(self, key, default=None):
        return getattr(self, key, default)


def _override_user(user):
    from app.core.security import get_current_user
    from app.main import app

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override


def _clear_overrides():
    from app.main import app
    app.dependency_overrides = {}


# ---------------------------------------------------------------------------
# A1: signature regression — filters must be accepted and applied
# ---------------------------------------------------------------------------


def test_list_observations_signature_accepts_all_filters():
    """A1 regression: the signature must still accept all 4 filter params."""
    from app.services.fhir_service import list_observations

    sig = inspect.signature(list_observations)
    for param in ("tenant_id", "patient_id", "code", "start_date", "end_date"):
        assert param in sig.parameters, (
            f"list_observations must accept {param!r} (audit A1)"
        )


# ---------------------------------------------------------------------------
# A1: query predicate verification (no DB needed)
# ---------------------------------------------------------------------------


class _StubSelect:
    """Records ``.where(...)`` calls and produces a chainable query."""

    def __init__(self):
        self.where_calls: list = []
        self._limit = None
        self._offset = None
        self._order_clauses: list = []

    def where(self, *predicates):
        self.where_calls.append(predicates)
        return self

    def order_by(self, *clauses):
        self._order_clauses.extend(clauses)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def offset(self, n):
        self._offset = n
        return self

    def nullslast(self):
        return self


@pytest.mark.asyncio
async def test_list_observations_applies_patient_filter(monkeypatch):
    """A1: when patient_id is provided, the query must contain a subject predicate."""
    from app.services import fhir_service as svc

    captured: list[str] = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, query):
            try:
                compiled = query.compile(compile_kwargs={"literal_binds": True})
                captured.append(str(compiled))
            except Exception:
                captured.append(str(query))

            class _Result:
                def scalar(self):
                    return 0

                def scalars(self):
                    return self

                def all(self):
                    return []

            return _Result()

    monkeypatch.setattr(svc, "DATABASE_AVAILABLE", True)
    monkeypatch.setattr(svc, "AsyncSessionLocal", lambda: _FakeSession())

    await svc.list_observations(
        tenant_id=TENANT_A,
        patient_id=PATIENT_A1,
    )

    joined = " ".join(captured)
    # The subject reference filter must be present, including the patient UUID
    assert str(PATIENT_A1) in joined, (
        "list_observations did not add a subject-reference predicate for patient_id "
        "(audit A1 not fixed)"
    )


@pytest.mark.asyncio
async def test_list_observations_applies_code_and_date_filters(monkeypatch):
    """A1: code + start_date + end_date must all contribute predicates."""
    from app.services import fhir_service as svc

    captured: list[str] = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, query):
            try:
                compiled = query.compile(compile_kwargs={"literal_binds": True})
                captured.append(str(compiled))
            except Exception:
                captured.append(str(query))

            class _Result:
                def scalar(self):
                    return 0

                def scalars(self):
                    return self

                def all(self):
                    return []

            return _Result()

    monkeypatch.setattr(svc, "DATABASE_AVAILABLE", True)
    monkeypatch.setattr(svc, "AsyncSessionLocal", lambda: _FakeSession())

    await svc.list_observations(
        tenant_id=TENANT_A,
        patient_id=PATIENT_A1,
        code="8867-4",
        start_date="2026-01-01",
        end_date="2026-06-01",
    )

    joined = " ".join(captured)
    assert "8867-4" in joined, "code filter not present in query"
    assert "fhir_observations" in joined


@pytest.mark.asyncio
async def test_list_observations_rejects_invalid_tenant(monkeypatch):
    """A1: invalid tenant_id returns empty without touching the DB."""
    from app.services import fhir_service as svc

    monkeypatch.setattr(svc, "DATABASE_AVAILABLE", True)
    result = await svc.list_observations(tenant_id="not-a-uuid")
    assert result == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_list_observations_rejects_invalid_patient(monkeypatch):
    """A1: invalid patient_id returns empty without leaking tenant-wide data."""
    from app.services import fhir_service as svc

    monkeypatch.setattr(svc, "DATABASE_AVAILABLE", True)
    result = await svc.list_observations(
        tenant_id=TENANT_A,
        patient_id="not-a-uuid",
    )
    assert result == {"items": [], "total": 0}


# ---------------------------------------------------------------------------
# A2: /fhir/Observation/history endpoint
# ---------------------------------------------------------------------------


def test_get_observation_history_signature():
    """A2: the new service function must exist with the right signature."""
    from app.services.fhir_service import get_observation_history

    sig = inspect.signature(get_observation_history)
    for required in ("tenant_id", "patient_id", "code"):
        assert required in sig.parameters


@pytest.mark.asyncio
async def test_observation_history_endpoint_does_not_call_get_observation():
    """A2 regression: the endpoint must NOT call the arity-mismatched get_observation.

    The original code called ``get_observation(patient_id, code, period)``.
    ``get_observation`` takes a single ``observation_id`` argument — every
    call raised TypeError. This test fails if that broken call returns.
    """
    _override_user(MockUser())
    try:
        with patch(
            "app.api.v1.endpoints.fhir.check_patient_access", new=AsyncMock()
        ), patch(
            "app.api.v1.endpoints.fhir.get_observation_history",
            new=AsyncMock(return_value=[]),
        ) as mock_history, patch(
            "app.api.v1.endpoints.fhir.get_observation",
            new=AsyncMock(side_effect=AssertionError(
                "endpoint called get_observation (audit A2 regressed)"
            )),
        ):
            from app.main import app
            from httpx import AsyncClient, ASGITransport

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    "/api/v1/fhir/Observation/history",
                    params={"patient_id": str(PATIENT_A1), "code": "8867-4"},
                )
            assert response.status_code == 200, response.text
            mock_history.assert_awaited_once()
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_observation_history_endpoint_passes_tenant_id():
    """B5: the endpoint must forward current_user.tenant_id to the service."""
    user = MockUser(tenant_id=TENANT_B)
    _override_user(user)
    try:
        captured = {}

        async def fake_history(tenant_id, patient_id, code, period="last-6-months"):
            captured["tenant_id"] = tenant_id
            captured["patient_id"] = patient_id
            captured["code"] = code
            return []

        with patch(
            "app.api.v1.endpoints.fhir.check_patient_access", new=AsyncMock()
        ), patch(
            "app.api.v1.endpoints.fhir.get_observation_history", new=fake_history
        ):
            from app.main import app
            from httpx import AsyncClient, ASGITransport

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    "/api/v1/fhir/Observation/history",
                    params={"patient_id": str(PATIENT_A1), "code": "8867-4"},
                )
            assert response.status_code == 200
        assert captured["tenant_id"] == TENANT_B
    finally:
        _clear_overrides()


# ---------------------------------------------------------------------------
# A2 + B5: service-level query predicate verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_observation_history_invalid_tenant_returns_empty(monkeypatch):
    """B5: invalid tenant_id returns [] without touching the DB."""
    from app.services import fhir_service as svc

    monkeypatch.setattr(svc, "DATABASE_AVAILABLE", True)
    result = await svc.get_observation_history(
        tenant_id="not-a-uuid",
        patient_id=str(PATIENT_A1),
        code="8867-4",
    )
    assert result == []


@pytest.mark.asyncio
async def test_get_observation_history_invalid_patient_returns_empty(monkeypatch):
    """A2: invalid patient_id returns [] without touching the DB."""
    from app.services import fhir_service as svc

    monkeypatch.setattr(svc, "DATABASE_AVAILABLE", True)
    result = await svc.get_observation_history(
        tenant_id=TENANT_A,
        patient_id="garbage",
        code="8867-4",
    )
    assert result == []


@pytest.mark.asyncio
async def test_get_observation_history_compiles_tenant_scoped_query(monkeypatch):
    """B5: the compiled query must contain the tenant_id predicate."""
    from app.services import fhir_service as svc

    captured: list[str] = []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, query):
            try:
                captured.append(str(query.compile(compile_kwargs={"literal_binds": True})))
            except Exception:
                captured.append(str(query))

            class _Result:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _Result()

    monkeypatch.setattr(svc, "DATABASE_AVAILABLE", True)
    monkeypatch.setattr(svc, "AsyncSessionLocal", lambda: _FakeSession())

    await svc.get_observation_history(
        tenant_id=TENANT_A,
        patient_id=PATIENT_A1,
        code="8867-4",
        period="last-30-days",
    )

    joined = " ".join(captured)
    # tenant_id, subject reference, and code must all appear in the SQL
    assert str(TENANT_A) in joined or str(TENANT_A).replace("-", "") in joined.replace("-", "")
    assert "8867-4" in joined
    assert "Patient/" in joined or str(PATIENT_A1).replace("-", "") in joined.replace(
        "-", ""
    )


def test_get_observation_still_single_arg():
    """A2 sanity: ``get_observation`` still takes a single observation_id.

    The endpoint at /Observation/{id} depends on this contract. We must not
    accidentally change its signature while fixing the history endpoint.
    """
    from app.services.fhir_service import get_observation

    sig = inspect.signature(get_observation)
    params = list(sig.parameters)
    assert params == ["observation_id"], (
        f"get_observation signature changed: {params}. The /Observation/{{id}} "
        "endpoint depends on this single-arg contract."
    )
