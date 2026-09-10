"""Audit 2026-09-11 S-4 — per-user rate limits on the LLM endpoints.

``rate_limit_user`` keys the fixed-window bucket on the authenticated
``user_id`` (degrading to the client IP when the identity carries none) and
degrades open exactly like the per-IP limiter.
"""

from __future__ import annotations

import types
import uuid

import pytest

from app.core import rate_limit as rl_mod
from tests.test_audit_a2_rate_limit import FakeRedis, FakeRequest


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rl_mod, "redis_client", fake)
    return fake


@pytest.mark.asyncio
async def test_user_limit_keys_on_user_id(fake_redis):
    dep = rl_mod.rate_limit_user("ai_chat", max_requests=3, window=300)
    user = types.SimpleNamespace(user_id=uuid.uuid4())
    for _ in range(3):
        await dep(FakeRequest(), user)
    assert fake_redis.store and all(":u:" in k for k in fake_redis.store)


@pytest.mark.asyncio
async def test_user_limit_blocks_over_budget(fake_redis):
    from fastapi import HTTPException

    dep = rl_mod.rate_limit_user("ai_chat", max_requests=3, window=300)
    user = types.SimpleNamespace(user_id=uuid.uuid4())
    for _ in range(3):
        await dep(FakeRequest(), user)
    with pytest.raises(HTTPException) as exc:
        await dep(FakeRequest(), user)
    assert exc.value.status_code == 429
    assert exc.value.headers.get("Retry-After")


@pytest.mark.asyncio
async def test_user_limit_is_per_user(fake_redis):
    dep = rl_mod.rate_limit_user("ai_chat", max_requests=1, window=300)
    user_a = types.SimpleNamespace(user_id=uuid.uuid4())
    user_b = types.SimpleNamespace(user_id=uuid.uuid4())
    await dep(FakeRequest(), user_a)
    await dep(FakeRequest(), user_b)


@pytest.mark.asyncio
async def test_user_limit_degrades_open_when_redis_down(fake_redis):
    fake_redis.fail = True
    dep = rl_mod.rate_limit_user("ai_chat", max_requests=1, window=300)
    user = types.SimpleNamespace(user_id=uuid.uuid4())
    for _ in range(5):
        await dep(FakeRequest(), user)


@pytest.mark.asyncio
async def test_ai_endpoints_carry_limiter(async_client, fake_redis, monkeypatch):
    """Source-level pin: the five LLM-costing routes must declare the
    ``rate_limit_user`` dependency (audit S-4 follow-through)."""
    import inspect

    from app.api.v1.endpoints import ai_assistance as mod

    src = inspect.getsource(mod)
    for name in (
        "async def assist_user(",
        "async def assist_user_stream(",
        "async def transcribe(",
        "async def resolve_hitl_task(",
        "async def resume_hitl_session(",
    ):
        idx = src.index(name)
        fn_src = src[idx:]
        head = fn_src[: fn_src.index("):\n") if "):\n" in fn_src else 0]
        assert "rate_limit_user(" in head, f"{name} lacks rate_limit_user"
