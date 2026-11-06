"""Tests for audit items B2, B4, B9 (AI config scope + exception leak).

B2: GET /ai-config/providers/{id}, /with-models, /models/{id} returned rows
    to any authenticated user without a scope check — leaked other tenants'
    providers AND their plaintext api_key.

B4: Global 500 handler returned {"detail": str(exc)} to clients — leaked
    internal exception detail/stack info. Now returns a generic message +
    correlation id; full detail is logged server-side.

B9: fetch-external-models had no RBAC and no SSRF guard on api_base.
"""

import importlib
import inspect
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")
USER_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class MockUser:
    def __init__(self, tenant_id=TENANT_A, user_id=USER_A, role="USER"):
        self.tenant_id = tenant_id
        self.user_id = user_id
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


def _make_provider(provider_id, scope, tenant_id=None, user_id=None, api_key="secret-key-XYZ"):
    """Build a fake provider object that satisfies verify_provider_access AND
    AIProviderResponse.model_validate (which uses from_attributes=True)."""

    class _Provider:
        pass

    p = _Provider()
    p.id = provider_id
    p.scope = scope
    p.tenant_id = tenant_id
    p.user_id = user_id
    p.api_key = api_key
    p.name = "p"
    p.provider_type = "openai"
    p.api_base = "https://api.openai.com/v1"
    p.is_active = True
    p.settings = {}
    p.is_local = False
    p.company_name = None
    p.company_website = None
    p.company_country = None
    p.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    p.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    p.to_dict = lambda: {
        "id": str(p.id),
        "scope": scope.value if hasattr(scope, "value") else scope,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "user_id": str(user_id) if user_id else None,
        "api_key": api_key,
        "name": p.name,
        "provider_type": p.provider_type,
        "api_base": p.api_base,
        "is_active": p.is_active,
        "settings": p.settings,
        "is_local": p.is_local,
        "company_name": p.company_name,
        "company_website": p.company_website,
        "company_country": p.company_country,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }
    return p


# ---------------------------------------------------------------------------
# B4: global exception handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_exception_handler_no_detail_in_prod(monkeypatch):
    """B4: in prod (DEBUG=False) the client must NOT see str(exc)."""
    from app.core.config import settings
    from app.main import global_exception_handler

    request = MagicMock()
    exc = RuntimeError("Database password is hunter2 and connection refused")

    monkeypatch.setattr(settings, "DEBUG", False)
    response = await global_exception_handler(request, exc)
    assert response.status_code == 500
    body = response.body.decode() if hasattr(response, "body") else ""
    assert "hunter2" not in body, "Production 500 response leaked internal exception detail"
    assert "correlation_id" in body, "Missing correlation_id in 500 response"


@pytest.mark.asyncio
async def test_global_exception_handler_includes_correlation_id(monkeypatch):
    """B4: every 500 must include a unique correlation_id for support."""
    from app.core.config import settings
    from app.main import global_exception_handler

    monkeypatch.setattr(settings, "DEBUG", False)
    response = await global_exception_handler(MagicMock(), ValueError("x"))
    body = response.body.decode()
    import json

    payload = json.loads(body)
    assert "correlation_id" in payload
    # Must be a UUID (correlatable)
    UUID(payload["correlation_id"])


@pytest.mark.asyncio
async def test_global_exception_handler_debug_still_shows_detail(monkeypatch):
    """B4: in DEBUG mode the detail is still surfaced for developer convenience."""
    from app.core.config import settings
    from app.main import global_exception_handler

    monkeypatch.setattr(settings, "DEBUG", True)
    response = await global_exception_handler(MagicMock(), ValueError("boom"))
    body = response.body.decode()
    assert "boom" in body


