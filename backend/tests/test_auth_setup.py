"""Tests for the first-run setup wizard endpoints.

Contract pinned here:
1. ``GET /auth/setup-status`` reports ``initialized`` (user count) and
   ``setup_token_required`` (non-localhost, non-dev).
2. ``POST /auth/setup`` is the only bootstrap path — creates the initial
   tenant + SYSTEM_ADMIN and returns login tokens.
3. ``setup`` is gated on uninitialized state (410 otherwise), the setup
   token (403 when required + missing/wrong), and email uniqueness (409).
4. ``setup`` race-protects via ``pg_advisory_xact_lock`` + a re-check
   inside the lock.
5. ``setup`` invalidates the token on success.
6. ``POST /auth/register`` is now invite-only — no-tenant_id bootstrap
   returns 403.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import auth as auth_endpoint
from app.core import setup_token
from app.models.enums import Role
from app.schemas.auth import SetupRequest, UserRegister


def _local_request() -> MagicMock:
    req = MagicMock()
    req.client = MagicMock(host="127.0.0.1")
    return req


def _remote_request() -> MagicMock:
    req = MagicMock()
    req.client = MagicMock(host="203.0.113.1")
    return req


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# setup-status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_status_uninitialized_local():
    """Localhost + no users → initialized=False, token not required."""
    with patch.object(
        auth_endpoint, "_is_initialized", new=AsyncMock(return_value=False)
    ):
        result = await auth_endpoint.setup_status(
            request=_local_request(), db=MagicMock()
        )
    assert result.initialized is False
    assert result.setup_token_required is False


@pytest.mark.asyncio
async def test_setup_status_uninitialized_remote_requires_token():
    """Remote + no users → token required."""
    with (
        patch.object(
            auth_endpoint, "_is_initialized", new=AsyncMock(return_value=False)
        ),
        patch.object(setup_token, "is_setup_token_required", return_value=True),
    ):
        result = await auth_endpoint.setup_status(
            request=_remote_request(), db=MagicMock()
        )
    assert result.initialized is False
    assert result.setup_token_required is True


@pytest.mark.asyncio
async def test_setup_status_initialized_never_needs_token():
    """Once initialized, the token is irrelevant."""
    with patch.object(
        auth_endpoint, "_is_initialized", new=AsyncMock(return_value=True)
    ):
        result = await auth_endpoint.setup_status(
            request=_remote_request(), db=MagicMock()
        )
    assert result.initialized is True
    assert result.setup_token_required is False


# ---------------------------------------------------------------------------
# setup — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_creates_system_admin_and_returns_tokens():
    """Uninitialized + localhost → SYSTEM_ADMIN created + tokens returned."""
    tenant_id = uuid.uuid4()
    fake_tenant = MagicMock(id=tenant_id)

    db = _mock_db()
    lock_result = MagicMock()
    db.execute.side_effect = [lock_result]  # only the advisory-lock call

    added = {}
    db.add.side_effect = lambda obj: added.setdefault("obj", obj)

    def _refresh(obj):
        obj.id = uuid.uuid4()

    db.refresh.side_effect = _refresh

    with (
        patch.object(
            auth_endpoint, "_is_initialized", new=AsyncMock(return_value=False)
        ),
        patch.object(
            auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
        ),
        patch.object(
            auth_endpoint, "create_tenant", new=AsyncMock(return_value=fake_tenant)
        ),
        patch.object(auth_endpoint, "setup_token") as tok_mod,
        patch.object(auth_endpoint, "token_store") as ts_mod,
    ):
        tok_mod.is_setup_token_required.return_value = False
        ts_mod.register_refresh = AsyncMock()
        ts_mod.register_session = AsyncMock()

        result = await auth_endpoint.setup(
            payload=SetupRequest(
                email="admin@example.com",
                password="securepassword",
                tenant_name="My Organization",
            ),
            request=_local_request(),
            db=db,
        )

    assert added["obj"].role == Role.SYSTEM_ADMIN
    assert added["obj"].email == "admin@example.com"
    assert added["obj"].tenant_id == str(tenant_id)
    assert result.access_token
    assert result.refresh_token
    assert result.token_type == "bearer"
    # Token cleared after success.
    tok_mod.clear.assert_called_once()
    ts_mod.register_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# setup — guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_when_already_initialized_returns_410():
    with patch.object(
        auth_endpoint, "_is_initialized", new=AsyncMock(return_value=True)
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_endpoint.setup(
                payload=SetupRequest(
                    email="admin@example.com",
                    password="securepassword",
                    tenant_name="My Organization",
                ),
                request=_local_request(),
                db=MagicMock(),
            )
    assert exc.value.status_code == 410


@pytest.mark.asyncio
async def test_setup_remote_without_token_returns_403():
    with (
        patch.object(
            auth_endpoint, "_is_initialized", new=AsyncMock(return_value=False)
        ),
        patch.object(auth_endpoint, "setup_token") as tok_mod,
    ):
        tok_mod.is_setup_token_required.return_value = True
        tok_mod.validate.return_value = False
        with pytest.raises(HTTPException) as exc:
            await auth_endpoint.setup(
                payload=SetupRequest(
                    email="admin@example.com",
                    password="securepassword",
                    tenant_name="My Organization",
                ),
                request=_remote_request(),
                db=MagicMock(),
            )
    assert exc.value.status_code == 403
    assert "setup token" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_setup_remote_with_valid_token_proceeds():
    """Remote + correct token → setup proceeds."""
    tenant_id = uuid.uuid4()
    fake_tenant = MagicMock(id=tenant_id)
    db = _mock_db()
    db.execute.side_effect = [MagicMock()]
    added = {}
    db.add.side_effect = lambda obj: added.setdefault("obj", obj)
    db.refresh.side_effect = lambda obj: setattr(obj, "id", uuid.uuid4())

    with (
        patch.object(
            auth_endpoint, "_is_initialized", new=AsyncMock(return_value=False)
        ),
        patch.object(
            auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
        ),
        patch.object(
            auth_endpoint, "create_tenant", new=AsyncMock(return_value=fake_tenant)
        ),
        patch.object(auth_endpoint, "setup_token") as tok_mod,
        patch.object(auth_endpoint, "token_store") as ts_mod,
    ):
        tok_mod.is_setup_token_required.return_value = True
        tok_mod.validate.return_value = True
        ts_mod.register_refresh = AsyncMock()
        ts_mod.register_session = AsyncMock()
        ts_mod.register_session = AsyncMock()

        result = await auth_endpoint.setup(
            payload=SetupRequest(
                email="admin@example.com",
                password="securepassword",
                tenant_name="My Organization",
                setup_token="anything-validated-by-mock",
            ),
            request=_remote_request(),
            db=db,
        )

    assert added["obj"].role == Role.SYSTEM_ADMIN
    assert result.access_token
    tok_mod.validate.assert_called_once_with("anything-validated-by-mock")


@pytest.mark.asyncio
async def test_setup_duplicate_email_returns_409():
    with (
        patch.object(
            auth_endpoint, "_is_initialized", new=AsyncMock(return_value=False)
        ),
        patch.object(auth_endpoint, "setup_token") as tok_mod,
        patch.object(
            auth_endpoint,
            "get_user_by_email",
            new=AsyncMock(return_value=MagicMock()),
        ),
    ):
        tok_mod.is_setup_token_required.return_value = False
        with pytest.raises(HTTPException) as exc:
            await auth_endpoint.setup(
                payload=SetupRequest(
                    email="dup@example.com",
                    password="securepassword",
                    tenant_name="My Organization",
                ),
                request=_local_request(),
                db=MagicMock(),
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_setup_acquires_advisory_lock():
    """Bootstrap path must call pg_advisory_xact_lock before the write."""
    tenant_id = uuid.uuid4()
    fake_tenant = MagicMock(id=tenant_id)
    db = _mock_db()
    db.execute.side_effect = [MagicMock()]  # the advisory-lock call
    db.add.side_effect = lambda obj: None
    db.refresh.side_effect = lambda obj: setattr(obj, "id", uuid.uuid4())

    with (
        patch.object(
            auth_endpoint, "_is_initialized", new=AsyncMock(return_value=False)
        ),
        patch.object(
            auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
        ),
        patch.object(
            auth_endpoint, "create_tenant", new=AsyncMock(return_value=fake_tenant)
        ),
        patch.object(auth_endpoint, "setup_token") as tok_mod,
        patch.object(auth_endpoint, "token_store") as ts_mod,
    ):
        tok_mod.is_setup_token_required.return_value = False
        ts_mod.register_refresh = AsyncMock()
        ts_mod.register_session = AsyncMock()

        await auth_endpoint.setup(
            payload=SetupRequest(
                email="admin@example.com",
                password="securepassword",
                tenant_name="My Organization",
            ),
            request=_local_request(),
            db=db,
        )

    first_call = db.execute.call_args_list[0]
    assert "pg_advisory_xact_lock" in str(first_call.args[0])


# ---------------------------------------------------------------------------
# register lockdown — bootstrap path removed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_without_tenant_id_returns_403():
    """The open bootstrap path is gone — register requires tenant_id + invite."""
    with patch.object(
        auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_endpoint.register(
                user_data=UserRegister(
                    email="someone@example.com",
                    password="password123",
                ),
                db=MagicMock(),
            )
    assert exc.value.status_code == 403
    assert "setup" in exc.value.detail.lower() or "invite" in exc.value.detail.lower()


# ---------------------------------------------------------------------------
# Lenient email validation — self-hosted installs use @localhost / @host.local
# (EmailStr/email-validator rejected these; see schemas/auth.py)
# ---------------------------------------------------------------------------


def test_setup_request_accepts_localhost_email():

    for addr in (
        "admin@localhost",
        "admin@healthassistant.local",
        "admin@home",
        "admin@example.com",
    ):
        req = SetupRequest(email=addr, password="securepassword", tenant_name="Org")
        assert req.email == addr


def test_setup_request_rejects_malformed_email():
    import pytest as _pytest
    from pydantic import ValidationError

    for bad in ("noatmark", "admin@", "@host", "a b@host", ""):
        with _pytest.raises(ValidationError):
            SetupRequest(email=bad, password="securepassword", tenant_name="Org")


# ---------------------------------------------------------------------------
# setup-status — demo_mode flag (DEMO_MODE)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_status_reports_demo_mode_when_enabled():
    """When settings.DEMO_MODE is on, setup-status surfaces demo_mode=True."""
    with (
        patch.object(auth_endpoint.settings, "DEMO_MODE", True),
        patch.object(
            auth_endpoint, "_is_initialized", new=AsyncMock(return_value=True)
        ),
    ):
        result = await auth_endpoint.setup_status(
            request=_local_request(), db=MagicMock()
        )
    assert result.demo_mode is True


@pytest.mark.asyncio
async def test_setup_status_demo_mode_off_by_default():
    """demo_mode defaults to False so the frontend shows the normal login."""
    with (
        patch.object(auth_endpoint.settings, "DEMO_MODE", False),
        patch.object(
            auth_endpoint, "_is_initialized", new=AsyncMock(return_value=False)
        ),
    ):
        result = await auth_endpoint.setup_status(
            request=_local_request(), db=MagicMock()
        )
    assert result.demo_mode is False


# ---------------------------------------------------------------------------
# demo-login — credential-free login (DEMO_MODE only)
# ---------------------------------------------------------------------------


def _demo_user():
    """A stand-in for the seeded demo UserModel."""
    u = MagicMock()
    u.id = uuid.uuid4()
    u.email = "demo@healthassistant.local"
    u.tenant_id = uuid.uuid4()
    u.role = Role.ADMIN
    return u


@pytest.mark.asyncio
async def test_demo_login_requires_demo_mode():
    """demo-login 404s when DEMO_MODE is off (the route must be inert)."""
    with patch.object(auth_endpoint.settings, "DEMO_MODE", False):
        with pytest.raises(HTTPException) as exc:
            await auth_endpoint.demo_login()
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_demo_login_503_when_demo_user_missing():
    """If seeding hasn't completed (no demo user), return 503 not a crash."""
    with (
        patch.object(auth_endpoint.settings, "DEMO_MODE", True),
        patch.object(
            auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=None)
        ),
        patch.object(auth_endpoint, "rate_limit", return_value=lambda: None),
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_endpoint.demo_login()
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_demo_login_stamps_demo_claim_and_issues_tokens():
    """Happy path: returns tokens whose access JWT carries demo=True."""
    user = _demo_user()
    with (
        patch.object(auth_endpoint.settings, "DEMO_MODE", True),
        patch.object(
            auth_endpoint, "get_user_by_email", new=AsyncMock(return_value=user)
        ),
        patch.object(
            auth_endpoint.token_store,
            "register_refresh",
            new=AsyncMock(),
        ),
    ):
        result = await auth_endpoint.demo_login()

    import jwt as _jwt
    from app.core.config import settings

    payload = _jwt.decode(
        result.access_token,
        settings.SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"verify_aud": False},
    )
    assert payload["demo"] is True
    assert payload["sub"] == user.email
    assert payload["role"] == Role.ADMIN.value
