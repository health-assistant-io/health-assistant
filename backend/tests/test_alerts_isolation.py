"""Regression tests for audit item B4 — alert tenant isolation.

Pre-fix contract: every endpoint called ``alert_service`` by bare id and
never re-checked tenancy. Any authenticated user could
read / update / delete / fire any alert in the system.

Post-fix contract pinned here:
1. Every endpoint injects ``current_user``.
2. Every ``alert_service`` method takes an optional ``tenant_id`` and
   applies it as a WHERE predicate on SELECT/UPDATE/DELETE.
3. Cross-tenant calls return 404 (or no-op for delete) — no information
   leak.
4. Patient-scoped routes call ``check_patient_access`` first.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.endpoints import alerts as alerts_endpoint
from app.api.v1.endpoints import utils as endpoint_utils
from app.models.enums import Role
from app.schemas.user import TokenData
from app.services import alert_service as svc


def _user(tenant_id=None, role=Role.USER.value) -> TokenData:
    return TokenData(
        sub="test@local",
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role=role,
    )


# ---------------------------------------------------------------------------
# Static signature guarantees — every endpoint injects current_user
# ---------------------------------------------------------------------------


def test_every_endpoint_requires_auth():
    import inspect

    handlers = [
        alerts_endpoint.get_alert_endpoint,
        alerts_endpoint.list_alerts_endpoint,
        alerts_endpoint.create_alert_endpoint,
        alerts_endpoint.update_alert_endpoint,
        alerts_endpoint.delete_alert_endpoint,
        alerts_endpoint.trigger_alert_endpoint,
        alerts_endpoint.get_alert_history_endpoint,
    ]
    for fn in handlers:
        sig = inspect.signature(fn)
        assert "current_user" in sig.parameters, (
            f"{fn.__name__} must inject current_user (auth + tenant scoping)"
        )


# ---------------------------------------------------------------------------
# Endpoints propagate tenant_id to the service layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_alert_endpoint_propagates_tenant_id_and_404s_cross_tenant():
    from fastapi import HTTPException

    user = _user()

    # Cross-tenant: service returns None → endpoint must 404.
    with patch.object(alerts_endpoint, "get_alert", new=AsyncMock(return_value=None)) as mocked:
        with pytest.raises(HTTPException) as exc:
            await alerts_endpoint.get_alert_endpoint(
                alert_id=str(uuid.uuid4()), current_user=user, db=MagicMock()
            )
    assert exc.value.status_code == 404

    args, kwargs = mocked.await_args
    assert kwargs.get("tenant_id") == user.tenant_id


@pytest.mark.asyncio
async def test_get_alert_endpoint_same_tenant_returns_alert():
    user = _user()
    alert = {"id": "x"}
    with patch.object(alerts_endpoint, "get_alert", new=AsyncMock(return_value=alert)) as mocked:
        result = await alerts_endpoint.get_alert_endpoint(
            alert_id=str(uuid.uuid4()), current_user=user, db=MagicMock()
        )
    assert result == alert
    args, kwargs = mocked.await_args
    assert kwargs.get("tenant_id") == user.tenant_id


@pytest.mark.asyncio
async def test_list_alerts_propagates_tenant_id_and_calls_check_patient_access():
    user = _user()
    with patch.object(
        alerts_endpoint, "check_patient_access", new=AsyncMock()
    ) as ck, patch.object(
        alerts_endpoint, "list_alerts", new=AsyncMock(return_value={"items": [], "total": 0})
    ) as listing:
        await alerts_endpoint.list_alerts_endpoint(
            patient_id=str(uuid.uuid4()),
            current_user=user,
            db=MagicMock(),
        )
    ck.assert_awaited_once()
    args, kwargs = listing.await_args
    assert kwargs.get("tenant_id") == user.tenant_id


@pytest.mark.asyncio
async def test_list_alerts_skips_check_patient_access_when_no_patient():
    """When patient_id is omitted (tenant-wide listing), don't call
    check_patient_access — ADMIN/MANAGER are entitled to the tenant view."""
    user = _user(role=Role.ADMIN.value)
    with patch.object(
        alerts_endpoint, "check_patient_access", new=AsyncMock()
    ) as ck, patch.object(
        alerts_endpoint, "list_alerts", new=AsyncMock(return_value={"items": [], "total": 0})
    ):
        await alerts_endpoint.list_alerts_endpoint(
            patient_id=None,
            current_user=user,
            db=MagicMock(),
        )
    ck.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_alert_calls_check_patient_access_and_passes_tenant():
    user = _user()
    fake = MagicMock()
    with patch.object(
        alerts_endpoint, "check_patient_access", new=AsyncMock()
    ) as ck, patch.object(
        alerts_endpoint, "create_alert", new=AsyncMock(return_value=fake)
    ) as creating:
        await alerts_endpoint.create_alert_endpoint(
            alert_type="hr_high",
            patient_id=str(uuid.uuid4()),
            threshold=120.0,
            enabled=True,
            current_user=user,
            db=MagicMock(),
        )
    ck.assert_awaited_once()
    args, kwargs = creating.await_args
    # 5 positional: type, patient_id, threshold, enabled, tenant_id
    assert args[4] == user.tenant_id


@pytest.mark.asyncio
async def test_update_alert_cross_tenant_returns_404():
    """Cross-tenant update → service returns None → 404."""
    from fastapi import HTTPException

    user = _user()
    with patch.object(alerts_endpoint, "update_alert", new=AsyncMock(return_value=None)) as mocked:
        with pytest.raises(HTTPException) as exc:
            await alerts_endpoint.update_alert_endpoint(
                alert_id=str(uuid.uuid4()),
                threshold=130.0,
                current_user=user,
                db=MagicMock(),
            )
    assert exc.value.status_code == 404
    args, kwargs = mocked.await_args
    assert kwargs.get("tenant_id") == user.tenant_id


@pytest.mark.asyncio
async def test_delete_alert_passes_tenant_id():
    user = _user()
    with patch.object(alerts_endpoint, "delete_alert", new=AsyncMock(return_value=True)) as mocked:
        result = await alerts_endpoint.delete_alert_endpoint(
            alert_id=str(uuid.uuid4()), current_user=user, db=MagicMock()
        )
    assert result == {"message": "Alert deleted successfully"}
    args, kwargs = mocked.await_args
    assert kwargs.get("tenant_id") == user.tenant_id


@pytest.mark.asyncio
async def test_trigger_alert_cross_tenant_returns_404():
    from fastapi import HTTPException

    user = _user()
    with patch.object(alerts_endpoint, "trigger_alert", new=AsyncMock(return_value=None)) as mocked:
        with pytest.raises(HTTPException) as exc:
            await alerts_endpoint.trigger_alert_endpoint(
                alert_id=str(uuid.uuid4()), current_user=user, db=MagicMock()
            )
    assert exc.value.status_code == 404
    args, kwargs = mocked.await_args
    assert kwargs.get("tenant_id") == user.tenant_id


@pytest.mark.asyncio
async def test_get_alert_history_calls_check_patient_access_when_patient():
    user = _user()
    with patch.object(
        alerts_endpoint, "check_patient_access", new=AsyncMock()
    ) as ck, patch.object(
        alerts_endpoint, "get_alert_history", new=AsyncMock(return_value=[])
    ) as hist:
        await alerts_endpoint.get_alert_history_endpoint(
            patient_id=str(uuid.uuid4()),
            current_user=user,
            db=MagicMock(),
        )
    ck.assert_awaited_once()
    args, kwargs = hist.await_args
    assert kwargs.get("tenant_id") == user.tenant_id


# ---------------------------------------------------------------------------
# Service-layer guarantees — tenant_id actually constrains the query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_get_alert_applies_tenant_predicate(monkeypatch):
    """alert_service.get_alert must add a tenant_id predicate when called
    with one."""
    captured = {"predicates": []}

    class _Stmt:
        def __init__(self, base):
            self.base = base

        def where(self, *preds):
            captured["predicates"].extend(str(p) for p in preds)
            return self

    real_select = svc.select

    def fake_select(*a, **kw):
        return _Stmt(real_select(*a, **kw))

    monkeypatch.setattr(svc, "select", fake_select)

    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=empty)

    class FakeASL:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr("app.services.alert_service.AsyncSessionLocal", lambda: FakeASL())
    monkeypatch.setattr("app.services.alert_service.DATABASE_AVAILABLE", True)

    tenant = uuid.uuid4()
    await svc.get_alert(uuid.uuid4(), tenant_id=tenant)

    joined = " ".join(captured["predicates"])
    assert "tenant_id" in joined, "alert_service.get_alert missing tenant_id predicate"


@pytest.mark.asyncio
async def test_service_update_alert_returns_none_when_cross_tenant(monkeypatch):
    """update_alert: cross-tenant call → UPDATE matches 0 rows → return None."""
    fake_result = MagicMock()
    fake_result.rowcount = 0
    session = AsyncMock()
    session.execute = AsyncMock(return_value=fake_result)
    session.commit = AsyncMock()

    class FakeASL:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr("app.services.alert_service.AsyncSessionLocal", lambda: FakeASL())
    monkeypatch.setattr("app.services.alert_service.DATABASE_AVAILABLE", True)
    monkeypatch.setattr(svc, "get_alert", AsyncMock(return_value=None))

    result = await svc.update_alert(
        uuid.uuid4(), threshold=10.0, enabled=True, tenant_id=uuid.uuid4()
    )
    assert result is None


@pytest.mark.asyncio
async def test_service_delete_alert_returns_false_when_cross_tenant(monkeypatch):
    fake_result = MagicMock()
    fake_result.rowcount = 0
    session = AsyncMock()
    session.execute = AsyncMock(return_value=fake_result)
    session.commit = AsyncMock()

    class FakeASL:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr("app.services.alert_service.AsyncSessionLocal", lambda: FakeASL())
    monkeypatch.setattr("app.services.alert_service.DATABASE_AVAILABLE", True)

    ok = await svc.delete_alert(uuid.uuid4(), tenant_id=uuid.uuid4())
    assert ok is False


@pytest.mark.asyncio
async def test_service_trigger_alert_returns_none_when_cross_tenant(monkeypatch):
    fake_result = MagicMock()
    fake_result.rowcount = 0
    session = AsyncMock()
    session.execute = AsyncMock(return_value=fake_result)
    session.commit = AsyncMock()

    class FakeASL:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr("app.services.alert_service.AsyncSessionLocal", lambda: FakeASL())
    monkeypatch.setattr("app.services.alert_service.DATABASE_AVAILABLE", True)

    ok = await svc.trigger_alert(uuid.uuid4(), tenant_id=uuid.uuid4())
    assert ok is None


@pytest.mark.asyncio
async def test_service_list_alerts_returns_empty_on_bad_uuid():
    """Service-level guard: malformed tenant_id returns empty result,
    not a 500."""
    result = await svc.list_alerts(
        tenant_id="not-a-uuid",
        patient_id=None,
    )
    assert result == {"items": [], "total": 0}
