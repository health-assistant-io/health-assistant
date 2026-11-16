"""The backup-import task payload is a tenant-scoped object key, not a path."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.enums import JobStatus
from app.models.export_import_job import ImportJobModel


class _Upload:
    filename = "backup.zip"
    content_type = "application/zip"

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._sent = False

    async def read(self, _size: int = -1) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._payload


@pytest.mark.asyncio
async def test_import_backup_dispatches_the_object_key(monkeypatch) -> None:
    from app.api.v1.endpoints import import_data
    from app.storage.artifacts import ArtifactStorage

    tenant_id = uuid4()
    user_id = uuid4()
    job_id = uuid4()
    job = ImportJobModel(
        id=job_id,
        tenant_id=tenant_id,
        user_id=user_id,
        source_filename="backup.zip",
        status=JobStatus.PENDING,
        progress=0,
        total_records=0,
        processed_records=0,
        failed_records=0,
    )

    class FakeService:
        def __init__(self, _db) -> None:
            pass

        async def create_import_job(self, *_args, **_kwargs):
            return job

    monkeypatch.setattr(import_data, "ImportService", FakeService)
    delay = MagicMock()
    monkeypatch.setattr("app.workers.tasks.import_backup.delay", delay)

    current_user = MagicMock()
    current_user.tenant_id = tenant_id
    current_user.user_id = user_id

    response = await import_data.import_backup(
        file=_Upload(b"PK\x03\x04 fake zip"),
        auto_map_biomarkers=True,
        use_ai_normalization=False,
        db=AsyncMock(),
        current_user=current_user,
    )
    assert response.id == str(job_id)

    delay.assert_called_once()
    args = delay.call_args.args
    assert args[0] == str(job_id)
    key = args[1]
    assert key.startswith(f"tenants/{tenant_id}/imports/")
    assert key.endswith(".zip")
    assert not key.startswith("/")
    assert json.loads(args[3]) == {
        "auto_map_biomarkers": True,
        "use_ai_normalization": False,
    }
    assert ArtifactStorage().exists(key)
    ArtifactStorage().delete(key)


@pytest.mark.asyncio
async def test_oversized_import_marks_the_job_failed(monkeypatch) -> None:
    from fastapi import HTTPException

    from app.api.v1.endpoints import import_data
    from app.storage.object_storage import ObjectTooLarge

    tenant_id = uuid4()
    user_id = uuid4()
    job = ImportJobModel(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        source_filename="huge.zip",
        status=JobStatus.PENDING,
    )

    class FakeService:
        def __init__(self, _db) -> None:
            self.failed: list[str] = []

        async def create_import_job(self, *_args, **_kwargs):
            return job

        async def _fail_job(self, job_id, error):
            self.failed.append(f"{job_id}:{error}")

    class FullStore:
        async def aput_key(self, *_args, **_kwargs):
            raise ObjectTooLarge("cap")

        async def adelete_key(self, *_args, **_kwargs):
            return True

    service = FakeService(None)
    monkeypatch.setattr(import_data, "ImportService", lambda _db: service)
    monkeypatch.setattr("app.storage.documents.DocumentStorage", lambda: FullStore())
    current_user = MagicMock()
    current_user.tenant_id = tenant_id
    current_user.user_id = user_id

    with pytest.raises(HTTPException) as caught:
        await import_data.import_backup(
            file=_Upload(b"PK\x03\x04 huge"),
            auto_map_biomarkers=True,
            use_ai_normalization=False,
            db=AsyncMock(),
            current_user=current_user,
        )

    assert caught.value.status_code == 413
    assert service.failed and service.failed[0].startswith(str(job.id))
