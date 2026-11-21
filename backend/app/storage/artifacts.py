"""Key builders and dual-read helpers for health's non-document byte roots.

Every durable artefact health writes now lands behind the family contract:
documents, import archives, export archives + manifests, anatomy figures and
the seed-pack cache. Keys are server-generated and tenant-prefixed; the
database keeps the key (or, for pre-contract rows, the filesystem path that the
dual-read branch below still accepts while the local volume is mounted).

The manifest of an export is its **own object** (``…/<job>.manifest.json``),
never a ``<file>.manifest.json`` sidecar on a filesystem.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from uuid import UUID

import anyio

from .bootstrap import current_store
from .object_storage import (
    BACKEND_LOCAL,
    ObjectInfo,
    ObjectNotFound,
    ObjectStore,
    filename_extension,
    join_key,
    tenant_key,
)

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024

#: Tenant id used for instance-wide catalogue bytes (seed figures, packs).
SYSTEM_TENANT = "system"


def export_key(
    tenant_id: UUID | str,
    job_id: UUID | str,
    extension: str = "zip",
    *,
    stem: str | None = None,
) -> str:
    """``tenants/<tenant>/exports/<job-id>[.<stem>]<ext>``."""
    object_id = f"{job_id}.{stem}" if stem else str(job_id)
    return tenant_key(str(tenant_id), "exports", object_id, extension)


def export_manifest_key(tenant_id: UUID | str, job_id: UUID | str) -> str:
    """The export manifest is its own object: ``…/<job-id>.manifest.json``."""
    return tenant_key(str(tenant_id), "exports", f"{job_id}.manifest", "json")


def import_key(tenant_id: UUID | str, job_id: UUID | str, filename: str) -> str:
    """``tenants/<tenant>/imports/<job-id><ext>`` (never a user filename)."""
    return tenant_key(str(tenant_id), "imports", str(job_id), filename_extension(filename))


def anatomy_key(slug: str, extension: str = "webp", *, source: bool = False) -> str:
    """``tenants/system/anatomy/<slug>[-source]<ext>`` for a body figure."""
    suffix = "-source" if source else ""
    return tenant_key(SYSTEM_TENANT, "anatomy", f"{slug}{suffix}", extension)


def seed_pack_key(source_id: UUID | str, digest: str) -> str:
    """``tenants/system/seed-packs/<source-id>/<digest>.zip`` (digest-pinned)."""
    return join_key(
        "tenants", SYSTEM_TENANT, "seed-packs", str(source_id), f"{digest}.zip"
    )


def guess_content_type(filename: str | None, default: str = "application/octet-stream") -> str:
    content_type, _ = mimetypes.guess_type(filename or "")
    return content_type or default


def looks_like_key(value: str | None) -> bool:
    """True for a contract key, false for an absolute/pre-contract filesystem path."""
    if not value:
        return False
    return not (value.startswith("/") or value.startswith(".") or os.path.isabs(value))


def _file_chunks(path: Path, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk


@contextmanager
def private_copy(path: Path, suffix: str = ".bin") -> Iterator[Path]:
    """Yield a private ``0600`` temp copy of a durable local file."""
    descriptor, name = tempfile.mkstemp(prefix="ha-fallback-", suffix=suffix)
    target = Path(name)
    try:
        os.chmod(target, 0o600)
        with os.fdopen(descriptor, "wb") as out, path.open("rb") as source:
            shutil.copyfileobj(source, out, CHUNK_SIZE)
        yield target
    finally:
        target.unlink(missing_ok=True)


class ArtifactStorage:
    """Byte access for exports, imports, anatomy figures and seed packs.

    ``read_key``/``stream_key`` are object-first. The pre-contract branches accept
    the old local location **only while the resolved backend is ``local``**:
    a cloud profile never reads a mounted pre-contract volume, matching the family
    migration law (the dual-read branch is a self-host rollback affordance).
    """

    def __init__(self, store: ObjectStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> ObjectStore:
        return self._store if self._store is not None else current_store()

    @property
    def is_local(self) -> bool:
        return self.store.backend == BACKEND_LOCAL

    # ------------------------------------------------------------- primitives

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ObjectInfo:
        return self.store.put(
            key,
            data,
            content_type=content_type or guess_content_type(filename),
        )

    def put_file(
        self,
        key: str,
        path: Path,
        *,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ObjectInfo:
        """Upload a bounded file (one read; the contract has no streaming put).

        The family contract's ``put`` takes bytes, so this reads the file once
        after a size check. Export archives are capped by
        ``HA_EXPORT_MAX_MB`` and the store object cap, and the worker temp
        space budget is ``concurrency x max_object_mb`` plus margin. A
        genuinely streaming upload would require a family contract change and
        is deliberately out of scope for P3.
        """
        from .object_storage import ObjectTooLarge

        size = path.stat().st_size
        limit = self.store.config.max_object_bytes
        if size > limit:
            raise ObjectTooLarge(f"file is {size} bytes; limit is {limit}")
        return self.put_bytes(
            key,
            path.read_bytes(),
            content_type=content_type,
            filename=filename or path.name,
        )

    def stat(self, key: str) -> ObjectInfo | None:
        return self.store.stat(key)

    def exists(self, key: str) -> bool:
        return self.store.stat(key) is not None

    def read(self, key: str) -> bytes | None:
        return self.store.get(key)

    def stream(self, key: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        return self.store.stream(key, chunk_size=chunk_size)

    def delete(self, key: str) -> bool:
        return self.store.delete(key)

    def purge_prefix(self, prefix: str) -> int:
        """Delete every object under a prefix; return the count attempted.

        ``list`` is not the application index, but erasure of a tenant-scoped
        byte root is exactly a migration/GC-shaped operation: prefixes are
        key-addressed and a missed delete is caught by provider lifecycle.
        """
        removed = 0
        for info in list(self.store.list(prefix)):
            if self.store.delete(info.key):
                removed += 1
        return removed

    # ----------------------------------------------------------- async bridge

    async def aread(self, key: str) -> bytes | None:
        return await anyio.to_thread.run_sync(self.read, key)

    async def astat(self, key: str) -> ObjectInfo | None:
        return await anyio.to_thread.run_sync(self.stat, key)

    async def astream(self, key: str, chunk_size: int = CHUNK_SIZE):
        """Async chunk iterator for a route response (store I/O off-loop)."""
        iterator = await anyio.to_thread.run_sync(
            partial(self.stream, key, chunk_size=chunk_size)
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

    def materialize(self, key: str, *, suffix: str | None = None) -> Iterator[Path]:
        from .object_storage import materialize as materialize_object

        return materialize_object(self.store, key, suffix=suffix)

    # --------------------------------------------------------- dual-read glue

    def read_with_fallback(self, key: str | None, fallback: Path | None) -> bytes | None:
        if key:
            data = self.store.get(key)
            if data is not None:
                return data
        if self.is_local and fallback is not None and fallback.exists():
            return fallback.read_bytes()
        return None

    def stream_with_fallback(
        self, key: str | None, fallback: Path | None, chunk_size: int = CHUNK_SIZE
    ) -> Iterator[bytes]:
        if key and self.store.stat(key) is not None:
            return self.store.stream(key, chunk_size=chunk_size)
        if self.is_local and fallback is not None and fallback.exists():
            return _file_chunks(fallback, chunk_size)
        raise ObjectNotFound(key or str(fallback))

    @contextmanager
    def materialize_with_fallback(
        self, key: str | None, fallback: Path | None, *, suffix: str | None = None
    ) -> Iterator[Path]:
        if key and self.store.stat(key) is not None:
            with self.materialize(key, suffix=suffix) as path:
                yield path
            return
        if self.is_local and fallback is not None and fallback.exists():
            with private_copy(fallback, suffix or fallback.suffix or ".bin") as path:
                yield path
            return
        raise ObjectNotFound(key or str(fallback))

    def delete_with_fallback(self, key: str | None, fallback: Path | None) -> bool:
        removed = self.store.delete(key) if key else False
        if self.is_local and fallback is not None and fallback.exists():
            fallback.unlink(missing_ok=True)
            removed = True
        return removed
