"""Export objects have their own 1 GiB ceiling and stream to a temp file."""

from __future__ import annotations

import base64
import glob
import os
import tempfile
import uuid
from pathlib import Path

import pytest

from app.models.enums import ExportScope, ExportType
from app.services.export.export_service import ExportService
from app.storage.bootstrap import (
    ENTRYPOINT_SERVER,
    StorageConfig,
    build_store,
    effective_max_upload_bytes,
    export_max_bytes,
    resolve_storage_config,
)
from app.storage.object_storage import ObjectTooLarge
from tests.settings_factory import dev_settings


def test_export_cap_binds_to_the_larger_object_class() -> None:
    settings = dev_settings(MAX_UPLOAD_SIZE=50, HA_EXPORT_MAX_MB=1024, HA_UPLOAD_DIR="/tmp/x")
    config = resolve_storage_config(settings)
    assert config.max_object_mb == 1024
    # The upload boundary stays clamped to the 50 MB host ceiling.
    assert effective_max_upload_bytes(config, settings) == 50 * 1024 * 1024
    assert export_max_bytes(settings) == 1024 * 1024 * 1024


def test_operator_can_still_raise_both_independently() -> None:
    settings = dev_settings(
        MAX_UPLOAD_SIZE=50,
        HA_EXPORT_MAX_MB=200,
        HA_OBJECT_STORAGE_MAX_MB=300,
        HA_UPLOAD_DIR="/tmp/x",
    )
    config = resolve_storage_config(settings)
    assert config.max_object_mb == 300
    assert effective_max_upload_bytes(config, settings) == 50 * 1024 * 1024


def test_oversized_export_is_refused_and_temp_is_removed(tmp_path, monkeypatch) -> None:
    store = build_store(
        StorageConfig(
            backend="local",
            entrypoint=ENTRYPOINT_SERVER,
            local_root=tmp_path / "objects",
            max_object_mb=1,
        ).validate()
    )
    monkeypatch.setattr("app.storage.artifacts.current_store", lambda settings=None: store)
    before = set(glob.glob(str(Path(tempfile.gettempdir()) / "ha-export-*")))

    svc = ExportService.__new__(ExportService)
    tenant, job = uuid.uuid4(), uuid.uuid4()
    manifest = svc.build_manifest(tenant, ExportScope.SYSTEM, ExportType.FULL_BACKUP, {})
    # Incompressible base64 so the ZIP really exceeds the 1 MB store cap
    # (a repeated string would compress to a few KB).
    sidecars = {"big.json": {"payload": base64.b64encode(os.urandom(2 * 1024 * 1024)).decode()}}
    with pytest.raises(ObjectTooLarge):
        svc.write_full_backup_zip({}, sidecars, [], tenant, job, manifest)

    after = set(glob.glob(str(Path(tempfile.gettempdir()) / "ha-export-*"))) - before
    assert after == set(), f"temp export files leaked: {after}"
    assert list(store.list("tenants")) == []
