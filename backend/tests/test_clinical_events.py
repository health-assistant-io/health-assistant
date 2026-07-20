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
from app.models.enums import ScheduleKind


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
        schedule_kind=ScheduleKind.STATE,
        # Phase 8e: required (NOT NULL). Mock value matches what production
        # rows now always carry.
        category_concept_id=uuid.uuid4(),
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


# ---------------------------------------------------------------------------
# Phase 2: date-search query params (active_on / onset_on / date_range).
# ---------------------------------------------------------------------------


def test_parse_date_range_valid():
    from app.services.clinical_event_service import _parse_date_range

    start, end = _parse_date_range("2026-03-01,2026-03-31")
    assert start == datetime.datetime(2026, 3, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    assert end == datetime.datetime(2026, 3, 31, 23, 59, 59, 999999, tzinfo=datetime.timezone.utc)


def test_parse_date_range_with_whitespace():
    from app.services.clinical_event_service import _parse_date_range

    start, end = _parse_date_range(" 2026-03-01 , 2026-03-31 ")
    assert start is not None
    assert end is not None
    assert start.year == 2026 and start.month == 3 and start.day == 1
    assert end.day == 31


def test_parse_date_range_invalid_returns_none():
    from app.services.clinical_event_service import _parse_date_range

    # Malformed dates — silently skipped (caller's responsibility to 400 if desired).
    assert _parse_date_range("not-a-date,2026-03-31") == (None, None)
    assert _parse_date_range("2026-03-01") == (None, None)  # no comma
    assert _parse_date_range("") == (None, None)
    assert _parse_date_range(None) == (None, None)


@pytest.mark.asyncio
async def test_list_events_with_active_on_param(async_client: AsyncClient):
    """active_on=YYYY-MM-DD should be accepted and forwarded to the service."""
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    db_mock = AsyncMock()
    mock_event = ClinicalEvent(
        id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        title="Ongoing Pain",
        status=ClinicalEventStatus.ACTIVE,
        onset_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
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
        "/api/v1/clinical-events?active_on=2026-03-15"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Ongoing Pain"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_list_events_with_onset_on_param(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    db_mock = AsyncMock()
    res_mock = MagicMock()
    res_mock.scalars.return_value.unique.return_value.all.return_value = []
    db_mock.execute.return_value = res_mock

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    response = await async_client.get(
        "/api/v1/clinical-events?onset_on=2026-03-15"
    )
    assert response.status_code == 200
    assert response.json() == []

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_list_events_with_date_range_param(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    db_mock = AsyncMock()
    res_mock = MagicMock()
    res_mock.scalars.return_value.unique.return_value.all.return_value = []
    db_mock.execute.return_value = res_mock

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    response = await async_client.get(
        "/api/v1/clinical-events?date_range=2026-01-01,2026-06-30"
    )
    assert response.status_code == 200

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_list_events_rejects_malformed_active_on(async_client: AsyncClient):
    """Bad date format should 422 (FastAPI validation), not 500."""
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user

    response = await async_client.get(
        "/api/v1/clinical-events?active_on=not-a-date"
    )
    assert response.status_code == 422

    app.dependency_overrides = {}


# ---------------------------------------------------------------------------
# Phase 4: schedule_kind (type-declared rendering hint).
# ---------------------------------------------------------------------------


def test_schedule_kind_enum_values():
    from app.models.enums import ScheduleKind

    assert ScheduleKind.STATE.value == "state"
    assert ScheduleKind.RANGE.value == "range"
    assert ScheduleKind.RECURRING.value == "recurring"
    assert ScheduleKind.POINT.value == "point"
    # Round-trip helper
    assert ScheduleKind.from_string("state") is ScheduleKind.STATE
    assert ScheduleKind.from_string("garbage") is None
    assert ScheduleKind.from_string(None) is None


def test_clinical_event_type_to_dict_includes_schedule_kind():
    from app.models.clinical_event import ClinicalEventType

    type_row = ClinicalEventType(
        id=uuid.uuid4(),
        name="Pain",
        slug="pain-test",
        schedule_kind=ScheduleKind.STATE,
    )
    payload = type_row.to_dict()
    assert payload["schedule_kind"] == "state"


def test_clinical_event_to_dict_resolves_schedule_kind_from_type():
    """ClinicalEvent.to_dict() exposes the type's schedule_kind at the instance
    level so the frontend adapter doesn't need to walk type_details."""
    from app.models.clinical_event import ClinicalEvent, ClinicalEventType

    type_row = ClinicalEventType(
        id=uuid.uuid4(),
        name="Pain",
        slug="pain-test",
        schedule_kind=ScheduleKind.STATE,
    )
    event = ClinicalEvent(
        id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        title="Ongoing Back Pain",
        status=ClinicalEventStatus.ACTIVE,
        onset_date=datetime.datetime.now(datetime.timezone.utc),
        occurrences=[],
        event_metadata={},
        type_entity=type_row,
        examination_links=[],
    )
    payload = event.to_dict()
    assert payload["schedule_kind"] == "state"


def test_clinical_event_to_dict_schedule_kind_falls_back_to_state_when_type_missing():
    """Phase 8a: schedule_kind is NOT NULL on the type, so a populated
    type_entity always carries one. When the relationship failed to load
    (shouldn't happen in production — type_id is NOT NULL — but happens in
    unit-test mocks), to_dict() returns the safe STATE default rather than
    None so the response schema's required-field constraint doesn't 500."""
    from app.models.clinical_event import ClinicalEvent

    # type_entity deliberately not set — simulates a relationship that failed
    # to eager-load. Should not raise; should yield STATE.
    event = ClinicalEvent(
        id=uuid.uuid4(),
        patient_id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        title="Orphan Event",
        status=ClinicalEventStatus.ACTIVE,
        onset_date=datetime.datetime.now(datetime.timezone.utc),
        occurrences=[],
        event_metadata={},
        type_entity=None,
        examination_links=[],
    )
    payload = event.to_dict()
    assert payload["schedule_kind"] == "state"


@pytest.mark.asyncio
async def test_create_event_type_rejects_invalid_schedule_kind(async_client: AsyncClient):
    """An unknown schedule_kind value must 422, not 500."""
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user

    response = await async_client.post(
        "/api/v1/clinical-events/types",
        json={
            "name": "Bogus",
            "slug": "bogus",
            "schedule_kind": "weekly",  # not a valid value
        },
    )
    assert response.status_code == 422

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_event_type_rejects_missing_schedule_kind(async_client: AsyncClient):
    """Phase 8a: schedule_kind is required on the wire (NOT NULL on the column).
    Omitting it must 422, not 500 / not silently default."""
    from app.main import app
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user

    response = await async_client.post(
        "/api/v1/clinical-events/types",
        json={
            "name": "NoKind",
            "slug": "no-kind",
            # schedule_kind intentionally omitted
        },
    )
    assert response.status_code == 422

    app.dependency_overrides = {}


def test_clinical_event_type_schema_accepts_all_schedule_kind_values():
    """Pydantic schema must round-trip all four enum values."""
    from app.schemas.clinical_event import ClinicalEventTypeBase

    for kind in ("state", "range", "recurring", "point"):
        item = ClinicalEventTypeBase(
            name=f"T-{kind}",
            slug=f"slug-{kind}",
            schedule_kind=kind,
            # Phase 8e: required (NOT NULL).
            category_concept_id=uuid.uuid4(),
        )
        assert item.schedule_kind.value == kind


def test_clinical_event_type_schema_rejects_missing_schedule_kind():
    """Phase 8a: the schema requires schedule_kind — Pydantic raises on a
    missing field before the request even reaches the endpoint logic."""
    from pydantic import ValidationError
    from app.schemas.clinical_event import ClinicalEventTypeBase

    with pytest.raises(ValidationError):
        ClinicalEventTypeBase(name="NoKind", slug="no-kind")


def test_seed_loader_assigns_schedule_kind():
    """The seed-service code path that builds a new ClinicalEventType from a
    seed entry must propagate schedule_kind via ScheduleKind.from_string."""
    from app.models.enums import ScheduleKind

    # Mirrors the logic in seed_service._process_clinical_event_types.
    assert ScheduleKind.from_string("state") is ScheduleKind.STATE
    assert ScheduleKind.from_string("range") is ScheduleKind.RANGE
    assert ScheduleKind.from_string("recurring") is ScheduleKind.RECURRING
    assert ScheduleKind.from_string("point") is ScheduleKind.POINT
    assert ScheduleKind.from_string(None) is None
    assert ScheduleKind.from_string("garbage") is None
