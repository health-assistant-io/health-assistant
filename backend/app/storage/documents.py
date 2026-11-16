"""Document bytes through the family object-storage contract.

``DocumentStorage`` is health's one seam between document rows and the
deployed backend: ``local`` on a self-host volume, ``s3``/``azure_blob``
under a cloud profile. The database stores an opaque server-generated key
(``documents.object_key``) — never a path. Rows written before the contract
carry no key; the facade dual-reads the legacy ``file_path`` while the local
volume is mounted so an upgrade keeps every existing document.

Synchronous methods are the primitives; the ``a*`` variants are the async
bridge used by routes and services (``anyio.to_thread``), so provider latency
never blocks the event loop. ``app.storage.bootstrap`` owns the process store.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import partial
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import anyio

from .artifacts import CHUNK_SIZE, ArtifactStorage, guess_content_type
from .bootstrap import current_store
from .object_storage import (
    BACKEND_LOCAL,
    InvalidObjectKey,
    ObjectInfo,
    ObjectNotFound,
    ObjectStore,
    clamp_presign_ttl,
    filename_extension,
    normalize_key,
    tenant_key,
)
from .object_storage import (
    materialize as materialize_object,
)

#: PHI presigns are clamped at the mint route. The family ceiling is one hour;
#: a health document is far more sensitive than a study blob.
PHI_PRESIGN_TTL_SECONDS = 300


class DocumentRecord(Protocol):
    """The row shape the facade needs (DocumentModel satisfies it)."""

    id: UUID
    tenant_id: UUID | str
    filename: str
    file_path: str | None
    object_key: str | None


def document_key(
    tenant_id: UUID | str, document_id: UUID | str, filename: str | None
) -> str:
    """``tenants/<tenant>/documents/<document-id><safe-ext>``.

    The identifier and tenant are opaque database ids; the extension is
    sanitized for MIME/type detection only and is never a path component.
    """
    extension = filename_extension(filename or "")
    return tenant_key(str(tenant_id), "documents", str(document_id), extension)


@contextmanager
def _private_copy(path: Path, suffix: str = ".bin") -> Iterator[Path]:
    """Yield a private ``0600`` temp copy of a durable local file."""
    descriptor, name = tempfile.mkstemp(prefix="ha-legacy-", suffix=suffix)
    target = Path(name)
    try:
        os.chmod(target, 0o600)
        with os.fdopen(descriptor, "wb") as out, path.open("rb") as source:
            shutil.copyfileobj(source, out, CHUNK_SIZE)
        yield target
    finally:
        target.unlink(missing_ok=True)


class DocumentStorage:
    """Document/derived-byte API over one ``ObjectStore``."""

    def __init__(self, store: ObjectStore | None = None) -> None:
        self._store = store
        self._artifacts = ArtifactStorage(store)

    @property
    def store(self) -> ObjectStore:
        return self._store if self._store is not None else current_store()

    @property
    def backend(self) -> str:
        return self.store.backend

    @property
    def is_local(self) -> bool:
        return self.backend == BACKEND_LOCAL

    @property
    def max_object_bytes(self) -> int:
        return self.store.config.max_object_bytes

    # ------------------------------------------------------------------ keys

    @staticmethod
    def key_for(record: DocumentRecord) -> str:
        """The row's object key, or its deterministic builder when absent."""
        return document_key(
            record.tenant_id, record.id, getattr(record, "filename", "")
        )

    @staticmethod
    def validate_key(record: DocumentRecord, key: str) -> str:
        """Refuse a key that escapes the row's tenant or document category."""
        normalized = normalize_key(key)
        prefix = f"tenants/{record.tenant_id}/documents/"
        if not normalized.startswith(prefix):
            raise InvalidObjectKey(
                f"document key {normalized!r} is outside tenants/{record.tenant_id}/documents/"
            )
        return normalized

    def object_key(self, record: DocumentRecord) -> str:
        stored = getattr(record, "object_key", None)
        if isinstance(stored, str) and stored:
            return self.validate_key(record, stored)
        return self.key_for(record)

    @staticmethod
    def legacy_path(record: DocumentRecord) -> Path | None:
        raw = getattr(record, "file_path", None)
        return Path(str(raw)) if isinstance(raw, str) and raw else None

    # ------------------------------------------------------------- documents

    def put(self, record: DocumentRecord, data: bytes, *, key: str | None = None) -> str:
        """Write the original upload and return its key (caller persists it)."""
        target = self.validate_key(record, key) if key else self.key_for(record)
        self.store.put(
            target,
            data,
            content_type=guess_content_type(getattr(record, "filename", None)),
        )
        return target

    def read(self, record: DocumentRecord) -> bytes | None:
        """Object key first, then the legacy local file (dual-read)."""
        key = self.object_key(record)
        data = self.store.get(key)
        if data is not None:
            return data
        if not self.is_local:
            return None
        legacy = self.legacy_path(record)
        if legacy and legacy.exists():
            with open(legacy, "rb") as handle:
                return handle.read()
        return None

    def stream(self, record: DocumentRecord, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        """Chunked bytes from the object, else the legacy local file."""
        key = self.object_key(record)
        if self.store.stat(key) is not None:
            return self.store.stream(key, chunk_size=chunk_size)
        if self.is_local:
            legacy = self.legacy_path(record)
            if legacy and legacy.exists():
                return self._file_chunks(legacy, chunk_size)
        raise ObjectNotFound(key)

    def exists(self, record: DocumentRecord) -> bool:
        if self.store.stat(self.object_key(record)) is not None:
            return True
        legacy = self.legacy_path(record)
        return self.is_local and legacy is not None and legacy.exists()

    @contextmanager
    def materialize(self, record: DocumentRecord) -> Iterator[Path]:
        """A private ``0600`` temp file for a path-taking processor."""
        key = self.object_key(record)
        if self.store.stat(key) is not None:
            with materialize_object(self.store, key) as path:
                yield path
            return
        if self.is_local:
            legacy = self.legacy_path(record)
            if legacy and legacy.exists():
                with _private_copy(legacy, Path(record.filename).suffix or ".bin") as path:
                    yield path
                return
        raise ObjectNotFound(key)

    def delete(self, record: DocumentRecord, *, object_shared: bool = False) -> None:
        """Remove the object (unless shared) and the legacy local file."""
        if not object_shared:
            self.store.delete(self.object_key(record))
        if not self.is_local or object_shared:
            return
        legacy = self.legacy_path(record)
        if legacy is not None and legacy.exists():
            legacy.unlink(missing_ok=True)

    # --------------------------------------------------------- key primitives

    def put_key(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectInfo:
        return self.store.put(key, data, content_type=content_type, metadata=metadata)

    def read_key(self, key: str) -> bytes | None:
        return self.store.get(key)

    def stream_key(self, key: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        return self.store.stream(key, chunk_size=chunk_size)

    def stat_key(self, key: str) -> ObjectInfo | None:
        return self.store.stat(key)

    def delete_key(self, key: str) -> bool:
        return self.store.delete(key)

    def presign_key(
        self,
        key: str,
        *,
        download_filename: str | None = None,
        inline: bool = False,
        ttl_seconds: int | None = None,
    ) -> str:
        """Presign a PHI object with the health ceiling (≤ 300 s).

        The contract ceiling is 3600 s; health clamps at the mint route so a
        caller (or a future config change) can never mint a longer-lived PHI
        URL by accident.
        """
        ttl = min(
            PHI_PRESIGN_TTL_SECONDS,
            self.store.config.presign_ttl_seconds,
            ttl_seconds if ttl_seconds is not None else PHI_PRESIGN_TTL_SECONDS,
        )
        return self.store.presign_get(
            key,
            clamp_presign_ttl(ttl),
            download_filename=download_filename,
            inline=inline,
        )

    @staticmethod
    def _file_chunks(path: Path, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    # ----------------------------------------------------------- async bridge

    async def aput(self, record: DocumentRecord, data: bytes, *, key: str | None = None) -> str:
        return await anyio.to_thread.run_sync(partial(self.put, record, data, key=key))

    async def aread(self, record: DocumentRecord) -> bytes | None:
        return await anyio.to_thread.run_sync(self.read, record)

    async def astream(
        self, record: DocumentRecord, chunk_size: int = CHUNK_SIZE
    ) -> AsyncIterator[bytes]:
        iterator = await anyio.to_thread.run_sync(
            partial(self.stream, record, chunk_size=chunk_size)
        )
        try:
            while True:
                chunk = await anyio.to_thread.run_sync(next, iterator, None)
                if chunk is None:
                    return
                yield chunk
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                await anyio.to_thread.run_sync(close)

    async def aexists(self, record: DocumentRecord) -> bool:
        return await anyio.to_thread.run_sync(self.exists, record)

    async def apresign_key(
        self,
        key: str,
        *,
        download_filename: str | None = None,
        inline: bool = False,
        ttl_seconds: int | None = None,
    ) -> str:
        return await anyio.to_thread.run_sync(
            partial(
                self.presign_key,
                key,
                download_filename=download_filename,
                inline=inline,
                ttl_seconds=ttl_seconds,
            )
        )

    async def adelete(self, record: DocumentRecord) -> None:
        await anyio.to_thread.run_sync(self.delete, record)

    @asynccontextmanager
    async def amaterialize(self, record: DocumentRecord) -> AsyncIterator[Path]:
        manager = self.materialize(record)
        path = await anyio.to_thread.run_sync(manager.__enter__)
        try:
            yield path
        finally:
            await anyio.to_thread.run_sync(manager.__exit__, None, None, None)

    async def astream_key(
        self, key: str, chunk_size: int = CHUNK_SIZE
    ) -> AsyncIterator[bytes]:
        iterator = await anyio.to_thread.run_sync(
            partial(self.stream_key, key, chunk_size=chunk_size)
        )
        try:
            while True:
                chunk = await anyio.to_thread.run_sync(next, iterator, None)
                if chunk is None:
                    return
                yield chunk
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                await anyio.to_thread.run_sync(close)

    async def aput_key(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        await anyio.to_thread.run_sync(
            partial(self.put_key, key, data, content_type=content_type)
        )
        return key

    async def aread_key(self, key: str) -> bytes | None:
        return await anyio.to_thread.run_sync(self.read_key, key)

    async def astat_key(self, key: str) -> ObjectInfo | None:
        return await anyio.to_thread.run_sync(self.stat_key, key)

    async def adelete_key(self, key: str) -> bool:
        return await anyio.to_thread.run_sync(self.delete_key, key)


def document_storage(store: ObjectStore | None = None) -> DocumentStorage:
    """The process ``DocumentStorage`` (an app/worker boot already built it)."""
    return DocumentStorage(store)


def document_object_key(record: DocumentRecord) -> str:
    """The row's key without constructing a facade (dispatch payloads)."""
    stored = getattr(record, "object_key", None)
    if stored:
        return str(stored)
    return DocumentStorage.key_for(record)


def storage_key_or_legacy(record: Any) -> str:
    """The payload a processor should receive: an object key or a legacy path.

    New rows carry an ``object_key`` and no ``file_path``. A pre-contract row
    carries only ``file_path``. The migration no longer backfills keys, but a
    row may still have both (a manual backfill, or a partially migrated
    deployment): if the keyed object is absent on the **local** backend, the
    legacy file is the only readable copy — pass that path so a rolling
    upgrade does not fail every old document. A cloud backend never falls back
    to a host path.
    """
    stored = getattr(record, "object_key", None)
    storage = DocumentStorage()
    if isinstance(stored, str) and stored:
        legacy = DocumentStorage.legacy_path(record)
        if (
            storage.is_local
            and legacy is not None
            and legacy.exists()
            and storage.stat_key(stored) is None
        ):
            return str(legacy)
        return stored
    raw = getattr(record, "file_path", None)
    if isinstance(raw, str) and raw and storage.is_local:
        return raw
    # A cloud backend must never dispatch a host path. Without a copied
    # object/key the deterministic key is the only safe payload; the worker
    # then fails that document clearly instead of reading a path that does
    # not exist in its container (run the migration tool to copy bytes and
    # backfill keys).
    return DocumentStorage.key_for(record)


async def astorage_key_or_legacy(record: Any) -> str:
    """Async bridge for a dispatch site (legacy existence checks off-loop)."""
    return await anyio.to_thread.run_sync(storage_key_or_legacy, record)
