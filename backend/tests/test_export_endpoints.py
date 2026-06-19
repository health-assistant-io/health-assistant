import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.main import app
from app.core.security import get_current_user
from app.core.database import get_db
from app.models.enums import ExportScope, ExportType, JobStatus
from app.models.export_import_job import ExportJobModel

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


# ---------- POST /export ----------

@pytest.mark.asyncio
async def test_create_export_patient_scope_user_requires_patient_ids(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = _override_user
    db_mock = AsyncMock()
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.post(
        "/api/v1/export",
        json={"scope": "patient", "export_type": "fhir_only"},
    )
    assert response.status_code == 400
    assert "patient_ids" in response.json()["detail"]
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_export_user_cannot_export_multiple_patients(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = _override_user
    db_mock = AsyncMock()
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.post(
        "/api/v1/export",
        json={"scope": "patient", "export_type": "fhir_only",
              "patient_ids": [str(uuid.uuid4()), str(uuid.uuid4())]},
    )
    assert response.status_code == 403
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_export_group_scope_forbidden_for_user(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = _override_user
    db_mock = AsyncMock()
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.post(
        "/api/v1/export",
        json={"scope": "group", "export_type": "full_backup",
              "patient_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 403
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_export_system_scope_forbidden_for_manager(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: _override_user("MANAGER")
    db_mock = AsyncMock()
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.post(
        "/api/v1/export",
        json={"scope": "system", "export_type": "full_backup"},
    )
    assert response.status_code == 403
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_export_enqueues_celery_task(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: _override_user("ADMIN")
    db_mock = AsyncMock()
    job = ExportJobModel(
        id=uuid.uuid4(), tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID,
        scope=ExportScope.SYSTEM, export_type=ExportType.FULL_BACKUP,
        status=JobStatus.PENDING, progress=0, patient_ids=None,
        smart_scope="system/*.cruds",
    )
    db_mock.add = MagicMock()
    db_mock.commit = AsyncMock()

    async def fake_refresh(j):
        j.id = job.id

    db_mock.refresh = AsyncMock(side_effect=fake_refresh)
    app.dependency_overrides[get_db] = _setup_db(db_mock)

    with patch("app.workers.tasks.export_backup") as mock_task:
        mock_task.delay = MagicMock()
        response = await async_client.post(
            "/api/v1/export",
            json={"scope": "system", "export_type": "full_backup"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "system"
    assert data["export_type"] == "full_backup"
    assert data["status"] == "PENDING"
    assert data["smart_scope"] == "system/*.cruds"
    mock_task.delay.assert_called_once()
    app.dependency_overrides = {}


# ---------- GET /export/jobs/{id} ----------

@pytest.mark.asyncio
async def test_get_export_job_returns_404_when_missing(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = _override_user
    db_mock = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db_mock.execute = AsyncMock(return_value=res)
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.get(f"/api/v1/export/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_export_job_returns_status(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = _override_user
    jid = uuid.uuid4()
    job = ExportJobModel(
        id=jid, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID,
        scope=ExportScope.PATIENT, export_type=ExportType.FHIR_ONLY,
        status=JobStatus.COMPLETED, progress=100,
        file_path="/tmp/x.fhir.json", file_size_bytes=123,
        resource_counts={"Patient": 1}, smart_scope="patient/*.rs",
    )
    db_mock = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = job
    db_mock.execute = AsyncMock(return_value=res)
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.get(f"/api/v1/export/jobs/{jid}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(jid)
    assert data["status"] == "COMPLETED"
    assert data["progress"] == 100
    app.dependency_overrides = {}


# ---------- GET /export/jobs ----------

@pytest.mark.asyncio
async def test_list_export_jobs(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = _override_user
    jobs = [
        ExportJobModel(
            id=uuid.uuid4(), tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID,
            scope=ExportScope.SYSTEM, export_type=ExportType.FULL_BACKUP,
            status=JobStatus.COMPLETED, progress=100, smart_scope="system/*.cruds",
        )
    ]
    db_mock = AsyncMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = jobs
    db_mock.execute = AsyncMock(return_value=res)
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.get("/api/v1/export/jobs")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["scope"] == "system"
    app.dependency_overrides = {}


# ---------- GET /export/jobs/{id}/download ----------

@pytest.mark.asyncio
async def test_download_export_404_when_file_missing(async_client: AsyncClient, tmp_path):
    app.dependency_overrides[get_current_user] = _override_user
    jid = uuid.uuid4()
    job = ExportJobModel(
        id=jid, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID,
        scope=ExportScope.PATIENT, export_type=ExportType.FHIR_ONLY,
        status=JobStatus.COMPLETED, progress=100, file_path=str(tmp_path / "nope.json"),
        smart_scope="patient/*.rs",
    )
    db_mock = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = job
    db_mock.execute = AsyncMock(return_value=res)
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.get(f"/api/v1/export/jobs/{jid}/download")
    assert response.status_code == 404
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_download_export_returns_file(async_client: AsyncClient, tmp_path):
    app.dependency_overrides[get_current_user] = _override_user
    jid = uuid.uuid4()
    f = tmp_path / "out.fhir.json"
    f.write_text('{"resourceType":"Bundle"}')
    job = ExportJobModel(
        id=jid, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID,
        scope=ExportScope.PATIENT, export_type=ExportType.FHIR_ONLY,
        status=JobStatus.COMPLETED, progress=100, file_path=str(f),
        smart_scope="patient/*.rs",
    )
    db_mock = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = job
    db_mock.execute = AsyncMock(return_value=res)
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.get(f"/api/v1/export/jobs/{jid}/download")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    app.dependency_overrides = {}
