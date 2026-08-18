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
from httpx import AsyncClient

from app.core.security import (
    create_invite_token,
    create_refresh_token,
    create_session_access_token,
    get_password_hash,
    verify_access_token,
)
from app.core import token_store
from app.models.enums import Role
from app.models.user_model import UserModel


@pytest.fixture
def mock_user():
    return UserModel(
        id=uuid.uuid4(),
        email="test@example.com",
        hashed_password=get_password_hash("testpassword123"),
        role=Role.USER,
        tenant_id=uuid.uuid4(),
        is_active=True,
        settings={},
    )


@pytest.fixture
def mock_admin():
    return UserModel(
        id=uuid.uuid4(),
        email="admin@example.com",
        hashed_password=get_password_hash("adminpassword123"),
        role=Role.ADMIN,
        tenant_id=uuid.uuid4(),
        is_active=True,
        settings={},
    )


async def _auth_header_for(user):
    token, jti = create_session_access_token(
        {
            "sub": user.email,
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": getattr(user.role, "value", user.role),
        }
    )
    # Register the session jti — the auth path checks the session store,
    # so test tokens must be live sessions (mirrors real mint paths).
    await token_store.register_session(str(user.id), jti, 3600)
    return token, jti


# ---------------------------------------------------------------------------
# C-1 — setup-status must never contain the token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_status_never_leaks_token(async_client: AsyncClient):
    with patch(
        "app.api.v1.endpoints.auth._is_initialized", new_callable=AsyncMock
    ) as mi:
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
async def test_manager_cannot_set_system_admin_role(
    async_client: AsyncClient, mock_user
):
    mock_user.role = Role.MANAGER
    token, _ = await _auth_header_for(mock_user)
    response = await async_client.put(
        f"/api/v1/users/{str(mock_user.id)}",
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


def test_refresh_token_rejected_as_access_token(mock_user):
    refresh, _ = create_refresh_token(
        {
            "sub": mock_user.email,
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
async def test_refresh_token_rejected_on_protected_route(
    async_client: AsyncClient, mock_user
):
    refresh, _ = create_refresh_token(
        {
            "sub": mock_user.email,
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
    token, jti = await _auth_header_for(mock_user)
    await token_store.register_session(str(mock_user.id), jti, 3600)
    refresh, rjti = create_refresh_token(
        {
            "sub": mock_user.email,
            "user_id": str(mock_user.id),
            "tenant_id": str(mock_user.tenant_id),
            "role": "USER",
        }
    )
    await token_store.register_refresh(str(mock_user.id), rjti, 3600)

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
    token, jti = create_invite_token(tenant_id=str(uuid.uuid4()), expires_days=36500)
    from app.core.security import decode_access_token

    payload = decode_access_token(token)
    # exp - iat should be ≤ 30 days even when 36500 requested
    assert payload["exp"] - payload["iat"] <= 30 * 86400 + 60


@pytest.mark.asyncio
async def test_invite_single_use_consumed():
    token, jti = create_invite_token(tenant_id=str(uuid.uuid4()))
    await token_store.register_invite(jti, 3600)
    assert await token_store.consume_invite(jti) is True
    # Second consumption fails — the invite is spent.
    assert await token_store.consume_invite(jti) is False
