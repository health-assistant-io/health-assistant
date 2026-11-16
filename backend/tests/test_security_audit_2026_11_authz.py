"""Security regression tests — 2026-11 authorization/auth hardening.

Covers the findings from the 2026-11 audit:
- import/upload patient ownership (USER cannot attach data to a foreign patient)
- backup restore requires ADMIN
- export job status/download object-level gate
- telemetry patient ownership
- patient user_id assignment validation
- biomarker retry/remap tenant+role gates
- password rotation / self-erasure re-authentication
- setup-token localhost bypass closed in production
- integrations OAuth callback session binding
- ZIP decompression caps
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.core.errors import AuthorizationError
from app.core.security import get_current_user, get_password_hash
from app.main import app
from app.models.enums import Role
from app.schemas.user import TokenData

TENANT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()


def _token(role=Role.USER.value, user_id=None, tenant_id=None):
    return TokenData(
        user_id=user_id or USER_ID,
        tenant_id=tenant_id or TENANT_ID,
        role=role,
        sub=str(user_id or USER_ID),
    )


def _override_db(db_mock):
    async def _get_db():
        yield db_mock

    return _get_db


# ---------------------------------------------------------------------------
# Import / upload patient ownership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_backup_requires_admin(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: _token(Role.USER.value)
    try:
        response = await async_client.post(
            "/api/v1/import/backup",
            files={"file": ("b.zip", b"PK\x05\x06" + b"\x00" * 18, "application/zip")},
        )
    finally:
        app.dependency_overrides = {}
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_fhir_import_requires_manager(async_client: AsyncClient):
    """A raw FHIR Bundle carries its own ids/subjects: USER cannot use it."""
    app.dependency_overrides[get_current_user] = lambda: _token(Role.USER.value)
    try:
        response = await async_client.post(
            "/api/v1/import/fhir",
            files={"file": ("b.json", b"{}", "application/json")},
        )
    finally:
        app.dependency_overrides = {}
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "filename", "mime"),
    [
        ("/api/v1/import/csv", "b.csv", "text/csv"),
        ("/api/v1/import/ocr", "b.pdf", "application/pdf"),
    ],
)
async def test_import_endpoints_check_patient_access(
    async_client: AsyncClient, path, filename, mime
):
    app.dependency_overrides[get_current_user] = lambda: _token(Role.USER.value)
    foreign_patient = uuid.uuid4()
    with patch(
        "app.api.v1.endpoints.import_data.check_patient_access",
        new=AsyncMock(side_effect=AuthorizationError("Access denied to this patient's data")),
    ) as check:
        try:
            response = await async_client.post(
                path,
                data={"patient_id": str(foreign_patient)},
                files={"file": (filename, b"x", mime)},
            )
        finally:
            app.dependency_overrides = {}
    assert response.status_code == 403
    check.assert_awaited_once()


@pytest.mark.asyncio
async def test_telemetry_rejects_foreign_patient(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: _token(Role.USER.value)
    payload = {
        "device_id": "watch-1",
        "points": [
            {
                "timestamp": "2026-11-08T10:00:00Z",
                "slug": "heart-rate",
                "value": 80,
                "patient_id": str(uuid.uuid4()),
            }
        ],
    }
    with patch(
        "app.api.v1.endpoints.telemetry.check_patient_access",
        new=AsyncMock(side_effect=AuthorizationError("Access denied to this patient's data")),
    ) as check:
        try:
            response = await async_client.post("/api/v1/telemetry/data", json=payload)
        finally:
            app.dependency_overrides = {}
    assert response.status_code == 403
    check.assert_awaited_once()


# ---------------------------------------------------------------------------
# Patients — user_id assignment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patient_update_rejects_foreign_tenant_user(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: _token(Role.ADMIN.value)
    db_mock = MagicMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db_mock.execute = AsyncMock(return_value=res)
    app.dependency_overrides[get_current_user] = lambda: _token(Role.ADMIN.value)
    from app.core.database import get_db

    app.dependency_overrides[get_db] = _override_db(db_mock)
    with (
        patch(
            "app.api.v1.endpoints.patients.check_patient_access",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("app.api.v1.endpoints.patients.update_patient", new=AsyncMock()) as update,
    ):
        try:
            response = await async_client.put(
                f"/api/v1/patients/{uuid.uuid4()}",
                json={"user_id": str(uuid.uuid4())},
            )
        finally:
            app.dependency_overrides = {}
    assert response.status_code == 404
    update.assert_not_awaited()


# ---------------------------------------------------------------------------
# Biomarkers — retry/remap gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_biomarker_remap_requires_manager(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: _token(Role.USER.value)
    try:
        response = await async_client.post(
            f"/api/v1/biomarkers/{uuid.uuid4()}/remap",
            json={"source_name": "Glucose"},
        )
    finally:
        app.dependency_overrides = {}
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Password rotation / self-erasure re-authentication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_change_requires_current_password():
    from app.api.v1.endpoints import me as me_endpoint
    from app.schemas.user import PasswordChangeRequest

    user = MagicMock()
    user.id = USER_ID
    user.tenant_id = TENANT_ID
    user.password_hash = get_password_hash("old-password-123")
    user.failed_login_attempts = 0
    user.locked_until = None

    with (
        patch.object(me_endpoint, "get_user_by_id", new=AsyncMock(return_value=user)),
        patch.object(me_endpoint, "set_login_failures", new=AsyncMock()) as failures,
        pytest.raises(HTTPException) as exc,
    ):
        await me_endpoint.change_password_endpoint(
            PasswordChangeRequest(
                current_password="wrong-password", new_password="new-password-123"
            ),
            current_user=_token(),
        )
    assert exc.value.status_code == 400
    failures.assert_awaited_once()


@pytest.mark.asyncio
async def test_password_change_revokes_sessions_on_success():
    from app.api.v1.endpoints import me as me_endpoint
    from app.schemas.user import PasswordChangeRequest

    user = MagicMock()
    user.id = USER_ID
    user.tenant_id = TENANT_ID
    user.password_hash = get_password_hash("old-password-123")
    user.failed_login_attempts = 0
    user.locked_until = None

    with (
        patch.object(me_endpoint, "get_user_by_id", new=AsyncMock(return_value=user)),
        patch.object(me_endpoint, "update_user", new=AsyncMock(return_value=user)) as update,
        patch.object(me_endpoint, "bump_token_version", new=AsyncMock()) as bump,
        patch.object(me_endpoint.token_store, "revoke_everything", new=AsyncMock()) as revoke,
        patch.object(me_endpoint, "log_audit_action", new=AsyncMock()),
        patch.object(me_endpoint, "reset_login_failures", new=AsyncMock()),
    ):
        result = await me_endpoint.change_password_endpoint(
            PasswordChangeRequest(
                current_password="old-password-123", new_password="new-password-123"
            ),
            current_user=_token(),
        )
    assert result is None
    update.assert_awaited_once()
    bump.assert_awaited_once_with(str(USER_ID))
    revoke.assert_awaited_once_with(str(USER_ID))


@pytest.mark.asyncio
async def test_self_delete_requires_password():
    from app.api.v1.endpoints import users as users_endpoint
    from app.schemas.user import SelfDeleteRequest

    user = MagicMock()
    user.id = USER_ID
    user.password_hash = get_password_hash("correct-password")
    user.failed_login_attempts = 0
    user.locked_until = None
    user.role = Role.USER.value

    with (
        patch.object(users_endpoint, "get_user_by_id", new=AsyncMock(return_value=user)),
        patch.object(users_endpoint, "set_login_failures", new=AsyncMock()) as failures,
        patch.object(users_endpoint, "run_erasure", new=AsyncMock()) as erase,
        pytest.raises(HTTPException) as exc,
    ):
        await users_endpoint.delete_current_user_endpoint(
            SelfDeleteRequest(password="wrong-password"),
            current_user=_token(),
            db=MagicMock(),
        )
    assert exc.value.status_code == 403
    failures.assert_awaited_once()
    erase.assert_not_awaited()


# ---------------------------------------------------------------------------
# Setup token — localhost is not a bypass in production
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_token_required_on_localhost_in_production(monkeypatch):
    from starlette.requests import Request

    from app.core import setup_token

    monkeypatch.setattr(setup_token.settings, "HA_APP_ENV", "production")
    monkeypatch.setattr(setup_token.settings, "SETUP_TOKEN_MODE", "log")
    request = Request({"type": "http", "client": ("127.0.0.1", 12345), "headers": []})
    assert setup_token.is_setup_token_required(request) is True


def test_setup_token_bypasses_in_dev(monkeypatch):
    from starlette.requests import Request

    from app.core import setup_token

    monkeypatch.setattr(setup_token.settings, "HA_APP_ENV", "test")
    request = Request({"type": "http", "client": ("203.0.113.9", 12345), "headers": []})
    assert setup_token.is_setup_token_required(request) is False


# ---------------------------------------------------------------------------
# Integrations OAuth callback — session binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_oauth_callback_rejects_foreign_session():
    from app.api.v1.endpoints import integrations as integrations_endpoint

    other_user = uuid.uuid4()
    pending = {
        "integration_id": str(uuid.uuid4()),
        "user_id": str(other_user),
        "tenant_id": str(TENANT_ID),
    }
    store = MagicMock()
    store.consume = AsyncMock(return_value=pending)
    with (
        patch.object(
            integrations_endpoint,
            "_load_enabled_oauth",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
        patch.object(integrations_endpoint, "OAuthStateStore", return_value=store),
        pytest.raises(HTTPException) as exc,
    ):
        await integrations_endpoint.oauth_callback(
            domain="withings",
            state="s",
            code="c",
            current_user=_token(),
            db=MagicMock(),
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# ZIP decompression caps
# ---------------------------------------------------------------------------


def test_zip_expansion_guard_rejects_oversized_entry():
    from app.services.imports.orchestrator import (
        MAX_ZIP_ENTRY_BYTES,
        _guard_zip_expansion,
    )

    info = MagicMock()
    info.filename = "documents/0"
    info.file_size = MAX_ZIP_ENTRY_BYTES + 1
    archive = MagicMock()
    archive.infolist.return_value = [info]
    with pytest.raises(ValueError, match="exceeds"):
        _guard_zip_expansion(archive)


@pytest.mark.asyncio
async def test_tenant_admin_cannot_reset_system_admin_password():
    from app.api.v1.endpoints import users as users_endpoint
    from app.schemas.user import PasswordResetRequest

    target = MagicMock()
    target.id = uuid.uuid4()
    target.role = Role.SYSTEM_ADMIN.value

    with (
        patch.object(users_endpoint, "get_user_by_id", new=AsyncMock(return_value=target)),
        patch.object(users_endpoint, "update_user", new=AsyncMock()) as update,
        pytest.raises(HTTPException) as exc,
    ):
        await users_endpoint.reset_user_password_endpoint(
            str(target.id),
            PasswordResetRequest(new_password="new-password-123"),
            current_user=_token(Role.ADMIN.value),
            db=MagicMock(),
        )
    assert exc.value.status_code == 403
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_manager_cannot_invite_admin():
    from app.api.v1.endpoints import auth as auth_endpoint

    with pytest.raises(HTTPException) as exc:
        await auth_endpoint.create_invite(
            tenant_id=str(TENANT_ID),
            email="new@example.com",
            role=Role.ADMIN.value,
            expires_days=7,
            current_user=_token(Role.MANAGER.value),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_logout_ignores_foreign_refresh_token():
    from fastapi import Response

    from app.api.v1.endpoints import auth as auth_endpoint
    from app.schemas.auth import TokenRefresh

    other_user = uuid.uuid4()
    foreign_payload = {"user_id": str(other_user), "jti": "foreign-jti", "fid": "foreign-fid"}
    request = MagicMock()
    request.cookies.get.return_value = None

    with (
        patch.object(auth_endpoint, "decode_refresh_token", return_value=foreign_payload),
        patch.object(auth_endpoint, "verify_access_token", return_value=None),
        patch.object(auth_endpoint.token_store, "revoke_refresh", new=AsyncMock()) as revoke,
        patch.object(auth_endpoint, "revoke_family", new=AsyncMock()) as family,
        patch.object(auth_endpoint, "_audit_auth_event", new=AsyncMock()),
    ):
        await auth_endpoint.logout(
            request=request,
            response=Response(),
            token_data=TokenRefresh(refresh_token="foreign-token"),
            current_user=_token(),
            token="access-token",
        )
    revoke.assert_not_awaited()
    family.assert_not_awaited()


@pytest.mark.asyncio
async def test_data_lifecycle_self_erasure_requires_password():
    """The generic erasure API must not bypass DELETE /users/me's password gate."""
    from app.api.v1.endpoints import data_lifecycle as lifecycle_endpoint
    from app.models.enums import ErasureSubjectType
    from app.schemas.data_lifecycle import ErasureRequestCreate

    subject = MagicMock()
    subject.id = USER_ID
    subject.tenant_id = TENANT_ID
    subject.user_id = USER_ID

    with (
        patch.object(
            lifecycle_endpoint,
            "resolve_subject",
            new=AsyncMock(return_value=subject),
        ),
        patch.object(lifecycle_endpoint, "assert_can_erase"),
        patch.object(lifecycle_endpoint, "run_erasure", new=AsyncMock()) as erase,
        pytest.raises(HTTPException) as exc,
    ):
        await lifecycle_endpoint.create_erasure(
            ErasureRequestCreate(
                subject_type=ErasureSubjectType.USER,
                subject_id=USER_ID,
                dry_run=False,
            ),
            current_user=_token(Role.USER.value),
            db=MagicMock(),
        )
    assert exc.value.status_code == 403
    erase.assert_not_awaited()


