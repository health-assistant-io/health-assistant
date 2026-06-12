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
@patch("app.api.v1.endpoints.auth.service_create_user")
async def test_register_success(
    mock_service_create_user,
    mock_get_user_by_email,
    async_client: AsyncClient,
    mock_user,
):
    mock_get_user_by_email.return_value = None
    mock_service_create_user.return_value = mock_user

    response = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "newuser@example.com",
            "password": "securepassword123",
            "tenant_id": str(mock_user.tenant_id),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == mock_user.email
    assert "id" in data
    mock_service_create_user.assert_called_once()


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
