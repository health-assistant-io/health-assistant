import pytest
from httpx import AsyncClient
from unittest.mock import patch
import uuid
from app.models.enums import Role

class MockStandardUser:
    def __init__(self):
        self.user_id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()
        self.role = Role.USER.value

@pytest.mark.asyncio
@patch("app.api.v1.endpoints.fhir.list_patients")
async def test_list_patients_isolation_for_standard_user(
    mock_list_patients, async_client: AsyncClient
):
    from app.main import app
    from app.core.security import get_current_user

    user = MockStandardUser()
    app.dependency_overrides[get_current_user] = lambda: user
    mock_list_patients.return_value = {"items": [], "total": 0}

    # Standard user requests their patients
    response = await async_client.get("/api/v1/fhir/Patient")
    
    assert response.status_code == 200
    
    # Verify that list_patients was called WITH the user's ID string to enforce isolation
    args, kwargs = mock_list_patients.call_args
    assert str(kwargs.get("user_id")) == str(user.user_id)

    app.dependency_overrides = {}

@pytest.mark.asyncio
@patch("app.api.v1.endpoints.fhir.check_patient_access")
async def test_access_denied_standard_user(
    mock_check_access, async_client: AsyncClient
):
    from app.main import app
    from app.core.security import get_current_user
    from fastapi import HTTPException

    app.dependency_overrides[get_current_user] = lambda: MockStandardUser()
    mock_check_access.side_effect = HTTPException(status_code=403, detail="Access denied")

    patient_id = uuid.uuid4()
    response = await async_client.get(f"/api/v1/fhir/Patient/{patient_id}")
    
    assert response.status_code == 403
    app.dependency_overrides = {}