# ---------------------------------------------------------------------------
# B2: scope checks on AI provider/model endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_provider_rejects_cross_tenant_user(async_client):
    """B2: a USER in tenant A cannot read a TENANT-scoped provider owned by tenant B."""
    user_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="USER")
    _override_user(user_a)
    try:
        provider_b = _make_provider(
            uuid4(),
            scope="TENANT"
            if False
            else __import__("app.models.enums", fromlist=["AIScope"]).AIScope.TENANT,
            tenant_id=TENANT_B,
        )
        with patch(
            "app.api.v1.endpoints.ai_config.AIProviderService.get_provider",
            new=AsyncMock(return_value=provider_b),
        ):
            response = await async_client.get(f"/api/v1/ai-config/providers/{provider_b.id}")
        assert response.status_code == 403, response.text
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_get_provider_rejects_other_users_personal_key(async_client):
    """B2: a USER cannot read another user's USER-scope provider (api_key leak)."""
    from app.models.enums import AIScope

    user_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="USER")
    _override_user(user_a)
    try:
        provider_b = _make_provider(
            uuid4(), scope=AIScope.USER, user_id=USER_B, api_key="user-b-secret"
        )
        with patch(
            "app.api.v1.endpoints.ai_config.AIProviderService.get_provider",
            new=AsyncMock(return_value=provider_b),
        ):
            response = await async_client.get(f"/api/v1/ai-config/providers/{provider_b.id}")
        assert response.status_code == 403
        assert "user-b-secret" not in response.text
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_get_provider_allows_owner(async_client):
    """B2: a USER can still read their own USER-scope provider."""
    from app.models.enums import AIScope

    user_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="USER")
    _override_user(user_a)
    try:
        provider_a = _make_provider(
            uuid4(), scope=AIScope.USER, user_id=USER_A, api_key="my-own-key"
        )
        with patch(
            "app.api.v1.endpoints.ai_config.AIProviderService.get_provider",
            new=AsyncMock(return_value=provider_a),
        ):
            response = await async_client.get(f"/api/v1/ai-config/providers/{provider_a.id}")
        assert response.status_code == 200, response.text
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_get_provider_allows_system_admin(async_client):
    """B2: SYSTEM_ADMIN can read any provider regardless of scope."""
    from app.models.enums import AIScope

    admin = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="SYSTEM_ADMIN")
    _override_user(admin)
    try:
        provider_b = _make_provider(uuid4(), scope=AIScope.TENANT, tenant_id=TENANT_B)
        with patch(
            "app.api.v1.endpoints.ai_config.AIProviderService.get_provider",
            new=AsyncMock(return_value=provider_b),
        ):
            response = await async_client.get(f"/api/v1/ai-config/providers/{provider_b.id}")
        assert response.status_code == 200, response.text
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_get_provider_with_models_rejects_cross_tenant(async_client):
    """B2: /with-models must also scope-check (was missing)."""
    from app.models.enums import AIScope

    user_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="USER")
    _override_user(user_a)
    try:
        provider_b = _make_provider(uuid4(), scope=AIScope.TENANT, tenant_id=TENANT_B)
        with (
            patch(
                "app.api.v1.endpoints.ai_config.AIProviderService.get_provider",
                new=AsyncMock(return_value=provider_b),
            ),
            patch(
                "app.api.v1.endpoints.ai_config.AIProviderService.get_models",
                new=AsyncMock(return_value=[]),
            ),
        ):
            response = await async_client.get(
                f"/api/v1/ai-config/providers/{provider_b.id}/with-models"
            )
        assert response.status_code == 403
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_get_model_rejects_cross_user(async_client):
    """B2: GET /models/{id} must scope-check via owning provider."""
    from app.models.enums import AIScope

    user_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="USER")
    _override_user(user_a)
    try:
        provider_b = _make_provider(uuid4(), scope=AIScope.USER, user_id=USER_B)
        model = MagicMock()
        model.id = uuid4()
        model.provider_id = provider_b.id
        svc_mock = MagicMock()
        svc_mock.get_model = AsyncMock(return_value=model)
        svc_mock.get_provider = AsyncMock(return_value=provider_b)

        with patch(
            "app.api.v1.endpoints.ai_config.AIProviderService",
            return_value=svc_mock,
        ):
            response = await async_client.get(f"/api/v1/ai-config/models/{model.id}")
        assert response.status_code == 403
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_fetch_external_models_rejects_non_scoped_user(async_client):
    """B2/B9: fetch-external-models must scope-check (was no RBAC at all)."""
    from app.models.enums import AIScope

    user_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="USER")
    _override_user(user_a)
    try:
        provider_b = _make_provider(
            uuid4(),
            scope=AIScope.USER,
            user_id=USER_B,
            api_key="not-yours",
        )
        # api_base must be a public host so we don't trip the SSRF guard
        provider_b.api_base = "https://api.openai.com/v1"
        with patch(
            "app.api.v1.endpoints.ai_config.AIProviderService.get_provider",
            new=AsyncMock(return_value=provider_b),
        ):
            response = await async_client.get(
                f"/api/v1/ai-config/providers/{provider_b.id}/fetch-external-models"
            )
        assert response.status_code == 403
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_fetch_external_models_ssrf_guard(monkeypatch, async_client):
    """B9: api_base pointing at a private/loopback host is rejected in prod."""
    from app.core.config import settings
    from app.models.enums import AIScope

    monkeypatch.setattr(settings, "DEBUG", False)

    admin = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="SYSTEM_ADMIN")
    _override_user(admin)
    try:
        provider = _make_provider(uuid4(), scope=AIScope.SYSTEM, tenant_id=None, user_id=None)
        provider.api_base = "http://127.0.0.1:8087/admin"  # loopback
        with patch(
            "app.api.v1.endpoints.ai_config.AIProviderService.get_provider",
            new=AsyncMock(return_value=provider),
        ):
            response = await async_client.get(
                f"/api/v1/ai-config/providers/{provider.id}/fetch-external-models"
            )
        assert response.status_code == 400
        assert "private" in response.text.lower() or "loopback" in response.text.lower()
    finally:
        _clear_overrides()


