import pytest
import uuid
from httpx import AsyncClient
from unittest.mock import patch
from app.schemas.user import TokenData


def override_get_current_user():
    uid = uuid.uuid4()
    return TokenData(
        user_id=uid,
        sub="current_user@example.com",
        role="USER",
        tenant_id=uuid.uuid4(),
    )


def override_get_admin_user():
    uid = uuid.uuid4()
    return TokenData(
        user_id=uid,
        sub="admin@example.com",
        role="SYSTEM_ADMIN",
        tenant_id=uuid.uuid4(),
    )


@pytest.fixture
def mock_tenant():
    # Use simple mock object or dictionary since TenantModel expects DB session config
    class MockTenant:
        def __init__(self):
            self.id = uuid.uuid4()
            self.name = "Test Tenant"
            self.settings = {"theme": "dark"}

        def to_dict(self):
            return {"id": str(self.id), "name": self.name, "settings": self.settings}

    return MockTenant()


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.tenants.get_tenant")
async def test_get_tenant(mock_get_tenant, async_client: AsyncClient, mock_tenant):
    from app.main import app
    from app.core.security import get_current_user

    # System Admin can see any tenant
    app.dependency_overrides[get_current_user] = override_get_admin_user
    mock_get_tenant.return_value = mock_tenant.to_dict()

    response = await async_client.get(f"/api/v1/tenants/{mock_tenant.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Tenant"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_tenant_forbidden(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user

    response = await async_client.post(
        "/api/v1/tenants?name=NewTenant", json={"settings": {}}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Role USER is not authorized to access this resource"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.tenants.create_tenant")
async def test_create_tenant_admin(
    mock_create_tenant, async_client: AsyncClient, mock_tenant
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_admin_user
    mock_create_tenant.return_value = mock_tenant.to_dict()

    response = await async_client.post(
        "/api/v1/tenants?name=Test%20Tenant",
        json={
            "theme": "dark"
        },  # Pass settings dictionary as json body or via query, depending on endpoint implementation
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Test Tenant"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_update_tenant_forbidden(async_client: AsyncClient, mock_tenant):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user

    response = await async_client.put(f"/api/v1/tenants/{mock_tenant.id}")

    assert response.status_code == 403

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.tenants.delete_tenant")
async def test_delete_tenant_admin(
    mock_delete_tenant, async_client: AsyncClient, mock_tenant
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_admin_user
    mock_delete_tenant.return_value = None

    response = await async_client.delete(f"/api/v1/tenants/{mock_tenant.id}")

    assert response.status_code == 200
    assert response.json()["message"] == "Tenant deleted successfully"
    mock_delete_tenant.assert_called_once_with(str(mock_tenant.id))

    app.dependency_overrides = {}
