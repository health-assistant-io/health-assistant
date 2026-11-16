import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.main import app
from app.models.enums import ExportScope, ExportType, JobStatus, Role
from app.models.export_import_job import ExportJobModel

TEST_USER_ID = uuid.uuid4()
TEST_TENANT_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _skip_step_up(monkeypatch):
    """These are scope/validation unit tests over a dependency-overridden
    principal (no real session family, no Redis). The step-up gate itself is
    covered by ``tests/test_step_up.py`` — here it is stubbed so the
    assertions stay about export scoping."""
    from app.services.auth import step_up

    async def _allow(principal, action):
        return "password"

    monkeypatch.setattr(step_up, "enforce", _allow)


def _override_user(role="USER"):
    from app.schemas.user import TokenData

    return TokenData(
        user_id=TEST_USER_ID,
        sub=str(TEST_USER_ID),
        tenant_id=TEST_TENANT_ID,
        role=role,
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
        json={
            "scope": "patient",
            "export_type": "fhir_only",
            "patient_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
        },
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
        json={"scope": "group", "export_type": "full_backup", "patient_ids": [str(uuid.uuid4())]},
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
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        scope=ExportScope.SYSTEM,
        export_type=ExportType.FULL_BACKUP,
        status=JobStatus.PENDING,
        progress=0,
        patient_ids=None,
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
        id=jid,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        scope=ExportScope.PATIENT,
        export_type=ExportType.FHIR_ONLY,
        status=JobStatus.COMPLETED,
        progress=100,
        file_path="/tmp/x.fhir.json",
        file_size_bytes=123,
        resource_counts={"Patient": 1},
        smart_scope="patient/*.rs",
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
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            user_id=TEST_USER_ID,
            scope=ExportScope.PATIENT,
            export_type=ExportType.FHIR_ONLY,
            status=JobStatus.COMPLETED,
            progress=100,
            smart_scope="patient/*.rs",
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
    assert data["items"][0]["scope"] == "patient"
    assert "file_path" not in data["items"][0]
    app.dependency_overrides = {}


# ---------- GET /export/jobs/{id}/download ----------


@pytest.mark.asyncio
async def test_download_export_404_when_file_missing(async_client: AsyncClient, tmp_path):
    app.dependency_overrides[get_current_user] = _override_user
    jid = uuid.uuid4()
    job = ExportJobModel(
        id=jid,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        scope=ExportScope.PATIENT,
        export_type=ExportType.FHIR_ONLY,
        status=JobStatus.COMPLETED,
        progress=100,
        file_path=str(tmp_path / "nope.json"),
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
        id=jid,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        scope=ExportScope.PATIENT,
        export_type=ExportType.FHIR_ONLY,
        status=JobStatus.COMPLETED,
        progress=100,
        file_path=str(f),
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


@pytest.mark.asyncio
async def test_download_export_does_not_treat_a_relative_key_as_a_local_path(
    async_client: AsyncClient, tmp_path, monkeypatch
):
    """A relative object key is not a legacy path, even if a same-named file exists."""
    app.dependency_overrides[get_current_user] = _override_user
    jid = uuid.uuid4()
    key = f"tenants/{TEST_TENANT_ID}/exports/{jid}.zip"
    local_lookalike = tmp_path / key
    local_lookalike.parent.mkdir(parents=True)
    local_lookalike.write_bytes(b"not the export object")
    monkeypatch.chdir(tmp_path)
    job = ExportJobModel(
        id=jid,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        scope=ExportScope.PATIENT,
        export_type=ExportType.FHIR_ONLY,
        status=JobStatus.COMPLETED,
        progress=100,
        file_path=key,
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


# ---------- Object-level authorization (audit 2026-11) ----------


@pytest.mark.asyncio
async def test_list_export_jobs_hides_other_users_and_system_scopes(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = _override_user
    jobs = [
        ExportJobModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            user_id=TEST_USER_ID,
            scope=ExportScope.PATIENT,
            export_type=ExportType.FHIR_ONLY,
            status=JobStatus.COMPLETED,
            progress=100,
            smart_scope="patient/*.rs",
        ),
        ExportJobModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            user_id=uuid.uuid4(),
            scope=ExportScope.SYSTEM,
            export_type=ExportType.FULL_BACKUP,
            status=JobStatus.COMPLETED,
            progress=100,
            smart_scope="system/*.cruds",
        ),
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
    assert data["items"][0]["user_id"] == str(TEST_USER_ID)
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_user_cannot_download_foreign_system_export(async_client: AsyncClient, tmp_path):
    app.dependency_overrides[get_current_user] = _override_user
    jid = uuid.uuid4()
    f = tmp_path / "admin-backup.zip"
    f.write_text("restricted")
    job = ExportJobModel(
        id=jid,
        tenant_id=TEST_TENANT_ID,
        user_id=uuid.uuid4(),
        scope=ExportScope.SYSTEM,
        export_type=ExportType.FULL_BACKUP,
        status=JobStatus.COMPLETED,
        progress=100,
        file_path=str(f),
        smart_scope="system/*.cruds",
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
async def test_user_cannot_read_foreign_export_job_status(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = _override_user
    jid = uuid.uuid4()
    job = ExportJobModel(
        id=jid,
        tenant_id=TEST_TENANT_ID,
        user_id=uuid.uuid4(),
        scope=ExportScope.SYSTEM,
        export_type=ExportType.FULL_BACKUP,
        status=JobStatus.COMPLETED,
        progress=100,
        smart_scope="system/*.cruds",
    )
    db_mock = AsyncMock()
    res = MagicMock()
    res.scalar_one_or_none.return_value = job
    db_mock.execute = AsyncMock(return_value=res)
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.get(f"/api/v1/export/jobs/{jid}")
    assert response.status_code == 404
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_manager_cannot_download_system_scope_export(async_client: AsyncClient, tmp_path):
    app.dependency_overrides[get_current_user] = lambda: _override_user(Role.MANAGER.value)
    jid = uuid.uuid4()
    f = tmp_path / "admin-backup.zip"
    f.write_text("restricted")
    job = ExportJobModel(
        id=jid,
        tenant_id=TEST_TENANT_ID,
        user_id=uuid.uuid4(),
        scope=ExportScope.SYSTEM,
        export_type=ExportType.FULL_BACKUP,
        status=JobStatus.COMPLETED,
        progress=100,
        file_path=str(f),
        smart_scope="system/*.cruds",
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
async def test_manager_list_hides_system_scope_exports(async_client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: _override_user(Role.MANAGER.value)
    jobs = [
        ExportJobModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            user_id=TEST_USER_ID,
            scope=ExportScope.GROUP,
            export_type=ExportType.FHIR_ONLY,
            status=JobStatus.COMPLETED,
            progress=100,
            smart_scope="group/*.rs",
        ),
        ExportJobModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            user_id=TEST_USER_ID,
            scope=ExportScope.SYSTEM,
            export_type=ExportType.FULL_BACKUP,
            status=JobStatus.COMPLETED,
            progress=100,
            smart_scope="system/*.cruds",
        ),
    ]
    db_mock = AsyncMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = jobs
    db_mock.execute = AsyncMock(return_value=res)
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.get("/api/v1/export/jobs")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["scope"] == "group"
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_export_group_requires_patient_ids(async_client: AsyncClient):
    """A GROUP export without ids would otherwise become a whole-tenant archive."""
    app.dependency_overrides[get_current_user] = lambda: _override_user(Role.MANAGER.value)
    db_mock = AsyncMock()
    app.dependency_overrides[get_db] = _setup_db(db_mock)
    response = await async_client.post(
        "/api/v1/export",
        json={"scope": "group", "export_type": "fhir_only"},
    )
    assert response.status_code == 400
    assert "patient_ids" in response.json()["detail"]
    app.dependency_overrides = {}
