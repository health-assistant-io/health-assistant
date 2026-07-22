import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock
from app.core.security import get_password_hash
from app.models.enums import Role
from app.models.user_model import UserModel
import uuid


# A test user fixture (real model, transient instance — never flushed)
@pytest.fixture
def mock_user():
    return UserModel(
        id=uuid.uuid4(),
        email="test@example.com",
        hashed_password=get_password_hash("testpassword123"),
        role=Role.USER,
        tenant_id=uuid.uuid4(),
        is_active=True,
        is_service_account=False,
        settings={},
    )


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.auth.get_user_by_email")
async def test_login_success(
    mock_get_user_by_email, async_client: AsyncClient, mock_user
):
    mock_get_user_by_email.return_value = mock_user

    response = await async_client.post(
        "/api/v1/auth/login",
        data={"username": "test@example.com", "password": "testpassword123"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert "expires_in" in data
    mock_get_user_by_email.assert_called_once_with("test@example.com")


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.auth.get_user_by_email")
async def test_login_wrong_password(
    mock_get_user_by_email, async_client: AsyncClient, mock_user
):
    mock_get_user_by_email.return_value = mock_user

    response = await async_client.post(
        "/api/v1/auth/login",
        data={"username": "test@example.com", "password": "wrongpassword"},
    )

    assert response.status_code == 401
    data = response.json()
    assert data["detail"] == "Incorrect email or password"


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.auth.get_user_by_email")
async def test_login_user_not_found(mock_get_user_by_email, async_client: AsyncClient):
    mock_get_user_by_email.return_value = None

    response = await async_client.post(
        "/api/v1/auth/login",
        data={"username": "notfound@example.com", "password": "somepassword"},
    )

    assert response.status_code == 401
    data = response.json()
    assert data["detail"] == "Incorrect email or password"


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.auth.token_store.register_refresh", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.auth.create_tenant")
@patch("app.api.v1.endpoints.auth.get_user_by_email")
@patch("app.api.v1.endpoints.auth._is_initialized", new_callable=AsyncMock)
async def test_setup_success(
    mock_is_initialized,
    mock_get_user_by_email,
    mock_create_tenant,
    mock_register_refresh,
    async_client: AsyncClient,
    mock_user,
):
    """First-run setup (POST /auth/setup) → new tenant + SYSTEM_ADMIN + tokens.

    The bootstrap path moved from POST /auth/register (now invite-only)
    to POST /auth/setup, which is the browser wizard endpoint. This test
    exercises the happy path: uninitialized system, no token required
    (dev env), creates the initial admin and returns login tokens.
    """
    from app.models.enums import Role

    mock_is_initialized.return_value = False
    mock_get_user_by_email.return_value = None
    mock_create_tenant.return_value = type("X", (), {"id": mock_user.tenant_id})()

    # Capture every UserModel the endpoint tries to persist so we can
    # assert the role. The setup path inlines the create in the request
    # session (audit B7 race protection via pg_advisory_xact_lock).
    created_users = []

    class _FakeDB:
        async def execute(self, stmt, *a, **kw):
            # The advisory-lock call — result is ignored.
            return type("R", (), {"scalar": lambda self: 0})()

        async def commit(self):
            for u in created_users:
                if u.id is None:
                    u.id = mock_user.id

        async def refresh(self, obj):
            obj.id = obj.id or mock_user.id

        def add(self, obj):
            created_users.append(obj)

    from app.main import app
    from app.core.database import get_db

    fake_db = _FakeDB()
    app.dependency_overrides[get_db] = lambda: fake_db
    try:
        response = await async_client.post(
            "/api/v1/auth/setup",
            json={
                "email": "newuser@example.com",
                "password": "securepassword123",
                "tenant_name": "My Organization",
            },
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    assert created_users, "setup path must add a UserModel to the session"
    assert created_users[0].role == Role.SYSTEM_ADMIN
    # Tokens returned so the caller is immediately authenticated.
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.auth.get_user_by_email")
async def test_register_email_exists(
    mock_get_user_by_email, async_client: AsyncClient, mock_user
):
    mock_get_user_by_email.return_value = mock_user

    response = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "securepassword123",
            "tenant_id": str(mock_user.tenant_id),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered"


@pytest.mark.asyncio
async def test_validate_token_missing(async_client: AsyncClient):
    response = await async_client.get("/api/v1/auth/validate")
    # Now it returns 401 because we handle missing token gracefully in get_token
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_validate_token_invalid(async_client: AsyncClient):
    response = await async_client.get(
        "/api/v1/auth/validate", headers={"Authorization": "Bearer invalid_token"}
    )
    assert response.status_code == 401
    assert (
        response.json()["detail"] == "Invalid token format"
        or response.json()["detail"] == "Invalid or expired token"
    )


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.auth.get_user_by_email")
async def test_refresh_token(
    mock_get_user_by_email, async_client: AsyncClient, mock_user
):
    mock_get_user_by_email.return_value = mock_user

    # First login to get a refresh token
    login_response = await async_client.post(
        "/api/v1/auth/login",
        data={"username": "test@example.com", "password": "testpassword123"},
    )
    refresh_token = login_response.json()["refresh_token"]

    # Now test the refresh endpoint
    refresh_response = await async_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )

    assert refresh_response.status_code == 200
    data = refresh_response.json()
    assert "access_token" in data
    # Audit A5: refresh tokens now ROTATE — a new refresh token is issued on
    # each refresh (the old jti is revoked server-side), so the returned token
    # must differ from the one presented.
    assert "refresh_token" in data
    assert data["refresh_token"] != refresh_token
    # The new refresh token is a valid typed refresh token.
    from app.core.security import decode_refresh_token

    new_payload = decode_refresh_token(data["refresh_token"])
    assert new_payload is not None
    assert new_payload.get("type") == "refresh"
