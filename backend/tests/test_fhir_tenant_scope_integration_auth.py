"""Tests for audit items B5 and B16 (endpoint/service security).

B5:  ``get_observation`` / ``get_diagnostic_report`` / ``get_medication``
     took only a resource id and did no tenant filtering — a cross-tenant
     read was possible if an endpoint forgot to verify ownership. The
     single-resource reads now accept an optional ``tenant_id`` and filter
     on it; the ``/fhir/*/{id}`` endpoints pass ``current_user.tenant_id``.

B16: ``GET /integrations/available`` and ``GET /integrations/{domain}/documentation``
     had no authentication — anonymous callers could enumerate enabled
     integrations and read their docs.
"""
import inspect
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest


TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockUser:
    def __init__(self, tenant_id=TENANT_A, user_id=None, role="USER"):
        self.tenant_id = tenant_id
        self.user_id = user_id or uuid4()
        self.role = role
        self.sub = "test"


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
# B5: service-level tenant scoping
# ---------------------------------------------------------------------------


def test_b5_service_signatures_accept_tenant_id():
    """B5: each single-resource service fn must accept an optional tenant_id."""
    from app.services import fhir_service

    for fn_name in ("get_observation", "get_diagnostic_report", "get_medication", "delete_observation"):
        fn = getattr(fhir_service, fn_name)
        sig = inspect.signature(fn)
        assert "tenant_id" in sig.parameters, (
            f"{fn_name} must accept tenant_id for tenant-scoped reads."
        )
        # And it must be optional (default None) so legacy internal callers
        # that have already verified access are not broken.
        assert sig.parameters["tenant_id"].default is None, (
            f"{fn_name}.tenant_id must default to None to preserve legacy callers."
        )


@pytest.mark.asyncio
async def test_b5_get_observation_filters_by_tenant():
    """B5: when tenant_id is supplied, the WHERE clause restricts to that tenant."""
    from app.services import fhir_service

    captured = {}

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            captured["stmt"] = stmt
            return FakeResult()

    with patch.object(fhir_service, "AsyncSessionLocal", return_value=FakeSession()), \
         patch.object(fhir_service, "DATABASE_AVAILABLE", True):
        await fhir_service.get_observation(uuid4(), tenant_id=TENANT_A)

    # The compiled WHERE clause must reference tenant_id (not just the SELECT col list).
    compiled = str(captured["stmt"].compile())
    where_clause = compiled.split("WHERE", 1)[-1] if "WHERE" in compiled else ""
    assert "tenant_id" in where_clause, (
        "get_observation did not add a tenant_id predicate in WHERE when one was supplied."
    )


@pytest.mark.asyncio
async def test_b5_get_observation_without_tenant_is_unscoped():
    """B5: legacy callers passing no tenant_id get the unscoped behaviour."""
    from app.services import fhir_service

    captured = {}

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            captured["stmt"] = stmt
            return FakeResult()

    with patch.object(fhir_service, "AsyncSessionLocal", return_value=FakeSession()), \
         patch.object(fhir_service, "DATABASE_AVAILABLE", True):
        await fhir_service.get_observation(uuid4())

    compiled = str(captured["stmt"].compile())
    where_clause = compiled.split("WHERE", 1)[-1] if "WHERE" in compiled else ""
    # Legacy path: only the id predicate in WHERE, no tenant filter.
    assert "tenant_id" not in where_clause, (
        "get_observation with no tenant_id unexpectedly added a tenant filter in WHERE."
    )


# ---------------------------------------------------------------------------
# B5: endpoints pass tenant_id through
# ---------------------------------------------------------------------------


def test_b5_endpoints_pass_tenant_id_to_service():
    """B5 regression: the observations endpoint module must thread
    ``current_user.tenant_id`` into the service call."""
    src = inspect.getsource(__import__("app.api.v1.endpoints.observations", fromlist=["x"]))

    for fn_name, service_call in (
        ("get_observation_endpoint", "get_observation(observation_id, current_user.tenant_id)"),
        ("delete_observation_endpoint", "get_observation(observation_id, current_user.tenant_id)"),
    ):
        assert f"async def {fn_name}(" in src, f"{fn_name} missing from observations.py?"
        assert service_call in src, (
            f"observations.py must call {service_call!r} — found unscoped call."
        )


