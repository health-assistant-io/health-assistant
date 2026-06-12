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
def mock_patient_data():
    return {
        "resourceType": "Patient",
        "id": "123",
        "name": [{"family": "Doe", "given": ["John"]}],
        "gender": "male",
        "birthDate": "1980-01-01",
    }


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.fhir.check_patient_access")
async def test_get_patient_success(
    mock_get_patient, async_client: AsyncClient, mock_patient_data
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_get_patient.return_value = mock_patient_data

    response = await async_client.get("/api/v1/fhir/Patient/123")
    assert response.status_code == 200
    assert response.json()["resourceType"] == "Patient"
    assert response.json()["name"][0]["family"] == "Doe"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.fhir.check_patient_access")
async def test_get_patient_not_found(mock_get_patient, async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from fastapi import HTTPException

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_get_patient.side_effect = HTTPException(status_code=404, detail="Patient not found")

    response = await async_client.get("/api/v1/fhir/Patient/999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Patient not found"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.fhir.create_patient")
async def test_create_patient(
    mock_create_patient, async_client: AsyncClient, mock_patient_data
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_create_patient.return_value = mock_patient_data

    response = await async_client.post(
        "/api/v1/fhir/Patient",
        json={
            "resourceType": "Patient",
            "name": [{"family": "Doe", "given": ["John"]}],
        },
    )
    assert response.status_code == 200
    assert response.json()["resourceType"] == "Patient"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.fhir.list_patients")
async def test_list_patients(
    mock_list_patients, async_client: AsyncClient, mock_patient_data
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    # list_patients actually returns {"items": [...], "total": ...}
    mock_list_patients.return_value = {"items": [mock_patient_data], "total": 1}

    response = await async_client.get("/api/v1/fhir/Patient")
    assert response.status_code == 200
    assert isinstance(response.json()["items"], list)
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["id"] == "123"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.fhir.create_patient")
async def test_create_patient(
    mock_create_patient, async_client: AsyncClient, mock_patient_data
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_create_patient.return_value = mock_patient_data

    response = await async_client.post(
        "/api/v1/fhir/Patient",
        json={
            "resourceType": "Patient",
            "name": [{"family": "Doe", "given": ["John"]}],
        },
    )
    assert response.status_code == 200
    assert response.json()["resourceType"] == "Patient"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.fhir.list_patients")
async def test_list_patients(
    mock_list_patients, async_client: AsyncClient, mock_patient_data
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    # list_patients actually returns {"items": [...], "total": ...}
    mock_list_patients.return_value = {"items": [mock_patient_data], "total": 1}

    response = await async_client.get("/api/v1/fhir/Patient")
    assert response.status_code == 200
    assert isinstance(response.json()["items"], list)
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["id"] == "123"

    app.dependency_overrides = {}