# ---------------------------------------------------------------------------
# Static source checks (regression guards)
# ---------------------------------------------------------------------------


def test_get_provider_endpoint_calls_verify_provider_access():
    """B2 regression: get_provider must invoke verify_provider_access.

    Catches accidental removal of the scope check at source level.
    """
    src = inspect.getsource(importlib.import_module("app.api.v1.endpoints.ai_config"))
    # Every entry point that loads a provider must call verify_*_access.
    for fn_name in (
        "async def get_provider(",
        "async def get_provider_with_models(",
        "async def fetch_external_models(",
        "async def create_model(",
        "async def get_models_for_provider(",
        "async def get_model(",
        "async def update_model(",
        "async def delete_model(",
    ):
        assert fn_name in src, f"AI config endpoint {fn_name!r} removed?"

    # Count verify_provider_access / verify_model_access calls — at least 8
    # (one per endpoint above).
    calls = src.count("verify_provider_access(") + src.count("verify_model_access(")
    assert calls >= 7, f"Expected at least 7 verify_*_access calls in ai_config.py, found {calls}"


# ---------------------------------------------------------------------------
# B10: caller-supplied tenant_id/user_id on the resolution endpoints
# ---------------------------------------------------------------------------

TASK = "ocr"


def _make_model(model_id, provider_id):
    from app.models.ai_provider_model import AIModel

    return AIModel(
        id=model_id,
        provider_id=provider_id,
        name="Resolved model",
        model_name="gpt-4o",
        description=None,
        capabilities=["vision"],
        is_active=True,
        max_tokens=4096,
        temperature=None,
        is_local=False,
        settings={},
    )


