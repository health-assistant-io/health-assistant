"""Tests for audit item A5 — refresh-token rotation + revocation."""

import pytest

from app.core import token_store
from app.core.security import (
    REFRESH_TOKEN_KIND,
    create_refresh_token,
    create_session_access_token,
    decode_refresh_token,
)


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.fail = False

    async def set(self, k, v, ex=None):
        if self.fail:
            raise RuntimeError("down")
        self.store[k] = v

    async def exists(self, k):
        if self.fail:
            raise RuntimeError("down")
        return 1 if k in self.store else 0

    async def delete(self, k):
        if self.fail:
            raise RuntimeError("down")
        return self.store.pop(k, None) is not None

    async def scan_iter(self, pattern):
        if self.fail:
            raise RuntimeError("down")
        import fnmatch

        for k in list(self.store.keys()):
            if fnmatch.fnmatch(k, pattern):
                yield k


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(token_store, "redis_client", fake)
    return fake


CLAIMS = {
    "user_id": "user-123",
    "tenant_id": "tenant-1",
    "role": "USER",
    "ver": 1,
}


class TestRefreshTokenShape:
    def test_refresh_token_has_kind_and_jti(self):
        token, jti = create_refresh_token(CLAIMS)
        assert jti
        payload = decode_refresh_token(token)
        assert payload is not None
        assert payload["token_kind"] == REFRESH_TOKEN_KIND
        assert payload["jti"] == jti

    @pytest.mark.contract  # §18.1 — kind mismatch: access token ≠ refresh
    def test_access_token_not_accepted_as_refresh(self):
        access, _jti = create_session_access_token(CLAIMS)
        # A session token has token_kind="session" → rejected at /refresh.
        assert decode_refresh_token(access) is None

    @pytest.mark.contract  # §18.1 — garbage token rejected
    def test_garbage_token_rejected(self):
        assert decode_refresh_token("not-a-jwt") is None


class TestRotationAndRevocation:
    @pytest.mark.asyncio
    async def test_register_then_active(self, fake_redis):
        _token, jti = create_refresh_token(CLAIMS)
        await token_store.register_refresh("user-123", jti, 60)
        assert await token_store.is_active("user-123", jti) is True

    @pytest.mark.asyncio
    async def test_revoked_not_active(self, fake_redis):
        _token, jti = create_refresh_token(CLAIMS)
        await token_store.register_refresh("user-123", jti, 60)
        await token_store.revoke_refresh("user-123", jti)
        assert await token_store.is_active("user-123", jti) is False

    @pytest.mark.asyncio
    async def test_unknown_jti_not_active(self, fake_redis):
        assert await token_store.is_active("user-123", "never-issued") is False

    @pytest.mark.asyncio
    async def test_revoke_all_clears_user(self, fake_redis):
        _t1, j1 = create_refresh_token(CLAIMS)
        _t2, j2 = create_refresh_token(CLAIMS)
        await token_store.register_refresh("user-123", j1, 60)
        await token_store.register_refresh("user-123", j2, 60)
        count = await token_store.revoke_all_refresh("user-123")
        assert count == 2
        assert await token_store.is_active("user-123", j1) is False
        assert await token_store.is_active("user-123", j2) is False

    @pytest.mark.asyncio
    async def test_degrades_open_when_redis_down(self, fake_redis):
        fake_redis.fail = True
        # When Redis is unreachable, is_active returns True (availability).
        assert await token_store.is_active("user-123", "x") is True


class TestEndpointsWired:
    def test_refresh_rotates_and_logout_exists(self):
        import inspect

        from app.api.v1.endpoints import auth
        from app.services import auth_session_service

        # /refresh must revoke the old jti + register a new one (rotation).
        # Registration happens in the shared issuance path
        # (auth_session_service.issue_session, which refresh calls with
        # family_id=… to rotate the family) — assert across both.
        src = inspect.getsource(auth.refresh_token) + inspect.getsource(
            auth_session_service.issue_session
        )
        assert "revoke_refresh" in src
        assert "register_refresh" in src
        assert "decode_refresh_token" in src
        # logout + logout-all endpoints exist.
        assert hasattr(auth, "logout")
        assert hasattr(auth, "logout_all")
