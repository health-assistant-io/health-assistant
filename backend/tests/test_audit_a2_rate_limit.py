"""Tests for audit item A2 — Redis-backed rate limiting on auth endpoints."""
import types

import pytest

from app.core import rate_limit as rl_mod


class FakeRedis:
    """Deterministic in-memory stand-in for the async redis client."""

    def __init__(self):
        self.store: dict[str, int] = {}
        self.fail = False

    async def incr(self, key: str) -> int:
        if self.fail:
            raise RuntimeError("redis down")
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> None:
        return


class FakeRequest:
    def __init__(self, ip="1.2.3.4", forwarded=None):
        self.client = types.SimpleNamespace(host=ip)
        h = {}
        if forwarded:
            h["x-forwarded-for"] = forwarded
        self.headers = h


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rl_mod, "redis_client", fake)
    return fake


class TestClientIP:
    def test_uses_forwarded_first_hop(self):
        req = FakeRequest(forwarded="203.0.113.5, 10.0.0.1")
        assert rl_mod._client_ip(req) == "203.0.113.5"

    def test_falls_back_to_peer(self):
        assert rl_mod._client_ip(FakeRequest(ip="10.0.0.9")) == "10.0.0.9"


class TestRateLimitLogic:
    @pytest.mark.asyncio
    async def test_allows_under_limit(self, fake_redis):
        dep = rl_mod.rate_limit("login", max_requests=5, window=60)
        for _ in range(5):
            await dep(FakeRequest())  # no raise

    @pytest.mark.asyncio
    async def test_blocks_over_limit_with_429(self, fake_redis):
        from fastapi import HTTPException

        dep = rl_mod.rate_limit("login", max_requests=3, window=60)
        for _ in range(3):
            await dep(FakeRequest())
        with pytest.raises(HTTPException) as exc:
            await dep(FakeRequest())
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers

    @pytest.mark.asyncio
    async def test_separate_buckets_per_ip(self, fake_redis):
        from fastapi import HTTPException

        dep = rl_mod.rate_limit("login", max_requests=2, window=60)
        await dep(FakeRequest(ip="1.1.1.1"))
        await dep(FakeRequest(ip="1.1.1.1"))
        # A different IP has its own bucket.
        await dep(FakeRequest(ip="2.2.2.2"))
        # Original IP now exceeds.
        with pytest.raises(HTTPException):
            await dep(FakeRequest(ip="1.1.1.1"))

    @pytest.mark.asyncio
    async def test_degrades_open_when_redis_down(self, fake_redis):
        fake_redis.fail = True
        dep = rl_mod.rate_limit("login", max_requests=1, window=60)
        # Redis unreachable → requests allowed (availability over blocking).
        for _ in range(50):
            await dep(FakeRequest())


class TestAuthEndpointsWired:
    """Static guard: the sensitive auth routes carry a rate_limit dependency."""

    def test_login_register_refresh_invite_are_rate_limited(self):
        import inspect
        from app.api.v1.endpoints import auth

        for name in ("login", "register", "refresh_token", "create_invite"):
            fn = getattr(auth, name)
            src = inspect.getsource(fn)
            assert "rate_limit" in src, f"{name} must depend on rate_limit"
