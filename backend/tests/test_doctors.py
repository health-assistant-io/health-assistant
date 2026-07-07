import pytest
from httpx import AsyncClient
from unittest.mock import patch
import uuid


def override_get_current_user():
    from app.schemas.user import TokenData

    uid = uuid.uuid4()
    tid = uuid.uuid4()
    return TokenData(user_id=uid, sub=str(uid), tenant_id=tid, role="user")


@pytest.fixture
def mock_doctor_data():
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "name": "Sarah Wilson",
        "specialty_concept_id": None,
        "license_number": "MD12345",
        "email": "sarah.wilson@hospital.com",
        "phone": "555-0101",
    }


@pytest.mark.asyncio
async def test_list_doctors(async_client: AsyncClient, mock_doctor_data):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db
    from unittest.mock import AsyncMock, MagicMock

    app.dependency_overrides[get_current_user] = override_get_current_user

    # Mock DB
    db_mock = AsyncMock()
    mock_doctor_obj = MagicMock()
    mock_doctor_obj.to_dict.return_value = mock_doctor_data

    result_mock = MagicMock()
    result_mock.scalars.return_value.unique.return_value.all.return_value = [
        mock_doctor_obj
    ]
    db_mock.execute.return_value = result_mock

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    response = await async_client.get("/api/v1/doctors")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["name"] == "Sarah Wilson"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.doctors.create_doctor")
async def test_create_doctor(mock_create, async_client: AsyncClient, mock_doctor_data):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_create.return_value = mock_doctor_data

    response = await async_client.post(
        "/api/v1/doctors", json={"name": "Sarah Wilson", "specialty_concept_id": None}
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Sarah Wilson"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.doctors.delete_doctor")
async def test_delete_doctor(mock_delete, async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_delete.return_value = True

    doctor_id = str(uuid.uuid4())
    response = await async_client.delete(f"/api/v1/doctors/{doctor_id}")
    assert response.status_code == 204

    app.dependency_overrides = {}
