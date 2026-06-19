import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workers.tasks import export_backup, import_backup


_EXPORT_ASYNC = export_backup.run.__wrapped__
_IMPORT_ASYNC = import_backup.run.__wrapped__


@pytest.mark.asyncio
async def test_export_backup_task_runs_service(monkeypatch):
    jid = str(uuid.uuid4())

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)

    class FakeFactory:
        def __call__(self):
            return fake_session, MagicMock(dispose=AsyncMock())

    run_called = {}

    class FakeExportService:
        def __init__(self, db):
            self.db = db

        async def run_export(self, job_id):
            run_called["job_id"] = str(job_id)

    monkeypatch.setattr("app.workers.tasks.get_async_session", FakeFactory())
    monkeypatch.setattr("app.services.export_service.ExportService", FakeExportService)

    result = await _EXPORT_ASYNC(None, jid)
    assert result["job_id"] == jid
    assert result["status"] == "completed"
    assert run_called["job_id"] == jid


@pytest.mark.asyncio
async def test_export_backup_task_returns_failed_on_exception(monkeypatch):
    jid = str(uuid.uuid4())

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)

    class FakeFactory:
        def __call__(self):
            return fake_session, MagicMock(dispose=AsyncMock())

    class FakeExportService:
        def __init__(self, db):
            pass

        async def run_export(self, job_id):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.workers.tasks.get_async_session", FakeFactory())
    monkeypatch.setattr("app.services.export_service.ExportService", FakeExportService)

    result = await _EXPORT_ASYNC(None, jid)
    assert result["status"] == "failed"
    assert "boom" in result["error"]


@pytest.mark.asyncio
async def test_import_backup_task_runs_service(monkeypatch, tmp_path):
    jid = str(uuid.uuid4())
    uid = str(uuid.uuid4())
    archive = tmp_path / "b.zip"
    archive.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)

    class FakeFactory:
        def __call__(self):
            return fake_session, MagicMock(dispose=AsyncMock())

    run_called = {}

    class FakeResult:
        status = MagicMock()
        status.value = "COMPLETED"
        processed_records = 3
        failed_records = 0

    class FakeImportService:
        def __init__(self, db):
            pass

        async def run_import(self, job_id, path, owner_id):
            run_called["job_id"] = str(job_id)
            run_called["owner_id"] = str(owner_id)
            return FakeResult()

    monkeypatch.setattr("app.workers.tasks.get_async_session", FakeFactory())
    monkeypatch.setattr("app.services.import_service.ImportService", FakeImportService)

    result = await _IMPORT_ASYNC(None, jid, str(archive), uid)
    assert result["job_id"] == jid
    assert result["status"] == "COMPLETED"
    assert result["processed"] == 3
    assert run_called["owner_id"] == uid


@pytest.mark.asyncio
async def test_import_backup_task_cleans_up_archive(monkeypatch, tmp_path):
    jid = str(uuid.uuid4())
    uid = str(uuid.uuid4())
    archive = tmp_path / "b.zip"
    archive.write_bytes(b"data")

    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)

    class FakeFactory:
        def __call__(self):
            return fake_session, MagicMock(dispose=AsyncMock())

    class FakeResult:
        status = MagicMock()
        status.value = "COMPLETED"
        processed_records = 0
        failed_records = 0

    class FakeImportService:
        def __init__(self, db):
            pass

        async def run_import(self, job_id, path, owner_id):
            return FakeResult()

    monkeypatch.setattr("app.workers.tasks.get_async_session", FakeFactory())
    monkeypatch.setattr("app.services.import_service.ImportService", FakeImportService)

    assert archive.exists()
    await _IMPORT_ASYNC(None, jid, str(archive), uid)
    assert not archive.exists()
