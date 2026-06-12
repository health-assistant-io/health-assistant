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
def mock_layout_data():
    return {
        "id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "patient_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "name": "Custom View",
        "is_default": True,
        "layout_config": {"lg": []},
        "cards_config": [],
    }


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patient_layout.check_patient_access")
@patch("app.api.v1.endpoints.patient_layout.get_patient_layouts")
async def test_list_layouts(
    mock_get_layouts, mock_check, async_client: AsyncClient, mock_layout_data
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_get_layouts.return_value = [mock_layout_data]

    patient_id = str(uuid.uuid4())
    response = await async_client.get(f"/api/v1/patients/{patient_id}/layouts")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["name"] == "Custom View"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patient_layout.create_patient_layout")
async def test_create_layout(
    mock_create_layout, async_client: AsyncClient, mock_layout_data
):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db
    from unittest.mock import AsyncMock, MagicMock

    app.dependency_overrides[get_current_user] = override_get_current_user

    # Mock DB for patient check
    db_mock = AsyncMock()
    res_mock = MagicMock()
    res_mock.scalar_one_or_none.return_value = MagicMock()  # Patient found
    db_mock.execute.return_value = res_mock

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    mock_create_layout.return_value = mock_layout_data

    patient_id = str(uuid.uuid4())
    response = await async_client.post(
        f"/api/v1/patients/{patient_id}/layouts",
        json={
            "name": "New Layout",
            "is_default": False,
            "layout_config": {},
            "cards_config": [],
            "patient_id": patient_id,
        },
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Custom View"

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patient_layout.check_patient_access")
@patch("app.api.v1.endpoints.patient_layout.get_active_layout")
async def test_get_active_layout(
    mock_get_active, mock_check, async_client: AsyncClient, mock_layout_data
):
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    mock_get_active.return_value = mock_layout_data

    patient_id = str(uuid.uuid4())
    response = await async_client.get(f"/api/v1/patients/{patient_id}/layouts/active")

    assert response.status_code == 200
    assert response.json()["is_default"] is True

    app.dependency_overrides = {}
