import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock, MagicMock
from uuid import uuid4


def make_token(role="USER", user_id=None, tenant_id=None):
    token = MagicMock()
    token.role = role
    token.user_id = user_id or uuid4()
    token.tenant_id = tenant_id or uuid4()
    return token


@pytest.fixture
def observation_dict():
    return {
        "id": str(uuid4()),
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}], "text": "Heart rate"},
        "subject": {"reference": f"Patient/{uuid4()}"},
        "value_quantity": {"value": 72, "unit": "bpm"},
        "effective_datetime": "2026-01-01T00:00:00Z",
    }


# ---------- GET /observations (list) ----------


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.observations.list_observations", new_callable=AsyncMock)
async def test_list_observations_success(mock_list, async_client: AsyncClient, observation_dict):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="ADMIN")
    mock_list.return_value = {"items": [observation_dict], "total": 1}

    response = await async_client.get("/api/v1/observations")
    assert response.status_code == 200
    assert response.json()["total"] == 1

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.observations.check_patient_access", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.observations.list_observations", new_callable=AsyncMock)
async def test_list_observations_with_patient_id_checks_access(
    mock_list, mock_access, async_client: AsyncClient
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="ADMIN")
    mock_list.return_value = {"items": [], "total": 0}
    patient_id = uuid4()

    response = await async_client.get(f"/api/v1/observations?patient_id={patient_id}")
    assert response.status_code == 200
    mock_access.assert_awaited_once()

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_list_observations_user_role_no_patient_returns_empty(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="USER")

    response = await async_client.get("/api/v1/observations")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}

    app.dependency_overrides = {}


# ---------- POST /observations (create) ----------


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.observations.log_audit_action", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.observations.create_observation", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.observations.check_patient_access", new_callable=AsyncMock)
async def test_create_observation_success(
    mock_access, mock_create, mock_audit, async_client: AsyncClient, observation_dict
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="ADMIN")
    created = MagicMock()
    created.id = uuid4()
    mock_create.return_value = created

    response = await async_client.post(
        "/api/v1/observations",
        json={
            "code": observation_dict["code"],
            "subject": observation_dict["subject"],
            "value_quantity": observation_dict["value_quantity"],
        },
    )
    assert response.status_code == 200
    mock_access.assert_awaited_once()
    mock_create.assert_awaited_once()
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args.kwargs["action"] == "create_observation"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_observation_user_role_without_patient_400(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="USER")

    response = await async_client.post(
        "/api/v1/observations",
        json={"code": {"text": "x"}},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Patient reference required"

    app.dependency_overrides = {}


# ---------- GET /observations/{id} ----------


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.observations.get_observation", new_callable=AsyncMock)
async def test_get_observation_success(mock_get, async_client: AsyncClient, observation_dict):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="ADMIN")
    mock_get.return_value = observation_dict

    response = await async_client.get(f"/api/v1/observations/{observation_dict['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == observation_dict["id"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.observations.get_observation", new_callable=AsyncMock)
async def test_get_observation_not_found(mock_get, async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="ADMIN")
    mock_get.return_value = None

    response = await async_client.get(f"/api/v1/observations/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Observation not found"

    app.dependency_overrides = {}


# ---------- DELETE /observations/{id} ----------


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.observations.log_audit_action", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.observations.delete_observation", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.observations.get_observation", new_callable=AsyncMock)
async def test_delete_observation_success(
    mock_get, mock_delete, mock_audit, async_client: AsyncClient, observation_dict
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="ADMIN")
    obs = MagicMock()
    obs.id = uuid4()
    obs.subject = observation_dict["subject"]
    obs.to_dict.return_value = observation_dict
    mock_get.return_value = obs
    mock_delete.return_value = True

    response = await async_client.delete(f"/api/v1/observations/{observation_dict['id']}")
    assert response.status_code == 200
    mock_delete.assert_awaited_once()
    mock_audit.assert_awaited_once()
    assert mock_audit.call_args.kwargs["action"] == "delete_observation"
    assert mock_audit.call_args.kwargs["old_value"] == observation_dict

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.observations.delete_observation", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.observations.get_observation", new_callable=AsyncMock)
async def test_delete_observation_not_found(
    mock_get, mock_delete, async_client: AsyncClient
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="ADMIN")
    mock_get.return_value = None

    response = await async_client.delete(f"/api/v1/observations/{uuid4()}")
    assert response.status_code == 404
    mock_delete.assert_not_called()

    app.dependency_overrides = {}
