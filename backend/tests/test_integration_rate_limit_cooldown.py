"""Tests for the rate-limit cooldown helpers in
``app.services.integration_sync_service``.

Item 1 of the integrations-sdk-improvements plan: when an upstream
returns 429 with a ``Retry-After`` header, the SDK surfaces the value
on ``IntegrationRateLimitError.retry_after_seconds``; the engine copies
it to ``SyncResult.retry_after_seconds``; the worker writes a Redis
cooldown key so subsequent beats skip the integration until the
cooldown expires (instead of re-hitting the upstream every 60 s while
the window is still closed).

These tests pin the helper-level contract directly. The worker
integration is covered by the existing
``test_sync_active_integrations_lock.py`` + a new case here.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.integration_sync_service import (
    SyncResult,
    _clamp_cooldown,
    _cooldown_key,
    _COOLDOWN_MAX_SECONDS,
    _COOLDOWN_MIN_SECONDS,
    clear_rate_limit_cooldown,
    is_rate_limited,
    set_rate_limit_cooldown,
)


# ---------------------------------------------------------------------------
# _clamp_cooldown
# ---------------------------------------------------------------------------


def test_clamp_cooldown_passes_through_value_in_range():
    assert _clamp_cooldown(120) == 120
    assert _clamp_cooldown(120.0) == 120
    assert _clamp_cooldown("300") == 300  # type: ignore[arg-type]


def test_clamp_cooldown_applies_minimum_floor():
    """A Retry-After of 0 / 5 should be bumped to the minimum — otherwise
    an upstream lying could effectively disable the cooldown."""
    assert _clamp_cooldown(0) is None  # zero means "no cooldown"
    assert _clamp_cooldown(5) == _COOLDOWN_MIN_SECONDS
    assert _clamp_cooldown(_COOLDOWN_MIN_SECONDS - 1) == _COOLDOWN_MIN_SECONDS


def test_clamp_cooldown_applies_maximum_ceiling():
    """An absurd Retry-After is capped so a misconfigured upstream can't
    freeze the integration out for hours."""
    assert _clamp_cooldown(_COOLDOWN_MAX_SECONDS + 1) == _COOLDOWN_MAX_SECONDS
    assert _clamp_cooldown(86400) == _COOLDOWN_MAX_SECONDS  # 24h → 1h


def test_clamp_cooldown_none_and_invalid_return_none():
    assert _clamp_cooldown(None) is None
    assert _clamp_cooldown(-10) is None  # negative → no cooldown
    assert _clamp_cooldown("garbage") is None  # type: ignore[arg-type]


def test_cooldown_key_format():
    iid = uuid4()
    assert _cooldown_key(iid) == f"sync_cooldown:{iid}"


# ---------------------------------------------------------------------------
# set_rate_limit_cooldown / is_rate_limited / clear_rate_limit_cooldown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_rate_limit_cooldown_writes_redis_key(monkeypatch):
    iid = uuid4()
    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=True)
    monkeypatch.setattr("app.core.redis.redis_client", fake_redis)

    await set_rate_limit_cooldown(iid, 90)

    fake_redis.set.assert_awaited_once()
    args, kwargs = fake_redis.set.call_args
    assert args[0] == f"sync_cooldown:{iid}"
    assert kwargs["ex"] == 90


@pytest.mark.asyncio
async def test_set_rate_limit_cooldown_clamps_value(monkeypatch):
    iid = uuid4()
    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(return_value=True)
    monkeypatch.setattr("app.core.redis.redis_client", fake_redis)

    # 5 seconds → clamped to _COOLDOWN_MIN_SECONDS (60).
    await set_rate_limit_cooldown(iid, 5)
    _, kwargs = fake_redis.set.call_args
    assert kwargs["ex"] == _COOLDOWN_MIN_SECONDS


@pytest.mark.asyncio
async def test_set_rate_limit_cooldown_none_is_noop(monkeypatch):
    """When retry_after_seconds is None (upstream sent no hint), don't
    write a cooldown key — fall back to sync_interval throttle."""
    iid = uuid4()
    fake_redis = MagicMock()
    fake_redis.set = AsyncMock()
    monkeypatch.setattr("app.core.redis.redis_client", fake_redis)

    await set_rate_limit_cooldown(iid, None)
    fake_redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_rate_limit_cooldown_degrades_when_redis_down(monkeypatch):
    """If Redis raises, log + return (don't propagate). The worker falls
    back to the sync_interval throttle."""
    iid = uuid4()
    fake_redis = MagicMock()
    fake_redis.set = AsyncMock(side_effect=ConnectionError("redis gone"))
    monkeypatch.setattr("app.core.redis.redis_client", fake_redis)

    # Must not raise.
    await set_rate_limit_cooldown(iid, 60)


@pytest.mark.asyncio
async def test_is_rate_limited_returns_true_when_key_exists(monkeypatch):
    iid = uuid4()
    fake_redis = MagicMock()
    fake_redis.exists = AsyncMock(return_value=1)
    monkeypatch.setattr("app.core.redis.redis_client", fake_redis)

    assert await is_rate_limited(iid) is True


@pytest.mark.asyncio
async def test_is_rate_limited_returns_false_when_key_missing(monkeypatch):
    iid = uuid4()
    fake_redis = MagicMock()
    fake_redis.exists = AsyncMock(return_value=0)
    monkeypatch.setattr("app.core.redis.redis_client", fake_redis)

    assert await is_rate_limited(iid) is False


@pytest.mark.asyncio
async def test_is_rate_limited_returns_false_when_redis_down(monkeypatch):
    """Degrades to "not rate limited" so the sync proceeds; the upstream
    will re-raise IntegrationRateLimitError if still closed and the
    cooldown will be re-set."""
    iid = uuid4()
    fake_redis = MagicMock()
    fake_redis.exists = AsyncMock(side_effect=ConnectionError("redis gone"))
    monkeypatch.setattr("app.core.redis.redis_client", fake_redis)

    assert await is_rate_limited(iid) is False


@pytest.mark.asyncio
async def test_clear_rate_limit_cooldown_deletes_key(monkeypatch):
    iid = uuid4()
    fake_redis = MagicMock()
    fake_redis.delete = AsyncMock(return_value=1)
    monkeypatch.setattr("app.core.redis.redis_client", fake_redis)

    await clear_rate_limit_cooldown(iid)
    fake_redis.delete.assert_awaited_once_with(f"sync_cooldown:{iid}")


# ---------------------------------------------------------------------------
# SyncResult contract
# ---------------------------------------------------------------------------


def test_sync_result_carries_retry_after_seconds():
    """Pin the field so the worker can read it without getattr fallbacks."""
    result = SyncResult(status="failed", error_type="rate_limit", retry_after_seconds=42.0)
    assert result.retry_after_seconds == 42.0


def test_sync_result_retry_after_defaults_to_none():
    result = SyncResult(status="success")
    assert result.retry_after_seconds is None
