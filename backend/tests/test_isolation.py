import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.models.enums import Role


class MockStandardUser:
    def __init__(self):
        self.user_id = uuid.uuid4()
        self.tenant_id = uuid.uuid4()
        self.role = Role.USER.value


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patients.list_patients")
async def test_list_patients_isolation_for_standard_user(
    mock_list_patients, async_client: AsyncClient
):
    """``GET /patients`` is ``patient:read @ own`` (ADR 0054): the gate lets
    the USER principal through and the handler enforces the isolation — the
    service is called with the caller's grant-aware reachable set (own +
    active grants), never the raw ``user_id`` query parameter, so the
    tenant-wide directory is unreachable even though the route answers 200."""
    from app.core.security import get_current_user
    from app.main import app

    user = MockStandardUser()
    app.dependency_overrides[get_current_user] = lambda: user
    mock_list_patients.return_value = {"items": [], "total": 0}

    # A USER cannot widen the answer by passing another member's user_id —
    # the handler ignores the query parameter and filters by the reachable set.
    other_user_id = uuid.uuid4()
    response = await async_client.get("/api/v1/patients", params={"user_id": str(other_user_id)})

    assert response.status_code == 200
    _, kwargs = mock_list_patients.call_args
    assert isinstance(kwargs["patient_ids"], set)
    assert "user_id" not in kwargs
    assert all(pid != other_user_id for pid in kwargs["patient_ids"])
    assert mock_list_patients.call_args.args[0] == user.tenant_id

    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("app.api.v1.endpoints.patients.check_patient_access")
async def test_access_denied_standard_user(mock_check_access, async_client: AsyncClient):
    from fastapi import HTTPException

    from app.core.security import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: MockStandardUser()
    mock_check_access.side_effect = HTTPException(status_code=403, detail="Access denied")

    patient_id = uuid.uuid4()
    response = await async_client.get(f"/api/v1/patients/{patient_id}")

    assert response.status_code == 403
    app.dependency_overrides = {}