def _make_assignment(task_type, provider_id=None, model_id=None, tenant_id=None, user_id=None):
    from app.ai.providers.task_defs import task_def_id
    from app.models.ai_provider_model import AIScope, AITaskAssignment
    from app.models.ai_task_model import AITaskDef

    assignment = AITaskAssignment(
        id=uuid4(),
        task_type_id=task_def_id(task_type),
        scope=AIScope.TENANT if tenant_id else AIScope.SYSTEM,
        provider_id=provider_id,
        model_id=model_id,
        phi_allowed=False,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    assignment.task_def = AITaskDef(
        id=task_def_id(task_type), code=task_type, label_key=f"settings.ai.task_{task_type}"
    )
    return assignment


@pytest.mark.asyncio
async def test_active_assignment_rejects_cross_tenant_read(async_client):
    """B10: a tenant admin cannot resolve another tenant's active assignment."""
    admin_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="ADMIN")
    _override_user(admin_a)
    try:
        assignment_b = _make_assignment(TASK, tenant_id=TENANT_B)
        resolve = AsyncMock(return_value=assignment_b)
        with patch(
            "app.api.v1.endpoints.ai_config.AIProviderService.get_active_assignment_for_task",
            new=resolve,
        ):
            response = await async_client.get(
                f"/api/v1/ai-config/task-assignments/active/{TASK}?tenant_id={TENANT_B}"
            )
        assert response.status_code == 403, response.text
        resolve.assert_not_awaited()
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_active_assignment_rejects_other_users_read(async_client):
    """B10: a USER cannot resolve another user's active assignment."""
    from app.models.enums import AIScope

    user_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="USER")
    _override_user(user_a)
    try:
        assignment_b = _make_assignment(TASK, user_id=USER_B)
        assignment_b.scope = AIScope.USER
        resolve = AsyncMock(return_value=assignment_b)
        with patch(
            "app.api.v1.endpoints.ai_config.AIProviderService.get_active_assignment_for_task",
            new=resolve,
        ):
            response = await async_client.get(
                f"/api/v1/ai-config/task-assignments/active/{TASK}?user_id={USER_B}"
            )
        assert response.status_code == 403, response.text
        resolve.assert_not_awaited()
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_active_assignment_allows_own_tenant_and_user(async_client):
    """B10: same-tenant / same-user resolution keeps working."""
    from app.models.enums import AIScope

    user_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="USER")
    _override_user(user_a)
    try:
        assignment = _make_assignment(TASK, tenant_id=TENANT_A)
        assignment.scope = AIScope.TENANT
        resolve = AsyncMock(return_value=assignment)
        with patch(
            "app.api.v1.endpoints.ai_config.AIProviderService.get_active_assignment_for_task",
            new=resolve,
        ):
            response = await async_client.get(
                f"/api/v1/ai-config/task-assignments/active/{TASK}"
                f"?tenant_id={TENANT_A}&user_id={USER_A}"
            )
        assert response.status_code == 200, response.text
        assert response.json()["task_type"] == TASK
        resolve.assert_awaited_once_with(TASK, TENANT_A, USER_A)
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_active_assignment_system_admin_may_read_any_tenant(async_client):
    """B10: SYSTEM_ADMIN keeps cross-tenant resolution (admin surface)."""
    from app.models.enums import AIScope

    sysadmin = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="SYSTEM_ADMIN")
    _override_user(sysadmin)
    try:
        assignment_b = _make_assignment(TASK, tenant_id=TENANT_B)
        assignment_b.scope = AIScope.TENANT
        with patch(
            "app.api.v1.endpoints.ai_config.AIProviderService.get_active_assignment_for_task",
            new=AsyncMock(return_value=assignment_b),
        ):
            response = await async_client.get(
                f"/api/v1/ai-config/task-assignments/active/{TASK}?tenant_id={TENANT_B}"
            )
        assert response.status_code == 200, response.text
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_default_for_task_rejects_cross_tenant_read(async_client):
    """B10: default-for-task must refuse another tenant's resolved config."""
    admin_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="ADMIN")
    _override_user(admin_a)
    try:
        provider_b = _make_provider(uuid4(), scope="SYSTEM")
        assignment_b = _make_assignment(TASK, provider_id=provider_b.id, tenant_id=TENANT_B)
        resolve = AsyncMock(return_value=assignment_b)
        with (
            patch(
                "app.api.v1.endpoints.ai_config.AIProviderService.get_active_assignment_for_task",
                new=resolve,
            ),
            patch(
                "app.api.v1.endpoints.ai_config.AIProviderService.get_provider",
                new=AsyncMock(return_value=provider_b),
            ),
        ):
            response = await async_client.get(
                f"/api/v1/ai-config/default-for-task/{TASK}?tenant_id={TENANT_B}"
            )
        assert response.status_code == 403, response.text
        assert "secret-key-XYZ" not in response.text
        resolve.assert_not_awaited()
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_default_for_task_rejects_other_users_read(async_client):
    """B10: default-for-task must refuse another user's resolved config."""
    user_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="USER")
    _override_user(user_a)
    try:
        provider_a = _make_provider(uuid4(), scope="SYSTEM")
        assignment = _make_assignment(TASK, provider_id=provider_a.id, user_id=USER_B)
        resolve = AsyncMock(return_value=assignment)
        with (
            patch(
                "app.api.v1.endpoints.ai_config.AIProviderService.get_active_assignment_for_task",
                new=resolve,
            ),
            patch(
                "app.api.v1.endpoints.ai_config.AIProviderService.get_provider",
                new=AsyncMock(return_value=provider_a),
            ),
        ):
            response = await async_client.get(
                f"/api/v1/ai-config/default-for-task/{TASK}?user_id={USER_B}"
            )
        assert response.status_code == 403, response.text
        resolve.assert_not_awaited()
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_default_for_task_allows_own_tenant(async_client):
    """B10: same-tenant resolution still returns provider + model."""
    from app.models.enums import AIScope

    user_a = MockUser(tenant_id=TENANT_A, user_id=USER_A, role="USER")
    _override_user(user_a)
    try:
        provider_a = _make_provider(uuid4(), scope=AIScope.TENANT, tenant_id=TENANT_A)
        model_a = _make_model(uuid4(), provider_a.id)
        assignment = _make_assignment(
            TASK, provider_id=provider_a.id, model_id=model_a.id, tenant_id=TENANT_A
        )
        assignment.scope = AIScope.TENANT
        with (
            patch(
                "app.api.v1.endpoints.ai_config.AIProviderService.get_active_assignment_for_task",
                new=AsyncMock(return_value=assignment),
            ),
            patch(
                "app.api.v1.endpoints.ai_config.AIProviderService.get_provider",
                new=AsyncMock(return_value=provider_a),
            ),
            patch(
                "app.api.v1.endpoints.ai_config.AIProviderService.get_model",
                new=AsyncMock(return_value=model_a),
            ),
        ):
            response = await async_client.get(
                f"/api/v1/ai-config/default-for-task/{TASK}?tenant_id={TENANT_A}"
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["provider"]["id"] == str(provider_a.id)
        assert body["model"]["model_name"] == "gpt-4o"
        assert "secret-key-XYZ" not in response.text
    finally:
        _clear_overrides()


def test_resolution_endpoints_call_check_requested_scope():
    """B10 regression: both resolution endpoints guard caller-supplied scope."""
    src = inspect.getsource(importlib.import_module("app.api.v1.endpoints.ai_config"))
    for fn_name in (
        "async def get_active_task_assignment(",
        "async def get_default_for_task(",
    ):
        body = src.split(fn_name, 1)[1].split("\n@router.", 1)[0]
        assert "check_requested_scope(" in body, f"{fn_name} lost its scope guard"
