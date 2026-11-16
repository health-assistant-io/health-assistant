"""Worker-side materialization: keys become private temps, never durable paths."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from app.storage.bootstrap import ENTRYPOINT_SERVER, StorageConfig, build_store
from app.storage.documents import document_key
from app.workers import ai_tasks
from app.workers.ai_tasks import _document_source


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    store = build_store(
        StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=tmp_path / "objects",
            max_object_mb=5,
        ).validate()
    )
    monkeypatch.setattr(ai_tasks, "current_store", lambda settings=None: store)
    # storage_key_or_legacy resolves the store through its own module
    # binding as well as the worker's; patch both so the isolation holds.
    monkeypatch.setattr("app.storage.documents.current_store", lambda settings=None: store)
    yield store
    store.close()


def _temp_names() -> set[str]:
    return {name for name in os.listdir(tempfile.gettempdir()) if name.startswith("nx-object-")}


def test_key_materializes_private_temp_and_cleans_up(local_store) -> None:
    from uuid import uuid4

    tenant, document = uuid4(), uuid4()
    key = document_key(tenant, document, "scan.pdf")
    local_store.put(key, b"%PDF-1.4 worker bytes")
    before = _temp_names()

    with _document_source(key) as path:
        assert path.exists()
        assert path.read_bytes() == b"%PDF-1.4 worker bytes"
        assert path.suffix == ".pdf"
        assert (path.stat().st_mode & 0o777) == 0o600
        in_block = str(path)

    assert not Path(in_block).exists()
    assert _temp_names() - before == set()


def test_zero_page_missing_object_leaves_no_temp(local_store) -> None:
    before = _temp_names()
    with pytest.raises(FileNotFoundError), _document_source("tenants/t/documents/missing.dcm"):
        pass
    assert _temp_names() == before


def test_partial_stream_failure_still_removes_temp(local_store, monkeypatch) -> None:
    key = "tenants/t/documents/partial.pdf"
    local_store.put(key, b"0123456789")

    def broken_stream(_key, chunk_size=65536):
        yield b"0123"
        raise OSError("provider reset mid-stream")

    monkeypatch.setattr(local_store, "stream", broken_stream)
    before = _temp_names()
    with pytest.raises(OSError), _document_source(key):
        pass
    assert _temp_names() == before


def test_legacy_absolute_path_is_still_accepted(tmp_path, monkeypatch) -> None:
    legacy = tmp_path / "queued.pdf"
    legacy.write_bytes(b"old queued payload")
    with _document_source(str(legacy)) as path:
        assert path == legacy
        assert path.read_bytes() == b"old queued payload"
    # A legacy path is durable input from an already-queued message; the
    # worker does not delete it (the old task contract owned that cleanup).
    assert legacy.exists()


def test_legacy_missing_path_raises() -> None:
    with pytest.raises(FileNotFoundError), _document_source("/tmp/definitely-not-there-ha.pdf"):
        pass


def test_keyed_row_with_missing_object_falls_back_to_local_file(tmp_path, local_store) -> None:
    """Rolling-upgrade regression: a row with a key but no copied object.

    A manual/old backfill could set ``object_key`` before the bytes were
    copied. The worker dispatch must hand the processor the legacy local path
    (not the unreadable key) on a local backend, and that payload must execute
    through the same materialization seam.
    """
    from types import SimpleNamespace
    from uuid import uuid4

    from app.storage.documents import storage_key_or_legacy

    legacy = tmp_path / "pre-contract.pdf"
    legacy.write_bytes(b"legacy OCR bytes")
    row = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        filename="pre-contract.pdf",
        file_path=str(legacy),
        object_key="tenants/t/documents/never-copied.pdf",
    )
    payload = storage_key_or_legacy(row)
    assert payload == str(legacy)

    with _document_source(payload) as path:
        assert path.read_bytes() == b"legacy OCR bytes"


@pytest.mark.asyncio
async def test_retry_dispatch_uses_the_legacy_path_when_the_key_is_missing(
    tmp_path, local_store, monkeypatch
) -> None:
    """The OCR dispatch seam (retry/trigger) receives the readable payload."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    from app.services.documents import document_service

    legacy = tmp_path / "retry.pdf"
    legacy.write_bytes(b"legacy retry bytes")
    row = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        owner_id=uuid4(),
        filename="retry.pdf",
        file_path=str(legacy),
        object_key="tenants/t/documents/retry.pdf",
        status="uploaded",
        progress=0,
        error_message=None,
    )
    apply_async = MagicMock()
    monkeypatch.setattr(
        "app.workers.ai_tasks.ocr_document.apply_async", apply_async
    )
    monkeypatch.setattr(document_service, "get_document", AsyncMock(return_value=row))
    db = AsyncMock()

    await document_service.trigger_extraction(str(row.id), db)

    payload = apply_async.call_args.kwargs["args"][1]
    assert payload == str(legacy)
    with _document_source(payload) as path:
        assert path.read_bytes() == b"legacy retry bytes"


def test_object_backend_never_falls_back_to_a_host_path(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    from app.storage.bootstrap import ENTRYPOINT_SERVER, StorageConfig, build_store
    from app.storage.documents import storage_key_or_legacy

    store = build_store(
        StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=tmp_path / "objects",
            max_object_mb=5,
        ).validate()
    )
    store.backend = "s3"  # simulate a cloud adapter without network
    monkeypatch.setattr("app.storage.documents.current_store", lambda settings=None: store)
    legacy = tmp_path / "pre-contract.pdf"
    legacy.write_bytes(b"not readable under a cloud profile")
    row = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        filename="pre-contract.pdf",
        file_path=str(legacy),
        object_key="tenants/t/documents/key.pdf",
    )
    assert storage_key_or_legacy(row) == row.object_key


def test_cloud_without_object_key_never_returns_a_host_path(tmp_path, monkeypatch) -> None:
    """``object_key IS NULL`` under a cloud backend: deterministic key only."""
    from types import SimpleNamespace
    from uuid import uuid4

    from app.storage.bootstrap import ENTRYPOINT_SERVER, StorageConfig
    from app.storage.documents import storage_key_or_legacy

    store = build_store(
        StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=tmp_path / "objects",
            max_object_mb=5,
        ).validate()
    )
    store.backend = "s3"  # simulate a cloud adapter without network
    monkeypatch.setattr("app.storage.documents.current_store", lambda settings=None: store)
    legacy = tmp_path / "pre-contract.pdf"
    legacy.write_bytes(b"not readable under a cloud profile")
    row = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        filename="pre-contract.pdf",
        file_path=str(legacy),
        object_key=None,
    )
    payload = storage_key_or_legacy(row)
    assert payload != str(legacy)
    assert not payload.startswith("/")
    assert payload == f"tenants/{row.tenant_id}/documents/{row.id}.pdf"