@pytest.mark.asyncio
async def test_b5_get_observation_endpoint_404s_on_cross_tenant(async_client):
    """B5: an observation owned by tenant B returns 404 to a tenant-A user
    (never leaks the row). The service-level scope means the endpoint sees
    None and raises 404 rather than returning the cross-tenant row."""
    _override_user(MockUser(tenant_id=TENANT_A, role="ADMIN"))
    try:
        with patch(
            "app.api.v1.endpoints.observations.get_observation",
            new=AsyncMock(return_value=None),  # service returns None for cross-tenant
        ):
            response = await async_client.get(f"/api/v1/observations/{uuid4()}")
        assert response.status_code == 404, response.text
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_b5_get_medication_endpoint_404s_on_cross_tenant(async_client):
    """B5: medication cross-tenant read returns 404 (via check_medication_access)."""
    from fastapi import HTTPException

    _override_user(MockUser(tenant_id=TENANT_A, role="ADMIN"))
    try:
        with patch(
            "app.api.v1.endpoints.medications.check_medication_access",
            new=AsyncMock(side_effect=HTTPException(status_code=404, detail="Medication record not found")),
        ):
            response = await async_client.get(f"/api/v1/medications/{uuid4()}")
        assert response.status_code == 404
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_b5_delete_observation_endpoint_404s_on_cross_tenant(async_client):
    """B5: a cross-tenant delete sees None (service-scoped) → 404, not 403."""
    _override_user(MockUser(tenant_id=TENANT_A, role="ADMIN"))
    try:
        with patch(
            "app.api.v1.endpoints.observations.get_observation",
            new=AsyncMock(return_value=None),
        ):
            response = await async_client.delete(f"/api/v1/observations/{uuid4()}")
        assert response.status_code == 404
    finally:
        _clear_overrides()


# ---------------------------------------------------------------------------
# B16: auth on integration listing + documentation endpoints
# ---------------------------------------------------------------------------


def test_b16_list_available_requires_auth_dependency():
    """B16: list_available_integrations must depend on get_current_user."""
    src = inspect.getsource(__import__("app.api.v1.endpoints.integrations", fromlist=["x"]))
    # Find the list_available_integrations body and confirm it declares the dep.
    idx = src.index("async def list_available_integrations(")
    body = src[idx : idx + 800]
    assert "get_current_user" in body, (
        "list_available_integrations must depend on get_current_user."
    )


def test_b16_documentation_requires_auth_dependency():
    """B16: get_integration_documentation must depend on get_current_user."""
    src = inspect.getsource(__import__("app.api.v1.endpoints.integrations", fromlist=["x"]))
    idx = src.index("async def get_integration_documentation(")
    body = src[idx : idx + 800]
    assert "get_current_user" in body, (
        "get_integration_documentation must depend on get_current_user."
    )


@pytest.mark.asyncio
async def test_b16_list_available_rejects_anonymous(async_client):
    """B16: an unauthenticated request to /available must not be 200."""
    # Do NOT register a user override — real get_current_user runs.
    response = await async_client.get("/api/v1/integrations/available")
    # 401 (no token) or 403 — either is acceptable; 200 would be the bug.
    assert response.status_code in (401, 403), (
        f"Anonymous access to /integrations/available returned {response.status_code} "
        "."
    )


@pytest.mark.asyncio
async def test_b16_documentation_rejects_anonymous(async_client):
    """B16: an unauthenticated request to /documentation must not be 200."""
    response = await async_client.get("/api/v1/integrations/dev_dummy/documentation")
    assert response.status_code in (401, 403), (
        f"Anonymous access to /integrations/documentation returned {response.status_code} "
        "."
    )


@pytest.mark.asyncio
async def test_b16_list_available_accepts_authenticated(async_client):
    """B16: an authenticated user (any role) can still hit /available."""
    _override_user(MockUser(role="USER"))
    try:
        # Patch the DB lookup so we don't need a real DB.
        fake_db = MagicMock()
        fake_db.execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
        )
        with patch("app.api.v1.endpoints.integrations.get_db", return_value=iter([fake_db])):
            with patch(
                "app.api.v1.endpoints.integrations.integration_registry.get_all_manifests",
                return_value=[],
            ):
                response = await async_client.get("/api/v1/integrations/available")
        assert response.status_code == 200, response.text
    finally:
        _clear_overrides()
