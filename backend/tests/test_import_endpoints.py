import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.main import app
from app.core.security import get_current_user
from app.core.database import get_db
from app.models.enums import JobStatus
from app.models.export_import_job import ImportJobModel

TEST_USER_ID = uuid.uuid4()
TEST_TENANT_ID = uuid.uuid4()


def _override_user(role="USER"):
    from app.schemas.user import TokenData

    return TokenData(
        user_id=TEST_USER_ID, sub=str(TEST_USER_ID),
        tenant_id=TEST_TENANT_ID, role=role,
    )


def _setup_db(db_mock):
    async def override_get_db():
        yield db_mock
    return override_get_db


@pytest.mark.asyncio
async def test_import_backup_enqueues_celery_task(async_client: AsyncClient, tmp_path):
    app.dependency_overrides[get_current_user] = _override_user
    db_mock = AsyncMock()
    job = ImportJobModel(
        id=uuid.uuid4(), tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID,
        source_filename="b.zip", status=JobStatus.PENDING, progress=0,
    )
    db_mock.add = MagicMock()
    db_mock.commit = AsyncMock()

    async def fake_refresh(j):
        j.id = job.id

    db_mock.refresh = AsyncMock(side_effect=fake_refresh)
    app.dependency_overrides[get_db] = _setup_db(db_mock)

    zip_bytes = b"PK\x05\x06" + b"\x00" * 18
    with patch("app.workers.tasks.import_backup") as mock_task:
        mock_task.delay = MagicMock()
        response = await async_client.post(
            "/api/v1/import/backup",
            files={"file": ("b.zip", zip_bytes, "application/zip")},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "PENDING"
    assert data["source_filename"] == "b.zip"
    mock_task.delay.assert_called_once()
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_import_job_returns_status(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = _override_user
    jid = uuid.uuid4()
    job = ImportJobModel(
        id=jid, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID,
        source_filename="b.zip", status=JobStatus.COMPLETED, progress=100,
        total_records=5, processed_records=5, failed_records=0,
    )
    db_mock = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = job
    db_mock.execute = AsyncMock(return_value=res)
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.get(f"/api/v1/import/jobs/{jid}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(jid)
    assert data["status"] == "COMPLETED"
    assert data["total_records"] == 5
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_import_job_404_when_missing(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = _override_user
    db_mock = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db_mock.execute = AsyncMock(return_value=res)
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.get(f"/api/v1/import/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_import_fhir_endpoint_restores_bundle(async_client: AsyncClient, tmp_path):
    app.dependency_overrides[get_current_user] = _override_user
    db_mock = AsyncMock()
    db_mock.commit = AsyncMock()
    app.dependency_overrides[get_db] = _setup_db(db_mock)

    bundle = '{"resourceType":"Bundle","type":"transaction","entry":[]}'
    with patch("app.services.import_service.ImportService.import_from_fhir", new=AsyncMock(
        return_value={"job_id": "", "status": "COMPLETED", "total_records": 0,
                      "processed_records": 0, "failed_records": 0}
    )):
        response = await async_client.post(
            "/api/v1/import/fhir",
            files={"file": ("b.json", bundle.encode(), "application/json")},
        )
    assert response.status_code == 200
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_import_fhir_rejects_non_json(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = _override_user
    db_mock = AsyncMock()
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.post(
        "/api/v1/import/fhir",
        files={"file": ("b.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    app.dependency_overrides = {}
