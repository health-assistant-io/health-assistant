import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock, MagicMock
from uuid import uuid4


def make_token(role="USER", user_id=None, tenant_id=None):
    """Build a TokenData-like object for dependency override."""
    token = MagicMock()
    token.role = role
    token.user_id = user_id or uuid4()
    token.tenant_id = tenant_id or uuid4()
    return token


@pytest.fixture
def patient_dict():
    return {
        "id": str(uuid4()),
        "name": [{"family": "Doe", "given": ["John"]}],
        "gender": "male",
        "birth_date": "1980-01-01",
        "user_id": None,
    }


# ---------- GET /patients (list) ----------


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patients.list_patients", new_callable=AsyncMock)
async def test_list_patients_success(mock_list, async_client: AsyncClient, patient_dict):
    from app.main import app
    from app.core.security import get_current_user

    token = make_token(role="ADMIN")
    app.dependency_overrides[get_current_user] = lambda: token
    mock_list.return_value = {"items": [patient_dict], "total": 1}

    response = await async_client.get("/api/v1/patients")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"][0]["family"] == "Doe"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patients.list_patients", new_callable=AsyncMock)
async def test_list_patients_user_role_forced_user_id(mock_list, async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user

    user_id = uuid4()
    token = make_token(role="USER", user_id=user_id)
    app.dependency_overrides[get_current_user] = lambda: token
    mock_list.return_value = {"items": [], "total": 0}

    response = await async_client.get("/api/v1/patients")
    assert response.status_code == 200
    # USER role forces user_id == current_user.user_id
    _, kwargs = mock_list.call_args
    assert kwargs["user_id"] == str(user_id)

    app.dependency_overrides = {}


# ---------- POST /patients (create) ----------


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patients.create_patient", new_callable=AsyncMock)
async def test_create_patient_success(mock_create, async_client: AsyncClient, patient_dict):
    from app.main import app
    from app.core.security import get_current_user

    token = make_token(role="ADMIN")
    app.dependency_overrides[get_current_user] = lambda: token
    mock_create.return_value = patient_dict

    response = await async_client.post(
        "/api/v1/patients",
        json={"name": [{"family": "Doe", "given": ["John"]}], "gender": "male"},
    )
    assert response.status_code == 200
    assert response.json()["name"][0]["family"] == "Doe"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patients.create_patient", new_callable=AsyncMock)
async def test_create_patient_user_role_forces_user_id(mock_create, async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user

    user_id = uuid4()
    token = make_token(role="USER", user_id=user_id)
    app.dependency_overrides[get_current_user] = lambda: token
    mock_create.return_value = {"id": "x"}

    await async_client.post("/api/v1/patients", json={"name": [{"family": "X"}]})

    payload, _ = mock_create.call_args.args, mock_create.call_args.kwargs
    # First positional arg is the patient_data dict
    assert payload[0]["user_id"] == str(user_id)

    app.dependency_overrides = {}


# ---------- GET /patients/{id} ----------


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patients.check_patient_access", new_callable=AsyncMock)
async def test_get_patient_success(mock_access, async_client: AsyncClient, patient_dict):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="ADMIN")
    mock_access.return_value = patient_dict

    response = await async_client.get(f"/api/v1/patients/{patient_dict['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == patient_dict["id"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patients.check_patient_access", new_callable=AsyncMock)
async def test_get_patient_not_found(mock_access, async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from fastapi import HTTPException

    app.dependency_overrides[get_current_user] = lambda: make_token(role="ADMIN")
    mock_access.side_effect = HTTPException(status_code=404, detail="Patient not found")

    response = await async_client.get(f"/api/v1/patients/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Patient not found"

    app.dependency_overrides = {}


# ---------- PUT /patients/{id} ----------


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patients.update_patient", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.patients.check_patient_access", new_callable=AsyncMock)
async def test_update_patient_success(
    mock_access, mock_update, async_client: AsyncClient, patient_dict
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="ADMIN")
    updated = {**patient_dict, "gender": "female"}
    mock_update.return_value = updated

    response = await async_client.put(
        f"/api/v1/patients/{patient_dict['id']}", json={"gender": "female"}
    )
    assert response.status_code == 200
    assert response.json()["gender"] == "female"

    app.dependency_overrides = {}


# ---------- DELETE /patients/{id} ----------


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patients.delete_patient", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.patients.check_patient_access", new_callable=AsyncMock)
async def test_delete_patient_success(
    mock_access, mock_delete, async_client: AsyncClient, patient_dict
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = lambda: make_token(role="ADMIN")
    mock_access.return_value = patient_dict
    mock_delete.return_value = True

    response = await async_client.delete(f"/api/v1/patients/{patient_dict['id']}")
    assert response.status_code == 200
    assert response.json()["message"] == "Patient deleted successfully"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patients.delete_patient", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.patients.check_patient_access", new_callable=AsyncMock)
async def test_delete_patient_forbidden_for_unlinked_user(
    mock_access, mock_delete, async_client: AsyncClient, patient_dict
):
    from app.main import app
    from app.core.security import get_current_user

    # USER role but patient is linked to a different user.
    # check_patient_access returns a model-like object with attributes.
    patient_owner = uuid4()
    caller = uuid4()
    mock_patient = MagicMock()
    mock_patient.user_id = patient_owner
    app.dependency_overrides[get_current_user] = lambda: make_token(
        role="USER", user_id=caller
    )
    mock_access.return_value = mock_patient

    response = await async_client.delete(f"/api/v1/patients/{patient_dict['id']}")
    assert response.status_code == 403
    mock_delete.assert_not_called()

    app.dependency_overrides = {}
