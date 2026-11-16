"""Download route: presign vs stream, PHI TTL, headers, audit and 404/503."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.storage.bootstrap import ENTRYPOINT_SERVER, StorageConfig
from app.storage.documents import DocumentStorage
from app.storage.object_storage import InvalidObjectKey, ObjectNotFound, StorageUnavailable


class _FakeStore:
    backend = "s3"

    def __init__(self, *, present: bool = True, fail: Exception | None = None) -> None:
        self.present = present
        self.fail = fail
        self.presigns: list[dict] = []
        self.streamed: list[str] = []
        self.threads: list[str] = []
        self.config = StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=Path("/tmp/unused"),
            max_object_mb=10,
            presign_ttl_seconds=3600,
        ).validate()

    def _check(self) -> None:
        self.threads.append(threading.current_thread().name)
        if self.fail is not None:
            raise self.fail

    def stat(self, key):
        self._check()
        return object() if self.present else None

    def get(self, key):
        self._check()
        return b"%PDF-1.4 doc" if self.present else None

    def stream(self, key, chunk_size=65536):
        self._check()
        if not self.present:
            raise ObjectNotFound(key)
        self.streamed.append(key)
        yield b"%PDF-1.4 doc"

    def presign_get(self, key, ttl_seconds=None, *, download_filename=None, inline=False):
        self._check()
        self.presigns.append({"ttl": ttl_seconds, "inline": inline, "key": key})
        return f"https://provider.invalid/{key}?ttl={ttl_seconds}"


def _document(filename: str = "report.pdf"):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        filename=filename,
        file_path="",
        object_key=None,
    )


async def _call(monkeypatch, store, document):
    from app.api.v1.endpoints import documents as endpoint

    monkeypatch.setattr(endpoint, "get_document", AsyncMock(return_value=document))
    monkeypatch.setattr(endpoint, "_validate_presign_claims", AsyncMock())
    audit = AsyncMock()
    monkeypatch.setattr(endpoint, "log_audit_action", audit)
    monkeypatch.setattr(
        "app.core.security.verify_presigned_token",
        lambda token, doc_id: {
            "tenant_id": str(document.tenant_id),
            "user_id": str(uuid4()),
        },
    )
    facade = DocumentStorage(store)  # type: ignore[arg-type]
    monkeypatch.setattr("app.storage.documents.DocumentStorage", lambda: facade)
    response = await endpoint.download_document_endpoint(
        document_id=str(document.id), token="signed", db=AsyncMock()
    )
    return response, audit, store


@pytest.mark.asyncio
async def test_inline_pdf_streams_through_the_app(monkeypatch) -> None:
    document = _document("report.pdf")
    store = _FakeStore()
    response, audit, _ = await _call(monkeypatch, store, document)
    assert store.presigns == [], "inline responses must never be presigned"
    assert response.status_code == 200
    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    assert body == b"%PDF-1.4 doc"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert store.threads, "the route must touch the store"
    assert all(name != threading.current_thread().name for name in store.threads), (
        "store I/O must be off the event loop"
    )
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["new_value"]["delivery"] == "stream"


@pytest.mark.asyncio
async def test_attachment_presigns_with_phi_ceiling_and_headers(monkeypatch) -> None:
    # A blocked active-content type is the only attachment shape the route
    # can be handed (uploads reject it), but it exercises the presign branch.
    document = _document("payload.svg")
    store = _FakeStore()
    response, audit, _ = await _call(monkeypatch, store, document)
    assert response.status_code == 307
    assert len(store.presigns) == 1
    mint = store.presigns[0]
    assert mint["inline"] is False
    assert mint["ttl"] <= 300
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["new_value"]["delivery"] == "presign"


@pytest.mark.asyncio
async def test_vanished_object_answers_404_before_audit(monkeypatch) -> None:
    document = _document("report.pdf")
    store = _FakeStore(present=False)
    with pytest.raises(HTTPException) as caught:
        await _call(monkeypatch, store, document)
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_transient_store_failure_answers_503(monkeypatch) -> None:
    document = _document("report.pdf")
    store = _FakeStore(fail=StorageUnavailable("provider down"))
    with pytest.raises(HTTPException) as caught:
        await _call(monkeypatch, store, document)
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_foreign_tenant_key_is_not_served(monkeypatch) -> None:
    document = _document("report.pdf")
    document.object_key = "tenants/other/documents/x.pdf"
    store = _FakeStore()
    with pytest.raises(InvalidObjectKey):
        await _call(monkeypatch, store, document)
