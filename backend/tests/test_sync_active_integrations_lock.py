"""Regression tests for audit item C4 — ``sync_active_integrations`` lock.

Pre-fix contract: the 60-second Celery beat could overlap when a sync
took >60 s. Two workers both read ``last_synced_at``, both pulled, both
persisted Observations + telemetry — no dedup anywhere in the sync path
→ duplicate clinical rows after every overlapping sync.

Post-fix contract pinned here:
1. Each integration's sync attempts to acquire a Redis lock
   ``sync_lock:{integration_id}`` with ``NX`` (set-if-not-exists) and a
   600 s TTL.
2. If the lock isn't acquired, that integration is SKIPPED for this
   beat cycle (the other worker is already syncing it).
3. The lock is released (delete) in a ``finally`` block on success or
   failure. If the worker crashes, the TTL expires the lock.
4. Redis-down degrades gracefully to the legacy always-sync mode but
   logs a warning.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers import tasks as worker_tasks


def _async_engine():
    """Build a fake async engine whose dispose() is awaitable."""
    e = MagicMock()
    e.dispose = AsyncMock()
    return e



def _integration(provider="withings", integration_id=None):
    fake = MagicMock()
    fake.id = integration_id or uuid.uuid4()
    fake.provider = provider
    fake.tenant_id = uuid.uuid4()
    fake.user_id = uuid.uuid4()
    fake.status = "ACTIVE"
    fake.user_config = {}
    fake.last_synced_at = None
    fake.is_debug_enabled = False
    fake.instance_name = "default"
    return fake


def _db_with_integrations(integrations):
    """Build an async-CM mock DB that returns the given integrations list."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    db.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(
                return_value=MagicMock(all=MagicMock(return_value=integrations))
            )
        )
    )
    return db


# ---------------------------------------------------------------------------
# C4: per-integration lock is acquired before sync and released after
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_active_integrations_acquires_lock_per_integration():
    """Each integration's sync attempts a Redis ``set(NX, EX=600)`` on
    a per-integration lock key."""
    integ = _integration()

    # Mock DB
    db = _db_with_integrations([integ])
    db.flush = AsyncMock()

    # Mock Redis — capture all .set calls
    redis_set_calls = []

    async def _set(key, value, nx=None, ex=None):
        redis_set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        return True  # lock acquired

    async def _delete(key):
        return 1

    fake_redis = MagicMock()
    fake_redis.set = _set
    fake_redis.delete = _delete

    # Mock provider — no observations pulled, no push needed
    provider = MagicMock()
    provider.pull_data = AsyncMock(return_value=[])
    provider.push_data = AsyncMock()

    with patch.object(worker_tasks, "get_async_session", return_value=(db, _async_engine())), \
         patch.object(worker_tasks, "integration_registry"), \
         patch("app.core.redis.redis_client", fake_redis):
        worker_tasks.integration_registry.initialize = AsyncMock()
        worker_tasks.integration_registry.get_provider = MagicMock(return_value=provider)

        await worker_tasks.sync_active_integrations.__wrapped__.__wrapped__(None)

    assert redis_set_calls, "Expected at least one Redis set() call for the sync lock"
    call = redis_set_calls[0]
    assert call["key"] == f"sync_lock:{integ.id}", (
        f"Lock key should be 'sync_lock:{{integration_id}}'; got {call['key']}"
    )
    assert call["nx"] is True, "Lock must be acquired with NX (set-if-not-exists)"
    assert call["ex"] == 600, "Lock must have a 600s TTL (sync hard timeout)"


@pytest.mark.asyncio
async def test_sync_active_integrations_skips_when_lock_not_acquired():
    """If Redis returns False (lock held by another worker), the
    integration is skipped — no provider.pull_data called."""
    integ = _integration()

    db = _db_with_integrations([integ])

    async def _set(key, value, nx=None, ex=None):
        return False  # lock NOT acquired

    fake_redis = MagicMock()
    fake_redis.set = _set
    fake_redis.delete = AsyncMock()

    provider = MagicMock()
    provider.pull_data = AsyncMock()

    with patch.object(worker_tasks, "get_async_session", return_value=(db, _async_engine())), \
         patch.object(worker_tasks, "integration_registry"), \
         patch("app.core.redis.redis_client", fake_redis):
        worker_tasks.integration_registry.initialize = AsyncMock()
        worker_tasks.integration_registry.get_provider = MagicMock(return_value=provider)

        await worker_tasks.sync_active_integrations.__wrapped__.__wrapped__(None)

    provider.pull_data.assert_not_awaited(), (
        "pull_data must NOT be called when the Redis lock is held by another worker"
    )


