import pytest
from httpx import AsyncClient
from unittest.mock import patch
from app.core.security import get_password_hash
from app.models.user import User
import uuid


# A test user fixture
@pytest.fixture
def mock_user():
    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        hashed_password=get_password_hash("testpassword123"),
        role="user",
        tenant_id=str(uuid.uuid4()),
    )
    return user


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
@patch("app.api.v1.endpoints.auth.get_user_by_email")
@patch("app.api.v1.endpoints.auth.create_tenant")
async def test_register_success(
    mock_create_tenant,
    mock_get_user_by_email,
    async_client: AsyncClient,
    mock_user,
):
    """Bootstrap registration (no tenant_id) → new tenant + SYSTEM_ADMIN user.

    Audit B7 changed the contract: providing a ``tenant_id`` now requires
    a valid invite token. The original test used the join-existing-tenant
    path without an invite; that's exactly the hole B7 closes. Switched
    to the bootstrap path (no tenant_id), which is what a household
    self-onboarding user would actually hit.
    """
    from app.models.enums import Role

    mock_get_user_by_email.return_value = None
    mock_create_tenant.return_value = type(
        "X", (), {"id": mock_user.tenant_id}
    )()

    # Capture every UserModel the endpoint tries to persist so we can
    # assert the role. The bootstrap path inlines the create in the
    # request session (audit B7 race protection).
    created_users = []

    class _FakeDB:
        def __init__(self):
            self._seq = 0

        async def execute(self, stmt, *a, **kw):
            # First call → advisory lock; second → COUNT(users) → 0.
            self._seq += 1
            return type("R", (), {"scalar": lambda self: 0})()

        async def commit(self):
            # Assign an id so the response_model validation passes.
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
            "/api/v1/auth/register",
            json={
                "email": "newuser@example.com",
                "password": "securepassword123",
                # No tenant_id → bootstrap path
            },
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    assert created_users, "bootstrap path must add a UserModel to the session"
    assert created_users[0].role == Role.SYSTEM_ADMIN


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
    assert data["refresh_token"] == refresh_token
