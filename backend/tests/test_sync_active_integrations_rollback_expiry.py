"""Regression tests for the sync-cycle rollback expiry (MissingGreenlet).

Pre-fix contract: ``run_sync``'s error paths ``await db.rollback()``, and
an AsyncSession rollback expires EVERY object in the identity map
(``expire_on_commit=False`` only covers commits). The beat loop kept
reading plain attributes (``integration.id``, ``.provider`` …) off those
expired rows on the NEXT iteration — sync IO outside greenlet context —
so one failing integration (e.g. an unreachable FHIR host) aborted the
rest of the cycle with "greenlet_spawn has not been called; can't call
await_() here" (sqlalche.me/e/21/xd2s) while the task still reported
success.

Post-fix contract pinned here:
1. The loop decides on snapshotted plain values — no ORM attribute reads
   after a sync ran.
2. Each iteration re-acquires its row via ``await db.get(...)`` (the
   awaitable identity-map refresh path) before handing it to ``run_sync``.
3. A failing sync for one integration no longer prevents the remaining
   integrations from syncing in the same cycle, and no "Critical error"
   is logged.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import MissingGreenlet

from app.workers import tasks as worker_tasks


class _ExpiringIntegration:
    """ORM-row double that mimics AsyncSession rollback expiry: once
    expired, ANY attribute read raises MissingGreenlet, exactly like a
    real expired instance accessed outside greenlet context."""

    def __init__(self, provider, integration_id=None):
        object.__setattr__(
            self,
            "_data",
            {
                "id": integration_id or uuid.uuid4(),
                "provider": provider,
                "tenant_id": uuid.uuid4(),
                "user_id": uuid.uuid4(),
                "status": "ACTIVE",
                "user_config": {},
                "last_synced_at": None,
                "is_debug_enabled": False,
                "instance_name": "default",
            },
        )
        object.__setattr__(self, "_expired", False)

    def expire(self):
        object.__setattr__(self, "_expired", True)

    def __getattribute__(self, name):
        if name.startswith("_") or name == "expire":
            return object.__getattribute__(self, name)
        if object.__getattribute__(self, "_expired"):
            raise MissingGreenlet("greenlet_spawn has not been called; can't call await_() here.")
        return object.__getattribute__(self, "_data")[name]


def _async_engine():
    e = MagicMock()
    e.dispose = AsyncMock()
    return e


def _db_with_integrations(integrations):
    """Async-CM mock DB whose rollback expires every row (the real
    AsyncSession semantics the fix guards against) and whose ``get``
    answers with a refreshed (unexpired) row of the same identity."""

    async def _execute(stmt, *args, **kwargs):
        text = str(stmt)
        if "FROM user_integrations" in text and "integration_sync_state" not in text:
            result = MagicMock()
            result.scalars = MagicMock(
                return_value=MagicMock(all=MagicMock(return_value=integrations))
            )
            return result
        result = MagicMock()
        result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        return result

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    db.execute = AsyncMock(side_effect=_execute)
    db.rollback = AsyncMock(side_effect=lambda: [i.expire() for i in integrations])

    # read before any expiry
    ids = [(i.id, i.provider, i.user_id, i.tenant_id) for i in integrations]

    async def _get(model, id_):
        if not any(iid == id_ for iid, *_ in ids):
            return None
        iid, provider, user_id, tenant_id = next(t for t in ids if t[0] == id_)
        row = MagicMock()
        row.id = iid
        row.provider = provider
        row.user_id = user_id
        row.tenant_id = tenant_id
        row.user_config = {}
        row.last_synced_at = None
        row.instance_name = "default"
        row.is_debug_enabled = False
        return row

    db.get = AsyncMock(side_effect=_get)
    return db


@pytest.mark.asyncio
async def test_failing_sync_does_not_kill_remaining_cycle():
    """A rollback inside run_sync (first integration fails) must not stop
    the second integration from syncing — and no Critical error is logged."""
    rows = [
        _ExpiringIntegration("fhir_client"),
        _ExpiringIntegration("withings"),
    ]
    db = _db_with_integrations(rows)

    sync_calls = []

    async def _fake_run_sync(db, integration, provider, source=None):
        sync_calls.append((integration.id, integration.provider))
        await db.rollback()  # expires every identity-map object, as real error paths do
        failed = MagicMock()
        failed.status = "failed"
        failed.error = "could not resolve fhir.example.org — failing closed (no pin)"
        return failed

    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=True)
    fake_redis.delete = AsyncMock(return_value=1)

    provider = MagicMock()

    critical_errors = []

    def _error(fmt, *a, **kw):
        critical_errors.append(fmt % a if a else fmt)

    with (
        patch.object(worker_tasks, "get_async_session", return_value=(db, _async_engine())),
        patch.object(worker_tasks, "integration_registry"),
        patch("app.core.redis.redis_client", fake_redis),
        patch("app.services.integration_sync_service.run_sync", _fake_run_sync),
        patch.object(worker_tasks.logger, "error", _error),
    ):
        worker_tasks.integration_registry.initialize = AsyncMock()
        worker_tasks.integration_registry.get_provider = MagicMock(return_value=provider)

        await worker_tasks.sync_active_integrations.__wrapped__.__wrapped__(None)

    assert len(sync_calls) == 2, (
        "Both integrations must sync in the same cycle; a rollback during "
        f"the first sync aborted the rest (synced: {[str(i) for i in sync_calls]})"
    )
    assert [p for _, p in sync_calls] == ["fhir_client", "withings"], (
        "The second sync must run against the re-acquired row, not a dead object"
    )
    assert not any("Critical error" in e for e in critical_errors), (
        f"No Critical error expected once the loop survives rollback expiry; got: {critical_errors}"
    )
