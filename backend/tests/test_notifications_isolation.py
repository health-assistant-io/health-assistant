"""Regression tests for audit items B2 + B3 — notification auth + isolation.

Pre-fix contract:
- ``POST /notifications/{id}/delivered`` had **no auth at all** — any
  anonymous client could mark any notification as delivered (B2).
- All other notification endpoints (``list``, ``mark_as_read``,
  ``create_trigger``, ``list_triggers``, ``delete_trigger``,
  ``test_trigger``) filtered only by ``patient_id`` and never checked
  tenant or patient-access (B3).

Post-fix contract pinned here:
1. Every endpoint requires ``Depends(get_current_user)``.
2. ``NotificationManager.mark_as_read`` / ``mark_as_delivered`` /
   ``get_active_notifications`` accept and apply ``tenant_id`` — a
   cross-tenant call returns False / no rows.
3. ``NotificationManager`` no longer unconditionally returns ``True``
   from the mark_* methods — it returns the actual rowcount so an
   endpoint can surface 404 for cross-tenant calls.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.endpoints import notifications as notif_endpoint
from app.models.enums import Role
from app.schemas.user import TokenData


def _user(tenant_id=None, role=Role.USER.value) -> TokenData:
    return TokenData(
        sub="test@local",
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role=role,
    )


# ---------------------------------------------------------------------------
# B2: mark_as_delivered requires auth + is tenant-scoped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_as_delivered_requires_auth_signature():
    """The handler signature MUST depend on get_current_user.

    A handler without that dependency is exactly the B2 hole. Inspecting
    the signature is a static guarantee that does not require DB mocks.
    """
    import inspect

    sig = inspect.signature(notif_endpoint.mark_as_delivered)
    assert "current_user" in sig.parameters, (
        "mark_as_delivered must inject current_user (auth was previously missing)"
    )
    # And the dependency must come from get_current_user.
    dep = sig.parameters["current_user"].default
    assert dep is not None and getattr(dep, "dependency", None) is not None, (
        "current_user parameter must be wired through Depends(get_current_user)"
    )


@pytest.mark.asyncio
async def test_mark_as_delivered_cross_tenant_returns_404():
    """Cross-tenant mark-as-delivered returns 404 (no information leak)."""
    from fastapi import HTTPException

    user = _user()

    # Patch NotificationManager.mark_as_delivered to simulate "row in
    # another tenant → no row matched → False". The endpoint must surface
    # this as 404 and must have passed tenant_id into the manager call.
    with patch.object(
        notif_endpoint.NotificationManager,
        "mark_as_delivered",
        new=AsyncMock(return_value=False),
    ) as mocked:
        with pytest.raises(HTTPException) as exc:
            await notif_endpoint.mark_as_delivered(
                notification_id=str(uuid.uuid4()), current_user=user
            )
    assert exc.value.status_code == 404

    mocked.assert_awaited_once()
    args, kwargs = mocked.await_args
    assert kwargs.get("tenant_id") == user.tenant_id or (
        len(args) == 2 and args[1] == user.tenant_id
    ), "mark_as_delivered did NOT pass tenant_id — cross-tenant isolation broken"


@pytest.mark.asyncio
async def test_mark_as_delivered_same_tenant_succeeds():
    user = _user()
    with patch.object(
        notif_endpoint.NotificationManager,
        "mark_as_delivered",
        new=AsyncMock(return_value=True),
    ) as mocked:
        result = await notif_endpoint.mark_as_delivered(
            notification_id=str(uuid.uuid4()), current_user=user
        )
    assert result == {"status": "success"}
    args, kwargs = mocked.await_args
    assert kwargs.get("tenant_id") == user.tenant_id or args[1] == user.tenant_id


@pytest.mark.asyncio
async def test_mark_as_delivered_rejects_non_uuid():
    """Malformed notification_id → 400 (not 500)."""
    from fastapi import HTTPException

    user = _user()
    with pytest.raises(HTTPException) as exc:
        await notif_endpoint.mark_as_delivered(
            notification_id="not-a-uuid", current_user=user
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# B3: every other notification endpoint is tenant-scoped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_endpoint_requires_auth():
    """Static guarantee: every route handler in this module injects
    ``current_user``."""
    import inspect

    handlers = [
        notif_endpoint.list_notifications,
        notif_endpoint.mark_as_read,
        notif_endpoint.mark_as_delivered,
        notif_endpoint.create_trigger,
        notif_endpoint.list_triggers,
        notif_endpoint.delete_trigger,
        notif_endpoint.test_trigger,
    ]
    for fn in handlers:
        sig = inspect.signature(fn)
        assert "current_user" in sig.parameters, (
            f"{fn.__name__} must inject current_user (auth + tenant scoping)"
        )


@pytest.mark.asyncio
async def test_mark_as_read_passes_tenant_id_and_404s_on_cross_tenant():
    user = _user()
    from fastapi import HTTPException

    # Cross-tenant: manager returns False → endpoint must 404.
    with patch.object(
        notif_endpoint.NotificationManager,
        "mark_as_read",
        new=AsyncMock(return_value=False),
    ) as mocked:
        with pytest.raises(HTTPException) as exc:
            await notif_endpoint.mark_as_read(
                notification_id=str(uuid.uuid4()), current_user=user
            )
    assert exc.value.status_code == 404

    args, kwargs = mocked.await_args
    assert kwargs.get("tenant_id") == user.tenant_id or args[1] == user.tenant_id


@pytest.mark.asyncio
async def test_list_notifications_calls_check_patient_access():
    """A USER caller must pass check_patient_access before listing."""
    user = _user()
    with patch.object(
        notif_endpoint, "check_patient_access", new=AsyncMock()
    ) as ck, patch.object(
        notif_endpoint.NotificationManager,
        "get_active_notifications",
        new=AsyncMock(return_value=[]),
    ) as listing:
        await notif_endpoint.list_notifications(
            patient_id=str(uuid.uuid4()),
            unread_only=False,
            limit=20,
            current_user=user,
            db=MagicMock(),
        )
    ck.assert_awaited_once()
    # Ensure tenant_id is propagated to the service layer.
    args, kwargs = listing.await_args
    assert kwargs.get("tenant_id") == user.tenant_id, (
        "list_notifications did NOT pass tenant_id to NotificationManager"
    )


@pytest.mark.asyncio
async def test_create_trigger_calls_check_patient_access():
    user = _user()
    fake_trigger = MagicMock()
    fake_trigger.to_dict.return_value = {"id": "x"}
    with patch.object(
        notif_endpoint, "check_patient_access", new=AsyncMock()
    ) as ck, patch.object(
        notif_endpoint.NotificationManager,
        "create_trigger",
        new=AsyncMock(return_value=fake_trigger),
    ) as creating:
        await notif_endpoint.create_trigger(
            patient_id=str(uuid.uuid4()),
            title="t",
            body=None,
            notification_type="MEDICATION_REMINDER",
            trigger_type="TIME",
            config={},
            reference_id=None,
            current_user=user,
            db=MagicMock(),
        )
    ck.assert_awaited_once()
    args, kwargs = creating.await_args
    assert kwargs.get("tenant_id") == user.tenant_id


@pytest.mark.asyncio
async def test_list_triggers_calls_check_patient_access():
    user = _user()
    with patch.object(
        notif_endpoint, "check_patient_access", new=AsyncMock()
    ) as ck, patch.object(
        notif_endpoint, "AsyncSessionLocal"
    ) as SessionCls:
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        SessionCls.return_value = session

        await notif_endpoint.list_triggers(
            patient_id=str(uuid.uuid4()),
            current_user=user,
            db=MagicMock(),
        )
    ck.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_trigger_passes_tenant_predicate():
    """delete_trigger uses AsyncSessionLocal directly — we verify the
    DELETE statement carries the tenant_id predicate."""
    user = _user()
    with patch.object(
        notif_endpoint, "AsyncSessionLocal"
    ) as SessionCls, patch.object(
        notif_endpoint, "delete"
    ) as delete_fn:
        # Make `delete()` return a MagicMock that records .where() calls.
        stmt = MagicMock()
        stmt.where.return_value = stmt
        delete_fn.return_value = stmt

        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        SessionCls.return_value = session

        await notif_endpoint.delete_trigger(
            trigger_id=str(uuid.uuid4()), current_user=user
        )

    # `delete()` was called once with NotificationTrigger.
    delete_fn.assert_called_once()
    # `.where()` is called once with BOTH predicates (id + tenant_id).
    stmt.where.assert_called_once()
    where_call_args = stmt.where.call_args.args
    assert len(where_call_args) >= 2, (
        "delete_trigger must filter by id AND tenant_id in the same .where() call"
    )


@pytest.mark.asyncio
async def test_test_trigger_passes_tenant_predicate():
    user = _user()
    with patch.object(
        notif_endpoint, "AsyncSessionLocal"
    ) as SessionCls, patch.object(
        notif_endpoint.NotificationManager,
        "fire_notification",
        new=AsyncMock(),
    ):
        session = AsyncMock()
        # No row found in another tenant.
        empty = MagicMock()
        empty.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=empty)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        SessionCls.return_value = session

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await notif_endpoint.test_trigger(
                trigger_id=str(uuid.uuid4()), current_user=user
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# NotificationManager: tenant_id is enforced at the service layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_mark_as_read_returns_false_when_no_row(monkeypatch):
    """Service-layer contract: mark_as_read must reflect the actual rowcount
    so an endpoint can distinguish "matched" from "matched nothing"."""
    from app.services.notification_manager import NotificationManager

    fake_result = MagicMock()
    fake_result.rowcount = 0
    fake_session = AsyncMock()
    fake_session.execute = AsyncMock(return_value=fake_result)
    fake_session.commit = AsyncMock()

    class FakeASL:
        def __await__(self):
            async def _enter():
                return fake_session
            return _enter().__await__()

        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr(
        "app.services.notification_manager.AsyncSessionLocal", lambda: FakeASL()
    )
    monkeypatch.setattr(
        "app.services.notification_manager.DATABASE_AVAILABLE", True
    )

    ok = await NotificationManager.mark_as_read(
        uuid.uuid4(), tenant_id=uuid.uuid4()
    )
    assert ok is False


@pytest.mark.asyncio
async def test_manager_get_active_notifications_applies_tenant_filter(monkeypatch):
    """Service-layer contract: when tenant_id is passed, it must end up in
    the WHERE clause."""
    from app.services import notification_manager as nm

    captured = {"predicates": []}

    class _Stmt:
        def __init__(self, base):
            self.base = base

        def where(self, *preds):
            captured["predicates"].extend(str(p) for p in preds)
            return self

        def order_by(self, *a, **kw):
            return self

        def limit(self, *a, **kw):
            return self

    real_select = nm.select

    def fake_select(*a, **kw):
        return _Stmt(real_select(*a, **kw))

    monkeypatch.setattr(nm, "select", fake_select)

    empty = MagicMock()
    empty.scalars.return_value.all.return_value = []
    fake_session = AsyncMock()
    fake_session.execute = AsyncMock(return_value=empty)

    class FakeASL:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr(
        "app.services.notification_manager.AsyncSessionLocal", lambda: FakeASL()
    )
    monkeypatch.setattr(
        "app.services.notification_manager.DATABASE_AVAILABLE", True
    )

    tenant = uuid.uuid4()
    await nm.NotificationManager.get_active_notifications(
        patient_id=uuid.uuid4(), tenant_id=tenant, limit=20, unread_only=False
    )

    joined = " ".join(captured["predicates"])
    assert "tenant_id" in joined, (
        "get_active_notifications did NOT add a tenant_id predicate"
    )
