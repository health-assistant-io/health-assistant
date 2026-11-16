"""Tenant erasure removes the import archive prefix (no orphan PHI object)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.data_lifecycle.erasure import ErasureWalker
from app.storage.artifacts import ArtifactStorage, import_key
from app.storage.bootstrap import ENTRYPOINT_SERVER, StorageConfig
from app.storage.object_storage import build_store


@pytest.mark.asyncio
async def test_tenant_erasure_purges_import_archives(tmp_path, monkeypatch) -> None:
    store = build_store(
        StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=tmp_path / "objects",
            max_object_mb=5,
        ).validate()
    )
    monkeypatch.setattr("app.storage.artifacts.current_store", lambda settings=None: store)
    tenant, other = uuid4(), uuid4()
    ours = [
        import_key(tenant, uuid4(), "a.zip"),
        import_key(tenant, uuid4(), "b.fhir.json"),
    ]
    theirs = import_key(other, uuid4(), "c.zip")
    for key in [*ours, theirs]:
        store.put(key, b"phi archive")

    walker = ErasureWalker(AsyncMock(), dry_run=False)
    await walker._import_archives(tenant)

    storage = ArtifactStorage(store)
    assert walker.report.files["import_archives"] == 2
    assert "import_archives_scrubbed" in walker.report.cascade
    for key in ours:
        assert not storage.exists(key)
    assert storage.exists(theirs), "another tenant's archive must survive"


@pytest.mark.asyncio
async def test_dry_run_counts_without_deleting(tmp_path, monkeypatch) -> None:
    store = build_store(
        StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=tmp_path / "objects",
            max_object_mb=5,
        ).validate()
    )
    monkeypatch.setattr("app.storage.artifacts.current_store", lambda settings=None: store)
    tenant = uuid4()
    key = import_key(tenant, uuid4(), "a.zip")
    store.put(key, b"phi archive")

    walker = ErasureWalker(AsyncMock(), dry_run=True)
    await walker._import_archives(tenant)

    assert walker.report.files["import_archives"] == 1
    assert store.get(key) == b"phi archive"
