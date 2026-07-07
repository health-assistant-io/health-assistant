import pytest
from httpx import AsyncClient
from unittest.mock import patch, MagicMock, AsyncMock
import uuid
import datetime
from app.models.clinical_event import (
    ClinicalEvent,
    ClinicalEventType,
    ClinicalEventStatus,
)


# Define consistent test IDs
TEST_USER_ID = uuid.uuid4()
TEST_TENANT_ID = uuid.uuid4()

def override_get_current_user():
    from app.schemas.user import TokenData
    return TokenData(user_id=TEST_USER_ID, sub=str(TEST_USER_ID), tenant_id=TEST_TENANT_ID, role="user")


@pytest.mark.asyncio
async def test_list_event_types(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    db_mock = AsyncMock()

    mock_type = ClinicalEventType(
        id=uuid.uuid4(),
        name="Test Type",
        slug="test-type",
        description="Test Description",
        color="#ff0000",
    )

    res_mock = MagicMock()
    res_mock.scalars.return_value.all.return_value = [mock_type]
    db_mock.execute.return_value = res_mock

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    response = await async_client.get("/api/v1/clinical-events/types")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["slug"] == "test-type"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_event(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    db_mock = AsyncMock()
    db_mock.add = MagicMock()
    db_mock.commit = AsyncMock()
    db_mock.flush = AsyncMock()

    patient_id = uuid.uuid4()
    type_id = uuid.uuid4()
    event_id = uuid.uuid4()
    tenant_id = TEST_TENANT_ID

    res_patient = MagicMock()
    mock_patient = MagicMock()
    mock_patient.id = patient_id
    mock_patient.tenant_id = tenant_id
    mock_patient.user_id = TEST_USER_ID
    res_patient.scalar_one_or_none.return_value = mock_patient  # Patient exists

    mock_event = ClinicalEvent(
        id=event_id,
        patient_id=patient_id,
        type_id=type_id,
        tenant_id=tenant_id,
        title="Test Event",
        status=ClinicalEventStatus.ACTIVE,
        onset_date=datetime.datetime.now(datetime.timezone.utc),
        occurrences=[],
        event_metadata={},
        created_at=datetime.datetime.now(datetime.timezone.utc),
        updated_at=datetime.datetime.now(datetime.timezone.utc),
        type_entity=None,
        examination_links=[],
    )

    res_event = MagicMock()
    res_event.scalar_one.return_value = mock_event

    db_mock.execute.side_effect = [res_patient, res_event]

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    response = await async_client.post(
        "/api/v1/clinical-events",
        json={
            "patient_id": str(patient_id),
            "type_id": str(type_id),
            "title": "Test Event",
            "status": "ACTIVE",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Test Event"
    assert data["id"] == str(event_id)

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_event(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    db_mock = AsyncMock()
    event_id = uuid.uuid4()
    tenant_id = TEST_TENANT_ID

    mock_patient = MagicMock()
    mock_patient.id = uuid.uuid4()
    mock_patient.tenant_id = tenant_id
    mock_patient.user_id = TEST_USER_ID

    mock_event = ClinicalEvent(
        id=event_id,
        patient_id=mock_patient.id,
        tenant_id=tenant_id,
        title="Test Event",
        status=ClinicalEventStatus.ACTIVE,
        onset_date=datetime.datetime.now(datetime.timezone.utc),
        occurrences=[],
        event_metadata={},
        created_at=datetime.datetime.now(datetime.timezone.utc),
        updated_at=datetime.datetime.now(datetime.timezone.utc),
        type_entity=None,
        examination_links=[],
    )

    res_event = MagicMock()
    res_event.scalar_one_or_none.return_value = mock_event
    res_event.scalar_one.return_value = mock_event

    res_patient = MagicMock()
    res_patient.scalar_one_or_none.return_value = mock_patient

    db_mock.execute.side_effect = [res_event, res_patient, res_event]

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    response = await async_client.get(f"/api/v1/clinical-events/{event_id}")
    assert response.status_code == 200
    assert response.json()["id"] == str(event_id)

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_update_event(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    db_mock = AsyncMock()
    db_mock.commit = AsyncMock()
    db_mock.refresh = AsyncMock()
    event_id = uuid.uuid4()
    tenant_id = TEST_TENANT_ID

    mock_patient = MagicMock()
    mock_patient.id = uuid.uuid4()
    mock_patient.tenant_id = tenant_id
    mock_patient.user_id = TEST_USER_ID

    mock_event = ClinicalEvent(
        id=event_id,
        patient_id=mock_patient.id,
        tenant_id=tenant_id,
        title="Old Title",
        status=ClinicalEventStatus.ACTIVE,
        onset_date=datetime.datetime.now(datetime.timezone.utc),
        occurrences=[],
        event_metadata={},
        created_at=datetime.datetime.now(datetime.timezone.utc),
        updated_at=datetime.datetime.now(datetime.timezone.utc),
        type_entity=None,
        examination_links=[],
    )

    res_fetch = MagicMock()
    res_fetch.scalar_one_or_none.return_value = mock_event

    res_patient = MagicMock()
    res_patient.scalar_one_or_none.return_value = mock_patient

    res_final = MagicMock()
    res_final.scalar_one.return_value = mock_event

    db_mock.execute.side_effect = [res_fetch, res_patient, res_final]

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    response = await async_client.put(
        f"/api/v1/clinical-events/{event_id}", json={"title": "New Title"}
    )

    assert response.status_code == 200
    assert mock_event.title == "New Title"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_delete_event(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    db_mock = AsyncMock()
    db_mock.delete = AsyncMock()
    db_mock.commit = AsyncMock()
    event_id = uuid.uuid4()
    tenant_id = TEST_TENANT_ID

    mock_patient = MagicMock()
    mock_patient.id = uuid.uuid4()
    mock_patient.tenant_id = tenant_id
    mock_patient.user_id = TEST_USER_ID

    mock_event = MagicMock()
    mock_event.patient_id = mock_patient.id
    mock_event.tenant_id = tenant_id

    res_event = MagicMock()
    res_event.scalar_one_or_none.return_value = mock_event

    res_patient = MagicMock()
    res_patient.scalar_one_or_none.return_value = mock_patient

    db_mock.execute.side_effect = [res_event, res_patient]

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    response = await async_client.delete(f"/api/v1/clinical-events/{event_id}")
    assert response.status_code == 200
    assert response.json()["message"] == "Clinical event deleted successfully"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_list_events_by_examination(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    db_mock = AsyncMock()
    exam_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    mock_event = ClinicalEvent(
        id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        tenant_id=tenant_id,
        title="Event for Exam",
        status=ClinicalEventStatus.ACTIVE,
        onset_date=datetime.datetime.now(datetime.timezone.utc),
        occurrences=[],
        event_metadata={},
        created_at=datetime.datetime.now(datetime.timezone.utc),
        updated_at=datetime.datetime.now(datetime.timezone.utc),
        type_entity=None,
        examination_links=[],
    )

    res_mock = MagicMock()
    res_mock.scalars.return_value.unique.return_value.all.return_value = [mock_event]
    db_mock.execute.return_value = res_mock

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    response = await async_client.get(
        f"/api/v1/clinical-events?examination_id={exam_id}"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Event for Exam"

    app.dependency_overrides = {}
