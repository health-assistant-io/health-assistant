"""Telemetry endpoint tests — long-format upload contract.

The ``POST /telemetry/data`` body now carries long-format points
(``{timestamp, slug, value, unit?, patient_id?}``) — one point per
metric/timestamp. The wide ``heart_rate``/``steps``/``calories``/``data``
fields are gone (migration ``t1e2l3o4n5g6``).
"""
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
def mock_telemetry_points():
    # Long-format: two separate points (one per metric/timestamp).
    return [
        {"timestamp": "2024-01-01T08:00:00Z", "slug": "heart-rate", "value": 72.0, "unit": "bpm"},
        {"timestamp": "2024-01-01T08:05:00Z", "slug": "heart-rate", "value": 75.0, "unit": "bpm"},
    ]


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.telemetry.upload_telemetry_data")
async def test_upload_telemetry_data(
    mock_upload, async_client: AsyncClient, mock_telemetry_points
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_upload.return_value = len(mock_telemetry_points)

    response = await async_client.post(
        "/api/v1/telemetry/data",
        json={"device_id": "apple_watch_1", "points": mock_telemetry_points},
    )
    assert response.status_code == 200
    assert response.json()["uploaded"] == len(mock_telemetry_points)
    assert response.json()["device_id"] == "apple_watch_1"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.telemetry.get_telemetry_data")
async def test_get_telemetry_data(
    mock_get_data, async_client: AsyncClient, mock_telemetry_points
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_get_data.return_value = mock_telemetry_points

    response = await async_client.get(
        "/api/v1/telemetry/data?device_id=apple_watch_1&start_date=2024-01-01&end_date=2024-01-02"
    )
    assert response.status_code == 200
    assert response.json()["device_id"] == "apple_watch_1"
    assert len(response.json()["data"]) == 2

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.telemetry.get_telemetry_summary")
async def test_get_telemetry_summary(mock_get_summary, async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    # New generic summary shape: {slug: {min,max,avg,sum,count}}.
    mock_get_summary.return_value = {
        "date": "2024-01-01",
        "device_id": "apple_watch_1",
        "metrics": {
            "heart-rate": {"min": 60.0, "max": 90.0, "avg": 73.0, "sum": 0.0, "count": 10},
        },
    }

    response = await async_client.get(
        "/api/v1/telemetry/data/summary?device_id=apple_watch_1&date=2024-01-01"
    )
    assert response.status_code == 200
    assert "heart-rate" in response.json()["metrics"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_upload_rejects_wide_format_payload(async_client: AsyncClient):
    """Long-format contract: the old wide-format body (heart_rate/steps/calories)
    must be rejected — Pydantic validation requires ``slug`` + ``value``."""
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        response = await async_client.post(
            "/api/v1/telemetry/data",
            json={
                "device_id": "x",
                "points": [
                    # Old wide-format shape — missing required slug + value.
                    {"timestamp": "2024-01-01T08:00:00Z", "heart_rate": 72},
                ],
            },
        )
        assert response.status_code == 422, (
            "Wide-format payload without slug/value must fail validation"
        )
    finally:
        app.dependency_overrides = {}
