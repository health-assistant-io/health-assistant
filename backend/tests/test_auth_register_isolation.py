"""Regression tests for audit item B7 — auth.register tenant impersonation.

Post-fix contract pinned here:
1. ``POST /auth/register`` is invite-only — a ``tenant_id`` + valid
   invite token minted by that tenant's admin is always required.
   Missing/invalid/wrong-tenant token → 403. The open bootstrap path
   (no ``tenant_id``) was removed and replaced by ``POST /auth/setup``
   (covered in ``test_auth_setup.py``).
2. Invite tokens are minted by ADMIN/MANAGER/SYSTEM_ADMIN via
   POST /auth/invite. USER → 403.
3. SYSTEM_ADMIN role can never be granted via invite token (setup is
   the only SYSTEM_ADMIN grantor).
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.endpoints import auth as auth_endpoint
from app.core.security import create_invite_token, verify_invite_token
from app.models.enums import Role
from app.schemas.auth import UserRegister
from app.schemas.user import TokenData


def _user(role=Role.USER.value, tenant_id=None) -> TokenData:
    return TokenData(
        sub="test@local",
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role=role,
    )


# ---------------------------------------------------------------------------
# B7: joining an existing tenant requires a valid invite token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_existing_tenant_without_invite_returns_403():
    """No invite_token + tenant_id provided → 403 (the core B7 hole)."""
    from fastapi import HTTPException

    tenant_id = uuid.uuid4()
    db = MagicMock()

    with patch.object(
        auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
    ), patch.object(
        auth_endpoint, "get_tenant", new=AsyncMock(return_value=MagicMock(id=tenant_id))
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_endpoint.register(
                user_data=UserRegister(
                    email="attacker@evil.com",
                    password="password123",
                    tenant_id=str(tenant_id),
                    invite_token=None,
                ),
                db=db,
            )
    assert exc.value.status_code == 403
    assert "invite" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_register_existing_tenant_with_invalid_invite_returns_403():
    """A bad/expired/wrong-tenant invite → 403."""
    from fastapi import HTTPException

    tenant_id = uuid.uuid4()
    other_tenant_id = uuid.uuid4()
    db = MagicMock()

    # Token minted for OTHER_TENANT — must not work for tenant_id.
    bad_token = create_invite_token(
        tenant_id=str(other_tenant_id), email="attacker@evil.com"
    )

    with patch.object(
        auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
    ), patch.object(
        auth_endpoint, "get_tenant", new=AsyncMock(return_value=MagicMock(id=tenant_id))
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_endpoint.register(
                user_data=UserRegister(
                    email="attacker@evil.com",
                    password="password123",
                    tenant_id=str(tenant_id),
                    invite_token=bad_token,
                ),
                db=db,
            )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_register_existing_tenant_with_valid_invite_succeeds():
    """A valid invite → user is created as USER (or token's role)."""
    tenant_id = uuid.uuid4()
    db = MagicMock()

    valid_token = create_invite_token(
        tenant_id=str(tenant_id), email="newuser@family.com", role="USER"
    )

    fake_new_user = MagicMock()
    fake_new_user.email = "newuser@family.com"

    with patch.object(
        auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
    ), patch.object(
        auth_endpoint, "get_tenant", new=AsyncMock(return_value=MagicMock(id=tenant_id))
    ), patch.object(
        auth_endpoint, "service_create_user", new=AsyncMock(return_value=fake_new_user)
    ) as creating:
        result = await auth_endpoint.register(
            user_data=UserRegister(
                email="newuser@family.com",
                password="password123",
                tenant_id=str(tenant_id),
                invite_token=valid_token,
            ),
            db=db,
        )

    creating.assert_awaited_once()
    args, kwargs = creating.await_args
    assert kwargs.get("role") == Role.USER.value
    assert kwargs.get("tenant_id") == str(tenant_id)


@pytest.mark.asyncio
async def test_register_existing_tenant_with_email_mismatch_invite_returns_403():
    """An email-bound invite cannot be used by a different email."""
    from fastapi import HTTPException

    tenant_id = uuid.uuid4()
    db = MagicMock()

    valid_token = create_invite_token(
        tenant_id=str(tenant_id), email="specific@family.com"
    )

    with patch.object(
        auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
    ), patch.object(
        auth_endpoint, "get_tenant", new=AsyncMock(return_value=MagicMock(id=tenant_id))
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_endpoint.register(
                user_data=UserRegister(
                    email="impostor@evil.com",
                    password="password123",
                    tenant_id=str(tenant_id),
                    invite_token=valid_token,
                ),
                db=db,
            )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_register_nonexistent_tenant_returns_404():
    """tenant_id that doesn't exist → 404, not 403 (no leak)."""
    from fastapi import HTTPException

    db = MagicMock()
    with patch.object(
        auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
    ), patch.object(auth_endpoint, "get_tenant", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await auth_endpoint.register(
                user_data=UserRegister(
                    email="x@y.com",
                    password="password123",
                    tenant_id=str(uuid.uuid4()),
                    invite_token="anything",
                ),
                db=db,
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_400():
    from fastapi import HTTPException

    existing = MagicMock()
    db = MagicMock()
    with patch.object(
        auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=existing)
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_endpoint.register(
                user_data=UserRegister(
                    email="dup@family.com",
                    password="password123",
                ),
                db=db,
            )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# B7: invite-token role grant can never be SYSTEM_ADMIN
# ---------------------------------------------------------------------------


def test_create_invite_token_refuses_system_admin():
    """The token issuer refuses to encode SYSTEM_ADMIN."""
    from fastapi import HTTPException  # noqa: F401  (clarifies intent)

    with pytest.raises(ValueError):
        create_invite_token(tenant_id=str(uuid.uuid4()), role=Role.SYSTEM_ADMIN.value)


def test_verify_invite_token_downgrades_system_admin_to_user():
    """Defense in depth: even a hand-crafted SYSTEM_ADMIN token is
    downgraded to USER on verify."""
    import jwt
    from app.core.config import settings

    tenant_id = str(uuid.uuid4())
    # Hand-craft a SYSTEM_ADMIN token bypassing the issuer's check.
    rogue_token = jwt.encode(
        {
            "sub": "invite",
            "tenant_id": tenant_id,
            "role": Role.SYSTEM_ADMIN.value,
            "exp": 9999999999,
            "iat": 0,
        },
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    ok, role = verify_invite_token(rogue_token, expected_tenant_id=tenant_id)
    assert ok is True
    assert role == Role.USER.value, "SYSTEM_ADMIN must be downgraded to USER on verify"


# ---------------------------------------------------------------------------
# B7: invite issuance (POST /auth/invite)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_user_role_forbidden():
    """A USER cannot mint invite tokens."""
    from fastapi import HTTPException

    user = _user(role=Role.USER.value)
    with pytest.raises(HTTPException) as exc:
        await auth_endpoint.create_invite(current_user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_invite_admin_role_succeeds_for_own_tenant():
    admin = _user(role=Role.ADMIN.value)
    result = await auth_endpoint.create_invite(current_user=admin)
    assert "invite_token" in result
    assert result["tenant_id"] == str(admin.tenant_id)
    # Verify the issued token round-trips.
    ok, role = verify_invite_token(result["invite_token"], str(admin.tenant_id))
    assert ok is True
    assert role == Role.USER.value


@pytest.mark.asyncio
async def test_invite_system_admin_can_target_other_tenant():
    """SYSTEM_ADMIN can invite into any tenant."""
    sysadmin = _user(role=Role.SYSTEM_ADMIN.value, tenant_id=uuid.uuid4())
    other_tenant = uuid.uuid4()
    result = await auth_endpoint.create_invite(
        tenant_id=str(other_tenant), current_user=sysadmin
    )
    assert result["tenant_id"] == str(other_tenant)


@pytest.mark.asyncio
async def test_invite_admin_cannot_target_other_tenant():
    """A non-SYSTEM_ADMIN admin cannot invite into a different tenant."""
    from fastapi import HTTPException

    admin = _user(role=Role.ADMIN.value, tenant_id=uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        await auth_endpoint.create_invite(
            tenant_id=str(uuid.uuid4()), current_user=admin
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_invite_refuses_system_admin_role():
    """The invite endpoint refuses to grant SYSTEM_ADMIN."""
    from fastapi import HTTPException

    admin = _user(role=Role.ADMIN.value)
    with pytest.raises(HTTPException) as exc:
        await auth_endpoint.create_invite(
            role=Role.SYSTEM_ADMIN.value, current_user=admin
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_invite_manager_role_succeeds():
    """MANAGER can also issue invites (tenant operator)."""
    manager = _user(role=Role.MANAGER.value)
    result = await auth_endpoint.create_invite(current_user=manager)
    assert "invite_token" in result


# ---------------------------------------------------------------------------
# B7: bootstrap path moved to /auth/setup — see test_auth_setup.py
# (advisory lock + first-user SYSTEM_ADMIN). register is now invite-only.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Static signature checks
# ---------------------------------------------------------------------------


def test_user_register_schema_has_invite_token_field():
    """Pydantic schema must expose invite_token so clients can pass it."""
    fields = UserRegister.model_fields
    assert "invite_token" in fields
