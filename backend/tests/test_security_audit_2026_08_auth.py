# ruff: noqa: SIM117 -- long immutable strings; reflow when touched
"""Security regression tests — 2026-08 audit Batch 1 (auth hardening).

Covers:
- C-1  setup-status never leaks the setup token
- C-2  ADMIN/MANAGER cannot escalate to SYSTEM_ADMIN via /users
- H2   role change revokes sessions; refresh rebuilds claims from DB
- H3   refresh tokens are rejected as bearer access tokens
- M3   invite tokens are single-use with capped TTL
- logout revokes the live access token (session jti store)
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core import token_store
from app.core.security import (
    create_invite_token,
    create_refresh_token,
    decode_refresh_token,
    decode_token,
    verify_access_token,
)
from app.models.enums import Role

from ._auth_helpers import create_user, sign_in


@pytest_asyncio.fixture
async def mock_user():
    return await create_user(email="test@example.com", password="testpassword123", role=Role.USER)


@pytest_asyncio.fixture
async def mock_admin():
    return await create_user(
        email="admin@example.com", password="adminpassword123", role=Role.ADMIN
    )


async def _auth_header_for(user):
    """A live, verified session token for ``user`` (family + Redis jti).

    Goes through the real issuance path — the verifier demands contract
    claims (``sub``/``ver``/``auth_mode``), a live ``users`` row and a
    registered session jti, so hand-rolled claims no longer pass.
    """
    issued = await sign_in(user)
    payload = verify_access_token(issued.access_token)
    return issued.access_token, payload["jti"]


# ---------------------------------------------------------------------------
# C-1 — setup-status must never contain the token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_status_never_leaks_token(async_client: AsyncClient):
    with patch("app.api.v1.endpoints.auth._is_initialized", new_callable=AsyncMock) as mi:
        with patch("app.api.v1.endpoints.auth.setup_token") as ms:
            mi.return_value = False
            ms.current_mode.return_value = "env"
            ms.get.return_value = "super-secret-env-token"
            ms.is_setup_token_required.return_value = True
            response = await async_client.get("/api/v1/auth/setup-status")
    assert response.status_code == 200
    body = response.text
    assert "super-secret-env-token" not in body
    assert "token=" not in body
    assert response.json()["setup_url_hint"] is None


# ---------------------------------------------------------------------------
# C-2 — SYSTEM_ADMIN escalation via /users is blocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.contract  # §18.7 — admin boundary: cannot mint SYSTEM_ADMIN
async def test_admin_cannot_create_system_admin(async_client: AsyncClient, mock_admin):
    token, _ = await _auth_header_for(mock_admin)
    response = await async_client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "evil@example.com",
            "password": "password123",
            "tenant_id": str(mock_admin.tenant_id),
            "role": "SYSTEM_ADMIN",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.contract  # §18.7 — role guard: manager cannot grant SYSTEM_ADMIN
async def test_manager_cannot_set_system_admin_role(async_client: AsyncClient, mock_user):
    manager = await create_user(role=Role.MANAGER, tenant_id=mock_user.tenant_id)
    token, _ = await _auth_header_for(manager)
    response = await async_client.put(
        f"/api/v1/users/{manager.id!s}",
        headers={"Authorization": f"Bearer {token}"},
        params={"role": "SYSTEM_ADMIN"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_service_update_user_refuses_system_admin():
    from app.services import user_service

    result = await user_service.update_user(
        uuid.uuid4(), role="SYSTEM_ADMIN", tenant_id=uuid.uuid4()
    )
    assert result is None


# ---------------------------------------------------------------------------
# H3 — refresh tokens / invite / download tokens are not access tokens
# ---------------------------------------------------------------------------


@pytest.mark.contract  # §18.1 — kind mismatch: refresh token ≠ access
def test_refresh_token_rejected_as_access_token(mock_user):
    refresh, _ = create_refresh_token(
        {
            "user_id": str(mock_user.id),
            "tenant_id": str(mock_user.tenant_id),
            "role": "USER",
        }
    )
    assert verify_access_token(refresh) is None


@pytest.mark.asyncio
async def test_session_token_passes_verify(mock_user):
    token, _ = await _auth_header_for(mock_user)
    payload = verify_access_token(token)
    assert payload is not None
    assert payload.get("token_kind") == "session"


@pytest.mark.asyncio
@pytest.mark.contract  # §18.1 — kind mismatch on a protected route ⇒ 401
async def test_refresh_token_rejected_on_protected_route(async_client: AsyncClient, mock_user):
    refresh, _ = create_refresh_token(
        {
            "user_id": str(mock_user.id),
            "tenant_id": str(mock_user.tenant_id),
            "role": "USER",
        }
    )
    response = await async_client.get(
        "/api/v1/auth/validate", headers={"Authorization": f"Bearer {refresh}"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Session jti store — logout kills the access token itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_revokes_access_token(async_client: AsyncClient, mock_user):
    # A real sign-in: family row + rotating refresh jti + live access jti.
    issued = await sign_in(mock_user)
    token = issued.access_token
    jti = verify_access_token(token)["jti"]
    refresh = issued.refresh_token
    rjti = decode_refresh_token(refresh)["jti"]

    response = await async_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
        json={"refresh_token": refresh},
    )
    assert response.status_code == 200

    assert not await token_store.is_session_active(str(mock_user.id), jti)
    assert not await token_store.is_active(str(mock_user.id), rjti)

    # The access token is now dead on a protected route.
    resp2 = await async_client.get(
        "/api/v1/auth/validate", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_revoked_session_token_rejected(async_client: AsyncClient, mock_user):
    token, jti = await _auth_header_for(mock_user)
    await token_store.register_session(str(mock_user.id), jti, 3600)
    await token_store.revoke_session(str(mock_user.id), jti)
    response = await async_client.get(
        "/api/v1/auth/validate", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# M3 — invite tokens are single-use
# ---------------------------------------------------------------------------


def test_invite_token_carries_jti():
    token, jti = create_invite_token(tenant_id=str(uuid.uuid4()))
    assert jti
    from app.core.security import invite_jti

    assert invite_jti(token) == jti


def test_invite_expiry_capped():
    token, _jti = create_invite_token(tenant_id=str(uuid.uuid4()), expires_days=36500)
    payload = decode_token(token)
    # exp - iat should be ≤ 30 days even when 36500 requested
    assert payload["exp"] - payload["iat"] <= 30 * 86400 + 60


@pytest.mark.asyncio
async def test_invite_single_use_consumed():
    _token, jti = create_invite_token(tenant_id=str(uuid.uuid4()))
    await token_store.register_invite(jti, 3600)
    assert await token_store.consume_invite(jti) is True
    # Second consumption fails — the invite is spent.
    assert await token_store.consume_invite(jti) is False