@pytest.mark.asyncio
async def test_data_lifecycle_self_erasure_allows_dry_run_without_password():
    from app.api.v1.endpoints import data_lifecycle as lifecycle_endpoint
    from app.models.enums import ErasureSubjectType
    from app.schemas.data_lifecycle import ErasureRequestCreate

    subject = MagicMock()
    subject.id = USER_ID
    subject.tenant_id = TENANT_ID
    subject.user_id = USER_ID
    request = MagicMock()
    request.to_dict.return_value = {
        "id": str(uuid.uuid4()),
        "subject_type": "user",
        "subject_id": str(USER_ID),
        "dry_run": True,
    }

    with (
        patch.object(
            lifecycle_endpoint,
            "resolve_subject",
            new=AsyncMock(return_value=subject),
        ),
        patch.object(lifecycle_endpoint, "assert_can_erase"),
        patch.object(lifecycle_endpoint, "run_erasure", new=AsyncMock(return_value=request)),
        patch.object(
            lifecycle_endpoint,
            "ErasureResponse",
            return_value=MagicMock(dry_run=True),
        ),
    ):
        response = await lifecycle_endpoint.create_erasure(
            ErasureRequestCreate(
                subject_type=ErasureSubjectType.USER,
                subject_id=USER_ID,
                dry_run=True,
            ),
            current_user=_token(Role.USER.value),
            db=MagicMock(),
        )
    assert response.dry_run is True


