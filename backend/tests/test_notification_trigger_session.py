"""Regression tests for the ``check_notification_triggers`` engine-affinity fix.

Pre-fix contract: the Celery periodic task ``check_notification_triggers``
called ``NotificationManager.process_due_triggers()`` with no session, so
the manager opened its own via the global app engine (``AsyncSessionLocal``,
``pool_pre_ping=True``, real connection pool). Inside a prefork worker that
engine's asyncpg connections are bound to a different / already-closed event
loop than the per-task loop, so the pool's pre-ping checkout raised
``RuntimeError: Future ... attached to a different loop`` and the graceful
close raised ``RuntimeError: Event loop is closed``.

Post-fix contract pinned here:
1. ``check_notification_triggers`` owns a worker-scoped ``NullPool`` session
   via ``get_async_session()`` and injects it into ``process_due_triggers``.
2. ``process_due_triggers(session=...)`` uses the injected session for the
   trigger query AND forwards the SAME session to ``fire_notification``
   (never opens its own via ``AsyncSessionLocal``).
3. ``fire_notification(trigger, session=...)`` uses the injected session and
   never opens its own via ``AsyncSessionLocal``.
4. A failing trigger triggers ``session.rollback()`` so the shared session
   stays usable for the remaining triggers in the cycle.
"""
import inspect

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.notification import TriggerType


# ---------------------------------------------------------------------------
# 1. The task wires the worker-scoped session into the manager.
# ---------------------------------------------------------------------------


def test_task_injects_worker_session():
    """``check_notification_triggers`` must obtain its session from
    ``get_async_session`` (NullPool worker engine) and pass it as ``session=``
    to ``process_due_triggers``.

    This is the static guarantee that the global pooled app engine is never
    reached from the periodic task — the root cause of the loop crash.
    """
    from app.workers import tasks as worker_tasks

    src = inspect.getsource(worker_tasks.check_notification_triggers)
    assert "get_async_session()" in src, (
        "check_notification_triggers must create its session via "
        "get_async_session() (worker-scoped NullPool engine)."
    )
    assert "process_due_triggers(session=" in src, (
        "check_notification_triggers must inject the session into "
        "process_due_triggers(session=...) — otherwise the manager falls "
        "back to the global pooled engine and re-triggers the loop crash."
    )


def test_manager_signatures_accept_session():
    """Both public entry points accept an injectable ``session``."""
    from app.services.notification_manager import NotificationManager

    pdt = inspect.signature(NotificationManager.process_due_triggers)
    assert "session" in pdt.parameters, "process_due_triggers must accept session"

    fn = inspect.signature(NotificationManager.fire_notification)
    assert "session" in fn.parameters, "fire_notification must accept session"


# ---------------------------------------------------------------------------
# 2 + 3. The injected session is actually used (no AsyncSessionLocal fallback).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_due_triggers_uses_injected_session_and_forwards_it(monkeypatch):
    """When a session is injected, the manager must use it for the query and
    forward the SAME object to ``fire_notification`` — it must NOT instantiate
    ``AsyncSessionLocal``."""
    from app.services import notification_manager as nm

    monkeypatch.setattr(nm, "DATABASE_AVAILABLE", True)

    # Detector: AsyncSessionLocal must never be called on the injected path.
    asl_spy = MagicMock()
    monkeypatch.setattr(nm, "AsyncSessionLocal", asl_spy)

    trigger = MagicMock(id="t1", trigger_type=TriggerType.TIME, config={})

    fake_session = AsyncMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = [trigger]
    fake_session.execute = AsyncMock(return_value=res)
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()

    fire_spy = AsyncMock()
    monkeypatch.setattr(nm.NotificationManager, "fire_notification", fire_spy)

    await nm.NotificationManager.process_due_triggers(session=fake_session)

    asl_spy.assert_not_called()
    fake_session.execute.assert_awaited()
    # Same session object forwarded to fire_notification.
    assert fire_spy.await_args.args[1] is fake_session, (
        "process_due_triggers must forward the SAME injected session to "
        "fire_notification, not open a new one."
    )
    fake_session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_fire_notification_uses_injected_session(monkeypatch):
    """``fire_notification(trigger, session=...)`` must run on the injected
    session and never instantiate ``AsyncSessionLocal``."""
    from app.services import notification_manager as nm

    asl_spy = MagicMock()
    monkeypatch.setattr(nm, "AsyncSessionLocal", asl_spy)

    trigger = MagicMock(patient_id="p", tenant_id="t", id="t1", reference_id=None,
                       notification_type=MagicMock(), title="x", body=None, config={})

    fake_session = AsyncMock()
    fake_session.add = MagicMock()  # session.add is synchronous
    query_res = MagicMock()
    query_res.all.return_value = []      # no users → no push subs
    query_res.first.return_value = None  # has_push False
    fake_session.execute = AsyncMock(return_value=query_res)
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()

    with patch("app.workers.tasks.deliver_notification") as deliver:
        await nm.NotificationManager.fire_notification(trigger, session=fake_session)

    asl_spy.assert_not_called()
    fake_session.commit.assert_awaited()
    fake_session.refresh.assert_awaited()
    deliver.delay.assert_called()


# ---------------------------------------------------------------------------
# 4. A failed trigger rolls back so the shared session survives the cycle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_due_triggers_rolls_back_on_failed_trigger(monkeypatch):
    """If ``fire_notification`` raises for one trigger, the shared session
    must be rolled back so the remaining triggers still process and the final
    commit succeeds. (Without rollback a poisoned session would force every
    subsequent trigger to fail with PendingRollbackError.)"""
    from app.services import notification_manager as nm

    monkeypatch.setattr(nm, "DATABASE_AVAILABLE", True)
    monkeypatch.setattr(nm, "AsyncSessionLocal", MagicMock())

    t_bad = MagicMock(id="bad", trigger_type=TriggerType.TIME, config={})
    t_ok = MagicMock(id="ok", trigger_type=TriggerType.TIME, config={})

    fake_session = AsyncMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = [t_bad, t_ok]
    fake_session.execute = AsyncMock(return_value=res)
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()

    fire_spy = AsyncMock(side_effect=[RuntimeError("boom"), None])
    monkeypatch.setattr(nm.NotificationManager, "fire_notification", fire_spy)

    await nm.NotificationManager.process_due_triggers(session=fake_session)

    assert fire_spy.await_count == 2, "second trigger must still run after the first failed"
    fake_session.rollback.assert_awaited()
    fake_session.commit.assert_awaited()
