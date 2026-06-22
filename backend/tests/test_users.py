import pytest
from httpx import AsyncClient
from unittest.mock import patch
from app.models.user import User
import uuid


# Helper to mock the dependency
class MockTokenData:
    def __init__(self, user_id, tenant_id, role, sub=None):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.sub = sub
        # Add fields expected by UserResponse if used as return value
        self.id = user_id
        self.email = sub
        self.settings = {}


def override_get_current_user():
    return MockTokenData(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        role="USER",
        sub="current_user@example.com",
    )


def override_get_admin_user():
    return MockTokenData(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        role="ADMIN",
        sub="admin@example.com",
    )


def override_get_switched_admin():
    """Simulate a SYSTEM_ADMIN who has switched into another tenant."""
    original_tenant = uuid.uuid4()
    scoped_tenant = uuid.uuid4()
    admin_user_id = uuid.uuid4()
    tok = MockTokenData(
        user_id=admin_user_id,
        tenant_id=scoped_tenant,
        role="SYSTEM_ADMIN",
        sub="sysadmin@example.com",
    )
    tok.original_tenant_id = original_tenant
    tok.switched = True
    return tok

@pytest.fixture
def mock_target_user():
    return User(
        id=uuid.uuid4(),
        email="target@example.com",
        role="user",
        tenant_id=str(uuid.uuid4()),
    )


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.users.get_user_by_id")
async def test_get_me(mock_get_user_by_id, async_client: AsyncClient):
    # Mock the dependency using app.dependency_overrides
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_get_user_by_id.return_value = override_get_current_user()

    response = await async_client.get("/api/v1/users/me")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "current_user@example.com"

    # Clean up
    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.users.get_user_by_id")
async def test_get_me_switched_admin(mock_get_user_by_id, async_client: AsyncClient):
    """A switched SYSTEM_ADMIN's user row lives in their ORIGINAL tenant,
    not the scoped tenant. /users/me must look up by original_tenant_id
    or the query returns 404."""
    from app.main import app
    from app.core.security import get_current_user

    switched_admin = override_get_switched_admin()
    app.dependency_overrides[get_current_user] = lambda: switched_admin

    admin_user = User(
        id=switched_admin.user_id,
        email="sysadmin@example.com",
        role="SYSTEM_ADMIN",
        tenant_id=str(switched_admin.original_tenant_id),
    )
    mock_get_user_by_id.return_value = admin_user

    response = await async_client.get("/api/v1/users/me")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "sysadmin@example.com"

    # Verify the lookup used original_tenant_id, NOT the scoped tenant_id
    call_args = mock_get_user_by_id.call_args
    passed_tenant = call_args.kwargs.get("tenant_id")
    assert passed_tenant == switched_admin.original_tenant_id, (
        f"Expected original_tenant_id {switched_admin.original_tenant_id}, "
        f"got {passed_tenant} — switched admin would get 404"
    )

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.users.get_user_by_id")
async def test_get_user_forbidden(
    mock_get_user_by_id, async_client: AsyncClient, mock_target_user
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_get_user_by_id.return_value = mock_target_user

    response = await async_client.get(f"/api/v1/users/{mock_target_user.id}")
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to view this user"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.users.get_user_by_id")
async def test_get_user_admin_allowed(
    mock_get_user_by_id, async_client: AsyncClient, mock_target_user
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_admin_user
    mock_get_user_by_id.return_value = mock_target_user

    response = await async_client.get(f"/api/v1/users/{mock_target_user.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "target@example.com"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.users.update_user")
async def test_update_user_admin(
    mock_update_user, async_client: AsyncClient, mock_target_user
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_admin_user

    # Mocks the updated user response
    mock_target_user.email = "updated@example.com"
    mock_update_user.return_value = mock_target_user

    response = await async_client.put(
        f"/api/v1/users/{mock_target_user.id}?email=updated@example.com"
    )

    assert response.status_code == 200
    assert response.json()["email"] == "updated@example.com"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.users.delete_user")
async def test_delete_user_admin(
    mock_delete_user, async_client: AsyncClient, mock_target_user
):
    from app.main import app
    from app.core.security import get_current_user

    admin_token_data = override_get_admin_user()
    app.dependency_overrides[get_current_user] = lambda: admin_token_data
    # Mock void return
    mock_delete_user.return_value = True

    response = await async_client.delete(f"/api/v1/users/{mock_target_user.id}")

    assert response.status_code == 200
    assert response.json()["message"] == "User deleted successfully"
    mock_delete_user.assert_called_once_with(str(mock_target_user.id), tenant_id=admin_token_data.tenant_id)

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_delete_user_forbidden(async_client: AsyncClient, mock_target_user):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user

    response = await async_client.delete(f"/api/v1/users/{mock_target_user.id}")

    assert response.status_code == 403
    assert "not authorized to access this resource" in response.json()["detail"]

    app.dependency_overrides = {}
