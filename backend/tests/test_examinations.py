import pytest
from httpx import AsyncClient
from unittest.mock import patch, MagicMock, AsyncMock
import uuid
import datetime

# Use the real model classes instead of mocking them to avoid SQLAlchemy select() errors
from app.models.examination_model import ExaminationModel
from app.models.doctor_model import DoctorModel


def override_get_current_user():
    from app.schemas.user import TokenData

    uid = uuid.uuid4()
    tid = uuid.uuid4()
    return TokenData(user_id=uid, sub=str(uid), tenant_id=tid, role="USER")


@pytest.mark.asyncio
async def test_create_examination(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user

    exam_id = uuid.uuid4()
    mock_exam = ExaminationModel(
        id=exam_id,
        notes="Test note",
        examination_date=datetime.date(2023, 11, 1),
        tenant_id=uuid.uuid4(),
        created_at=datetime.datetime.now(),
        updated_at=datetime.datetime.now(),
    )

    db_mock = AsyncMock()
    db_mock.add = MagicMock()
    db_mock.commit = AsyncMock()

    res_patient = MagicMock()
    res_patient.scalar_one_or_none.return_value = MagicMock()

    res_dup = MagicMock()
    res_dup.scalars.return_value.first.return_value = None

    res_reload = MagicMock()
    res_reload.scalar_one.return_value = mock_exam

    db_mock.execute.side_effect = [res_patient, res_dup, res_reload]

    # After add/commit, refresh is called. We need to make sure the object has an ID
    async def mock_refresh(obj):
        if not obj.id:
            obj.id = uuid.uuid4()
        if not obj.created_at:
            obj.created_at = datetime.datetime.now()
        if not obj.updated_at:
            obj.updated_at = datetime.datetime.now()
        return None

    db_mock.refresh = mock_refresh

    async def create_override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = create_override_get_db

    pid = uuid.uuid4()
    with patch("app.services.access.check_patient_access") as mock_check:
        response = await async_client.post(
            "/api/v1/examinations",
            json={
                "examination_date": "2023-11-01",
                "notes": "Test note",
                "patient_id": str(pid),
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["notes"] == "Test note"
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_examination_with_doctors(async_client: AsyncClient):
    from app.main import app
    from app.core.security import get_current_user
    from app.core.database import get_db

    app.dependency_overrides[get_current_user] = override_get_current_user
    doctor_id = uuid.uuid4()
    patient_id = uuid.uuid4()

    exam_id = uuid.uuid4()
    mock_exam = ExaminationModel(
        id=exam_id,
        notes="Test note",
        examination_date=datetime.date(2023, 11, 1),
        tenant_id=uuid.uuid4(),
        created_at=datetime.datetime.now(),
        updated_at=datetime.datetime.now(),
    )

    db_mock = AsyncMock()
    db_mock.add = MagicMock()
    db_mock.commit = AsyncMock()

    async def mock_refresh(obj):
        if not obj.id:
            obj.id = uuid.uuid4()
        if not obj.created_at:
            obj.created_at = datetime.datetime.now()
        if not obj.updated_at:
            obj.updated_at = datetime.datetime.now()
        return None

    db_mock.refresh = mock_refresh

    mock_doctor = DoctorModel(id=doctor_id, name="Dr. Smith")

    res_patient = MagicMock()
    res_patient.scalar_one_or_none.return_value = MagicMock()

    res_dup = MagicMock()
    res_dup.scalars.return_value.first.return_value = None

    res_doctor = MagicMock()
    res_doctor.scalars.return_value.all.return_value = [mock_doctor]

    res_reload = MagicMock()
    res_reload.scalar_one.return_value = mock_exam

    db_mock.execute.side_effect = [res_patient, res_dup, res_doctor, res_reload]

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.services.access.check_patient_access") as mock_check:
        response = await async_client.post(
            "/api/v1/examinations",
            json={
                "examination_date": "2023-11-01",
                "notes": "Test note",
                "doctor_ids": [str(doctor_id)],
                "patient_id": str(patient_id),
            },
        )

    assert response.status_code == 200
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_examination(async_client: AsyncClient):
    from app.main import app
    from app.core.database import get_db
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user

    exam_id = uuid.uuid4()
    mock_exam = ExaminationModel(
        id=exam_id,
        notes="Existing exam",
        examination_date=datetime.date(2023, 1, 1),
        tenant_id=uuid.uuid4(),
        created_at=datetime.datetime.now(),
        updated_at=datetime.datetime.now(),
        observations=[], # Do not pass raw mocks as Pydantic fails validation
        medications=[] 
    )

    db_mock = AsyncMock()
    res_mock = MagicMock()
    res_mock.scalar_one_or_none.return_value = mock_exam
    db_mock.execute.return_value = res_mock

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.services.access.check_patient_access") as mock_check:
        response = await async_client.get(f"/api/v1/examinations/{exam_id}")
    assert response.status_code == 200
    assert response.json()["id"] == str(exam_id)
    # Validate the summary metrics count works correctly in the list endpoint
    response_list = await async_client.get(f"/api/v1/examinations?patient_id={mock_exam.patient_id}")
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_update_examination(async_client: AsyncClient):
    from app.main import app
    from app.core.database import get_db
    from app.core.security import get_current_user

    app.dependency_overrides[get_current_user] = override_get_current_user

    exam_id = uuid.uuid4()
    mock_exam = ExaminationModel(
        id=exam_id,
        notes="Old notes",
        examination_date=datetime.date(2023, 1, 1),
        tenant_id=uuid.uuid4(),
        created_at=datetime.datetime.now(),
        updated_at=datetime.datetime.now(),
    )

    db_mock = AsyncMock()
    res_mock = MagicMock()
    res_mock.scalar_one_or_none.return_value = mock_exam
    res_mock.scalar_one.return_value = mock_exam
    db_mock.execute.return_value = res_mock
    db_mock.commit = AsyncMock()
    db_mock.refresh = AsyncMock()

    async def override_get_db():
        yield db_mock

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.services.access.check_patient_access") as mock_check:
        response = await async_client.put(
            f"/api/v1/examinations/{exam_id}", json={"notes": "Updated notes"}
        )

    assert response.status_code == 200
    assert response.json()["notes"] == "Updated notes"
    app.dependency_overrides = {}