@pytest.mark.asyncio
async def test_admin_delete_user_route_refuses_self_erasure():
    from app.api.v1.endpoints import users as users_endpoint

    self_user = MagicMock()
    self_user.id = USER_ID

    with (
        patch.object(users_endpoint, "get_user_by_id", new=AsyncMock(return_value=self_user)),
        patch.object(users_endpoint, "run_erasure", new=AsyncMock()) as erase,
        pytest.raises(HTTPException) as exc,
    ):
        await users_endpoint.delete_user_endpoint(
            str(USER_ID),
            current_user=_token(Role.ADMIN.value),
            db=MagicMock(),
        )
    assert exc.value.status_code == 400
    erase.assert_not_awaited()


def test_task_ws_channel_is_per_user_for_user_role():
    from app.api.v1.endpoints.websockets import _tasks_channel

    assert _tasks_channel(_token(Role.USER.value)) == f"user:{USER_ID}:tasks"
    assert _tasks_channel(_token(Role.MANAGER.value)) == f"tenant:{TENANT_ID}:tasks"
    assert _tasks_channel(_token(Role.ADMIN.value)) == f"tenant:{TENANT_ID}:tasks"


def test_task_ws_channel_fails_closed_for_unknown_role():
    from app.api.v1.endpoints.websockets import _tasks_channel

    assert _tasks_channel(_token("")) == f"user:{USER_ID}:tasks"
    assert _tasks_channel(_token("SOMETHING_ELSE")) == f"user:{USER_ID}:tasks"
