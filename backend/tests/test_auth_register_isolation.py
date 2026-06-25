"""Regression tests for audit item B7 — auth.register tenant impersonation.

Pre-fix contract:
- ``POST /auth/register`` accepted any ``tenant_id`` with no check that
  the caller was authorized to join. An attacker who learned a victim
  ``tenant_id`` could register inside it and immediately read tenant-
  scoped data.
- The "first user becomes SYSTEM_ADMIN" check used
  ``SELECT COUNT(*) FROM users`` with no locking, so two concurrent
  bootstrap registrations could both promote.

Post-fix contract pinned here:
1. Joining an existing tenant (tenant_id provided) requires a valid
   invite token minted by that tenant's admin. Missing/invalid/wrong-
   tenant token → 403.
2. Bootstrap (no tenant_id) creates a new tenant + SYSTEM_ADMIN user.
3. Invite tokens are minted by ADMIN/MANAGER/SYSTEM_ADMIN via
   POST /auth/invite. USER → 403.
4. SYSTEM_ADMIN role can never be granted via invite token (bootstrap
   is the only SYSTEM_ADMIN grantor).
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
# B7: bootstrap path is race-protected via pg_advisory_xact_lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_bootstrap_acquires_advisory_lock():
    """Bootstrap path (no tenant_id) must acquire the advisory lock BEFORE
    the count() check so concurrent registrations serialize."""
    tenant_id = uuid.uuid4()
    fake_tenant = MagicMock(id=tenant_id)
    fake_user = MagicMock()

    db = MagicMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    # First db.execute → advisory lock; second → COUNT() returning 0
    # (first user); any further calls return None-shaped results.
    lock_result = MagicMock()
    count_result = MagicMock()
    count_result.scalar.return_value = 0
    db.execute.side_effect = [lock_result, count_result]

    with patch.object(
        auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
    ), patch.object(
        auth_endpoint, "create_tenant", new=AsyncMock(return_value=fake_tenant)
    ):
        await auth_endpoint.register(
            user_data=UserRegister(
                email="first@family.com",
                password="password123",
            ),
            db=db,
        )

    # First execute call must be the advisory lock.
    first_call = db.execute.call_args_list[0]
    rendered = str(first_call.args[0])
    assert "pg_advisory_xact_lock" in rendered, (
        "Bootstrap path must call pg_advisory_xact_lock before the count() check"
    )


@pytest.mark.asyncio
async def test_register_bootstrap_first_user_gets_system_admin():
    """First ever user → SYSTEM_ADMIN."""
    tenant_id = uuid.uuid4()
    fake_tenant = MagicMock(id=tenant_id)

    db = MagicMock()
    db.execute = AsyncMock()
    lock_result = MagicMock()
    count_result = MagicMock()
    count_result.scalar.return_value = 0
    db.execute.side_effect = [lock_result, count_result]
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    # Capture the UserModel before commit.
    added = {}

    def _capture_add(obj):
        added["obj"] = obj

    db.add.side_effect = _capture_add

    with patch.object(
        auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
    ), patch.object(
        auth_endpoint, "create_tenant", new=AsyncMock(return_value=fake_tenant)
    ):
        await auth_endpoint.register(
            user_data=UserRegister(
                email="first@family.com",
                password="password123",
            ),
            db=db,
        )

    assert added["obj"].role == Role.SYSTEM_ADMIN


@pytest.mark.asyncio
async def test_register_bootstrap_subsequent_user_gets_admin():
    """Second+ bootstrap (creates their own tenant) → ADMIN, not SYSTEM_ADMIN."""
    tenant_id = uuid.uuid4()
    fake_tenant = MagicMock(id=tenant_id)

    db = MagicMock()
    db.execute = AsyncMock()
    lock_result = MagicMock()
    count_result = MagicMock()
    count_result.scalar.return_value = 1  # already a user
    db.execute.side_effect = [lock_result, count_result]
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    added = {}
    db.add.side_effect = lambda obj: added.setdefault("obj", obj)

    with patch.object(
        auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
    ), patch.object(
        auth_endpoint, "create_tenant", new=AsyncMock(return_value=fake_tenant)
    ):
        await auth_endpoint.register(
            user_data=UserRegister(
                email="second@family.com",
                password="password123",
            ),
            db=db,
        )

    assert added["obj"].role == Role.ADMIN


# ---------------------------------------------------------------------------
# Static signature checks
# ---------------------------------------------------------------------------


def test_user_register_schema_has_invite_token_field():
    """Pydantic schema must expose invite_token so clients can pass it."""
    fields = UserRegister.model_fields
    assert "invite_token" in fields
