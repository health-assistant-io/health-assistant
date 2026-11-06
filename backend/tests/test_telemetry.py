"""Telemetry ingest endpoint tests — long-format upload contract.

The ``POST /telemetry/data`` body carries long-format points
(``{timestamp, slug, value, patient_id, unit?}``) plus a device reference and
optional source/label. ``patient_id`` is required. Read routes live under
``/analytics`` (the one analytical contract); this file only covers ingest.
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

PATIENT_ID = "00000000-0000-4000-8000-000000000001"


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
        {
            "timestamp": "2024-01-01T08:00:00Z",
            "slug": "heart-rate",
            "value": 72.0,
            "unit": "bpm",
            "patient_id": PATIENT_ID,
        },
        {
            "timestamp": "2024-01-01T08:05:00Z",
            "slug": "heart-rate",
            "value": 75.0,
            "unit": "bpm",
            "patient_id": PATIENT_ID,
        },
    ]


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.telemetry.upload_telemetry_data")
async def test_upload_telemetry_data(mock_upload, async_client: AsyncClient, mock_telemetry_points):
    from app.core.security import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_upload.return_value = len(mock_telemetry_points)

    response = await async_client.post(
        "/api/v1/telemetry/data",
        json={
            "device_id": "apple_watch_1",
            "source": "health-connect",
            "device_label": "Apple Watch",
            "points": mock_telemetry_points,
        },
    )
    assert response.status_code == 200
    assert response.json()["uploaded"] == len(mock_telemetry_points)
    assert response.json()["device_id"] == "apple_watch_1"
    _args, kwargs = mock_upload.call_args
    assert kwargs["source"] == "health-connect"
    assert kwargs["device_label"] == "Apple Watch"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_upload_rejects_wide_format_payload(async_client: AsyncClient):
    """Long-format contract: the old wide-format body (heart_rate/steps/calories)
    must be rejected — Pydantic validation requires ``slug`` + ``value``."""
    from app.core.security import get_current_user
    from app.main import app

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
