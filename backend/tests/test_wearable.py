import pytest
from httpx import AsyncClient
from unittest.mock import patch
import uuid


class MockUser:
    def __init__(self):
        self.id = "65daba01-2bcb-4b46-9f2f-de9352c209d6"
        self.user_id = self.id
        self.role = "user"
        self.tenant_id = str(uuid.uuid4())

    def get(self, key, default=None):
        return getattr(self, key, default)


def override_get_current_user():
    return MockUser()


@pytest.fixture
def mock_wearable_data():
    return [
        {"timestamp": "2024-01-01T08:00:00Z", "heart_rate": 72},
        {"timestamp": "2024-01-01T08:05:00Z", "heart_rate": 75},
    ]


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.wearable.upload_wearable_data")
async def test_upload_wearable_data(
    mock_upload, async_client: AsyncClient, mock_wearable_data
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_upload.return_value = len(mock_wearable_data)

    response = await async_client.post(
        "/api/v1/wearable/data", json={"device_id": "apple_watch_1", "points": mock_wearable_data}
    )
    assert response.status_code == 200
    assert response.json()["uploaded"] == len(mock_wearable_data)
    assert response.json()["device_id"] == "apple_watch_1"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.wearable.get_wearable_data")
async def test_get_wearable_data(
    mock_get_data, async_client: AsyncClient, mock_wearable_data
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_get_data.return_value = mock_wearable_data

    response = await async_client.get(
        "/api/v1/wearable/data?device_id=apple_watch_1&start_date=2024-01-01&end_date=2024-01-02"
    )
    assert response.status_code == 200
    assert response.json()["device_id"] == "apple_watch_1"
    assert len(response.json()["data"]) == 2

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.wearable.get_wearable_summary")
async def test_get_wearable_summary(mock_get_summary, async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_get_summary.return_value = {"avg_heart_rate": 73, "steps": 5000}

    response = await async_client.get(
        "/api/v1/wearable/data/summary?device_id=apple_watch_1&date=2024-01-01"
    )
    assert response.status_code == 200
    assert response.json()["avg_heart_rate"] == 73

    app.dependency_overrides = {}
