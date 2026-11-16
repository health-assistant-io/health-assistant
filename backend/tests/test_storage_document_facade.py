"""DocumentStorage facade: keys, dual-read, materialization and async bridge."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.storage.bootstrap import (
    ENTRYPOINT_SERVER,
    StorageConfig,
    build_store,
)
from app.storage.documents import (
    PHI_PRESIGN_TTL_SECONDS,
    DocumentStorage,
    document_key,
)
from app.storage.object_storage import InvalidObjectKey


@dataclass
class Row:
    id: UUID
    tenant_id: UUID
    filename: str
    file_path: str | None = None
    object_key: str | None = None
    metadata: dict = field(default_factory=dict)


@pytest.fixture
def local_store(tmp_path):
    config = StorageConfig(
        backend="local",
        entrypoint=ENTRYPOINT_SERVER,
        local_root=tmp_path / "objects",
        max_object_mb=5,
        presign_ttl_seconds=900,
    ).validate()
    store = build_store(config)
    yield store
    store.close()


def test_key_is_tenant_and_document_scoped() -> None:
    tenant, document = uuid4(), uuid4()
    key = document_key(tenant, document, "LAB REPORT.PDF")
    assert key == f"tenants/{tenant}/documents/{document}.pdf"


def test_put_read_stream_materialize(local_store) -> None:
    row = Row(uuid4(), uuid4(), "report.pdf")
    storage = DocumentStorage(local_store)
    key = storage.put(row, b"%PDF-1.4 data")
    row.object_key = key
    assert key.startswith(f"tenants/{row.tenant_id}/documents/")
    assert storage.read(row) == b"%PDF-1.4 data"
    assert b"".join(storage.stream(row, chunk_size=3)) == b"%PDF-1.4 data"
    with storage.materialize(row) as path:
        assert path.exists()
        assert (path.stat().st_mode & 0o777) == 0o600
        assert path.read_bytes() == b"%PDF-1.4 data"
    assert not path.exists()
    assert storage.delete(row) is None
    assert storage.read(row) is None


def test_dual_read_legacy_local_path(local_store, tmp_path) -> None:
    legacy = tmp_path / "legacy.pdf"
    legacy.write_bytes(b"legacy bytes")
    row = Row(uuid4(), uuid4(), "legacy.pdf", file_path=str(legacy))
    storage = DocumentStorage(local_store)
    assert storage.read(row) == b"legacy bytes"
    assert b"".join(storage.stream(row)) == b"legacy bytes"
    with storage.materialize(row) as path:
        assert path.read_bytes() == b"legacy bytes"
        assert (path.stat().st_mode & 0o777) == 0o600
    assert not path.exists()
    storage.delete(row)
    assert not legacy.exists()


def test_object_first_wins_over_legacy_file(local_store, tmp_path) -> None:
    legacy = tmp_path / "legacy.pdf"
    legacy.write_bytes(b"old")
    row = Row(uuid4(), uuid4(), "doc.pdf", file_path=str(legacy))
    storage = DocumentStorage(local_store)
    row.object_key = storage.put(row, b"new")
    assert storage.read(row) == b"new"
    assert legacy.read_bytes() == b"old"


def test_foreign_tenant_key_is_refused(local_store) -> None:
    row = Row(uuid4(), uuid4(), "doc.pdf")
    row.object_key = document_key(uuid4(), row.id, row.filename)
    storage = DocumentStorage(local_store)
    with pytest.raises(InvalidObjectKey):
        storage.read(row)


def test_missing_object_raises_for_stream(local_store) -> None:
    from app.storage.object_storage import ObjectNotFound

    row = Row(uuid4(), uuid4(), "missing.pdf")
    storage = DocumentStorage(local_store)
    with pytest.raises(ObjectNotFound):
        list(storage.stream(row))
    with pytest.raises(ObjectNotFound), storage.materialize(row):
        pass


class _CapturingStore:
    """A contract-shaped stub that records presign TTLs and thread names."""

    backend = "s3"

    def __init__(self) -> None:
        self.config = StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=Path("/tmp/unused"),
            max_object_mb=5,
            presign_ttl_seconds=3600,
        ).validate()
        self.ttls: list[int] = []
        self.threads: list[str] = []
        self.data = {("tenants", "t", "documents", "d.pdf"): b"x"}
        self.key = "tenants/t/documents/d.pdf"

    def stat(self, key):
        return object() if key == self.key else None

    def get(self, key):
        self.threads.append(threading.current_thread().name)
        return b"x" if key == self.key else None

    def stream(self, key, chunk_size=65536):
        self.threads.append(threading.current_thread().name)
        yield b"x"

    def put(self, key, data, **kwargs):
        return None

    def delete(self, key):
        return True

    def presign_get(self, key, ttl_seconds=None, *, download_filename=None, inline=False):
        self.ttls.append(int(ttl_seconds))
        return f"https://example.invalid/{key}?ttl={ttl_seconds}"


def test_phi_presign_is_clamped_to_300_seconds() -> None:
    store = _CapturingStore()
    storage = DocumentStorage(store)  # type: ignore[arg-type]
    url = storage.presign_key(store.key)
    assert url.startswith("https://example.invalid/")
    assert store.ttls == [PHI_PRESIGN_TTL_SECONDS]
    assert PHI_PRESIGN_TTL_SECONDS <= 300


def test_presign_never_exceeds_configured_shorter_ttl() -> None:
    store = _CapturingStore()
    store.config = StorageConfig(
        backend="local",
        entrypoint=ENTRYPOINT_SERVER,
        local_root=Path("/tmp/unused"),
        max_object_mb=5,
        presign_ttl_seconds=120,
    ).validate()
    DocumentStorage(store).presign_key(store.key)  # type: ignore[arg-type]
    assert store.ttls == [120]


@pytest.mark.asyncio
async def test_store_calls_are_offloaded_off_the_event_loop(tmp_path) -> None:
    store = _CapturingStore()
    row = Row(uuid4(), "t", "d.pdf")  # type: ignore[arg-type]
    row.tenant_id = "t"  # type: ignore[assignment]
    row.object_key = store.key
    storage = DocumentStorage(store)  # type: ignore[arg-type]
    main = threading.current_thread().name

    assert await storage.aread(row) == b"x"
    assert [chunk async for chunk in storage.astream(row)] == [b"x"]
    assert store.threads, "store calls must have run"
    assert all(name != main for name in store.threads), (
        "store I/O must run on a worker thread, never the event loop"
    )


def test_document_storage_does_not_leak_a_path_in_model_dict() -> None:
    from app.models.document_model import DocumentModel

    model = DocumentModel(
        id=uuid4(),
        tenant_id=uuid4(),
        owner_id=uuid4(),
        filename="report.pdf",
        file_path="/srv/phi/report.pdf",
        object_key="tenants/t/documents/d.pdf",
    )
    assert model.to_dict()["file_path"] == ""