@pytest.mark.asyncio
async def test_sync_active_integrations_releases_lock_on_success():
    """On successful sync, the lock is deleted."""
    integ = _integration()

    db = _db_with_integrations([integ])

    async def _set(key, value, nx=None, ex=None):
        return True

    delete_calls = []

    async def _delete(key):
        delete_calls.append(key)
        return 1

    fake_redis = MagicMock()
    fake_redis.set = _set
    fake_redis.delete = _delete

    provider = MagicMock()
    provider.pull_data = AsyncMock(return_value=[])
    provider.push_data = AsyncMock()

    with patch.object(worker_tasks, "get_async_session", return_value=(db, _async_engine())), \
         patch.object(worker_tasks, "integration_registry"), \
         patch("app.core.redis.redis_client", fake_redis):
        worker_tasks.integration_registry.initialize = AsyncMock()
        worker_tasks.integration_registry.get_provider = MagicMock(return_value=provider)

        await worker_tasks.sync_active_integrations.__wrapped__.__wrapped__(None)

    assert any(k == f"sync_lock:{integ.id}" for k in delete_calls), (
        "Lock must be released (delete) on successful sync"
    )


@pytest.mark.asyncio
async def test_sync_active_integrations_releases_lock_on_sync_failure():
    """On sync failure (provider raises), the lock is still deleted."""
    integ = _integration()

    db = _db_with_integrations([integ])

    async def _set(key, value, nx=None, ex=None):
        return True

    delete_calls = []

    async def _delete(key):
        delete_calls.append(key)
        return 1

    fake_redis = MagicMock()
    fake_redis.set = _set
    fake_redis.delete = _delete

    provider = MagicMock()
    provider.pull_data = AsyncMock(side_effect=RuntimeError("sync blew up"))

    with patch.object(worker_tasks, "get_async_session", return_value=(db, _async_engine())), \
         patch.object(worker_tasks, "integration_registry"), \
         patch("app.core.redis.redis_client", fake_redis):
        worker_tasks.integration_registry.initialize = AsyncMock()
        worker_tasks.integration_registry.get_provider = MagicMock(return_value=provider)

        # The exception is caught internally and logged — task does NOT re-raise.
        await worker_tasks.sync_active_integrations.__wrapped__.__wrapped__(None)

    assert any(k == f"sync_lock:{integ.id}" for k in delete_calls), (
        "Lock must be released (delete) in the finally block even when sync fails"
    )


@pytest.mark.asyncio
async def test_sync_active_integrations_degrades_when_redis_down():
    """If Redis is unavailable, the sync proceeds in legacy always-sync mode
    but logs a warning so operators notice the gap."""
    integ = _integration()

    db = _db_with_integrations([integ])

    # Redis.set raises → simulate Redis being down.
    fake_redis = MagicMock()

    async def _broken_set(*a, **kw):
        raise RuntimeError("connection refused")

    fake_redis.set = _broken_set
    fake_redis.delete = AsyncMock()

    provider = MagicMock()
    provider.pull_data = AsyncMock(return_value=[])
    provider.push_data = AsyncMock()

    warnings_seen = []

    def _warn(fmt, *a, **kw):
        warnings_seen.append(fmt % a if a else fmt)

    with patch.object(worker_tasks, "get_async_session", return_value=(db, _async_engine())), \
         patch.object(worker_tasks, "integration_registry"), \
         patch("app.core.redis.redis_client", fake_redis), \
         patch.object(worker_tasks.logger, "warning", _warn):
        worker_tasks.integration_registry.initialize = AsyncMock()
        worker_tasks.integration_registry.get_provider = MagicMock(return_value=provider)

        await worker_tasks.sync_active_integrations.__wrapped__.__wrapped__(None)

    # Provider.pull_data WAS called — legacy mode engaged.
    provider.pull_data.assert_awaited()
    # A warning was logged about Redis being unavailable.
    assert any("redis" in w.lower() or "lock" in w.lower() for w in warnings_seen), (
        "When Redis is down, operators must be warned that the dedup guard is off"
    )


@pytest.mark.asyncio
async def test_sync_active_integrations_multiple_integrations_independent_locks():
    """Two integrations get independent locks — sync of one doesn't block the other."""
    integ1 = _integration(provider="withings", integration_id=uuid.uuid4())
    integ2 = _integration(provider="fitbit", integration_id=uuid.uuid4())

    db = _db_with_integrations([integ1, integ2])

    set_calls = []

    async def _set(key, value, nx=None, ex=None):
        set_calls.append(key)
        return True

    fake_redis = MagicMock()
    fake_redis.set = _set
    fake_redis.delete = AsyncMock()

    provider = MagicMock()
    provider.pull_data = AsyncMock(return_value=[])
    provider.push_data = AsyncMock()

    with patch.object(worker_tasks, "get_async_session", return_value=(db, _async_engine())), \
         patch.object(worker_tasks, "integration_registry"), \
         patch("app.core.redis.redis_client", fake_redis):
        worker_tasks.integration_registry.initialize = AsyncMock()
        worker_tasks.integration_registry.get_provider = MagicMock(return_value=provider)

        await worker_tasks.sync_active_integrations.__wrapped__.__wrapped__(None)

    assert f"sync_lock:{integ1.id}" in set_calls
    assert f"sync_lock:{integ2.id}" in set_calls
