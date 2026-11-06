"""One analytical contract: the telemetry read routes are gone.

Plan 3 W1 collapses reads onto ``/analytics`` (web) and the bridge's
schema-pinned sub-API. These tests pin the deletion so a future edit cannot
reintroduce a second read surface, and pin that the Python raw-rows anomaly
detector is gone.
"""

import inspect
import uuid
from unittest.mock import patch

import pytest

TENANT_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
PATIENT_A = uuid.UUID("22222222-2222-2222-2222-222222222222")


class MockUser:
    def __init__(self, tenant_id=TENANT_A, role="USER"):
        self.user_id = uuid.uuid4()
        self.tenant_id = tenant_id
        self.role = role
        self.sub = "test"

    def get(self, key, default=None):
        return getattr(self, key, default)


def _override_user(user):
    from app.core.security import get_current_user
    from app.main import app

    async def _override():
        return user

    app.dependency_overrides[get_current_user] = _override


def _clear_overrides():
    from app.main import app

    app.dependency_overrides = {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/v1/telemetry/data", 405),
        ("GET", "/api/v1/telemetry/data/summary", 404),
        ("GET", "/api/v1/telemetry/anomalies", 404),
    ],
)
async def test_telemetry_read_routes_are_deleted(async_client, method, path, expected):
    _override_user(MockUser())
    try:
        response = await async_client.request(method, path)
        assert response.status_code == expected
    finally:
        _clear_overrides()


@pytest.mark.asyncio
async def test_upload_endpoint_passes_tenant_and_source(async_client):
    _override_user(MockUser())
    try:
        captured = {}

        async def fake_upload(db, device_id, points, tenant_id, source="bridge", device_label=None):
            captured.update(
                tenant_id=tenant_id,
                device_id=device_id,
                source=source,
                label=device_label,
            )
            return len(points)

        with patch(
            "app.api.v1.endpoints.telemetry.upload_telemetry_data",
            new=fake_upload,
        ):
            response = await async_client.post(
                "/api/v1/telemetry/data",
                json={
                    "device_id": "device-xxx",
                    "source": "health-connect",
                    "device_label": "Watch",
                    "points": [
                        {
                            "timestamp": "2026-01-01T08:00:00Z",
                            "slug": "heart-rate",
                            "value": 72.0,
                            "unit": "bpm",
                            "patient_id": str(PATIENT_A),
                        }
                    ],
                },
            )
        assert response.status_code == 200, response.text
        assert captured["tenant_id"] == TENANT_A
        assert captured["source"] == "health-connect"
        assert captured["label"] == "Watch"
    finally:
        _clear_overrides()


def test_telemetry_service_no_longer_exposes_read_surface():
    from app.services.telemetry import telemetry_service

    for name in ("get_telemetry_data", "get_telemetry_summary", "get_telemetry_anomalies"):
        assert not hasattr(telemetry_service, name), (
            f"{name} belonged to the deleted /telemetry read routes"
        )


def test_python_raw_rows_anomaly_detector_is_deleted():
    import importlib.util

    assert importlib.util.find_spec("app.services.anomaly_detector") is None, (
        "the Python raw-rows anomaly detector must not exist — "
        "the CAgg engine is the one anomaly implementation"
    )


def test_analytics_anomalies_delegate_to_the_cagg_engine():
    from app.services import analytics as analytics_service

    src = inspect.getsource(analytics_service.get_biomarker_anomalies)
    assert "detect_telemetry_anomalies" in src
    assert "list_telemetry_anomalies" in src
    assert "AnomalyDetector" not in src
