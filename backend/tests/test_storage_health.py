"""Storage boot log and cached health rows (plan 55 P4b)."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest
from httpx import AsyncClient

from app.main import app as application
from app.storage.bootstrap import log_storage_boot, open_configured_store
from app.storage.health import (
    STATUS_DEGRADED,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    StorageHealthProbe,
)
from app.storage.object_storage import (
    BACKEND_LOCAL,
    BACKEND_S3,
    ENTRYPOINT_SERVER,
    ObjectStore,
    S3Config,
    StorageConfig,
    StorageConfigError,
    StorageUnavailable,
)
from tests.settings_factory import dev_settings


class _Store:
    def __init__(self, error: Exception | None = None, *, delay: float = 0.0) -> None:
        self.calls = 0
        self.error = error
        self.delay = delay
        self.closed = False

    def health(self) -> None:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closed = True


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _local_config(required: bool = False) -> StorageConfig:
    return StorageConfig(
        backend=BACKEND_LOCAL,
        entrypoint=ENTRYPOINT_SERVER,
        required=required,
        local_root=Path("/tmp/ha-health"),
    )


def _s3_config(required: bool = True) -> StorageConfig:
    return StorageConfig(
        backend=BACKEND_S3,
        entrypoint=ENTRYPOINT_SERVER,
        cloud_profile="aws",
        required=required,
        s3=S3Config(bucket="ha-health-bucket", prefix="test/", sse="AES256"),
    ).validate()


def test_boot_log_names_backend_target_prefix_and_sse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = StorageConfig(
        backend=BACKEND_S3,
        entrypoint=ENTRYPOINT_SERVER,
        cloud_profile="aws",
        required=True,
        s3=S3Config(
            bucket="ha-health-bucket",
            prefix="test/",
            sse="AES256",
            access_key_id="AKIAEXAMPLE",
            secret_access_key="super-secret-value",
        ),
    ).validate()
    with caplog.at_level(logging.INFO, logger="app.storage.bootstrap"):
        log_storage_boot(config)
    line = caplog.text
    assert "object storage: backend=s3" in line
    assert "bucket/container=ha-health-bucket" in line
    assert "prefix=test" in line
    assert "sse=AES256" in line
    assert "super-secret-value" not in line
    assert "AKIAEXAMPLE" not in line


def test_probe_is_cached_for_the_ttl() -> None:
    store = _Store()
    clock = _Clock()
    probe = StorageHealthProbe(_local_config(), cast(ObjectStore, store), clock=clock)
    first = probe.snapshot()
    second = probe.snapshot()
    assert first == second
    assert first.status == STATUS_OK
    assert store.calls == 1
    clock.now += 29.0
    probe.snapshot()
    assert store.calls == 1
    clock.now += 2.0
    probe.snapshot()
    assert store.calls == 2


def test_transient_failure_degrades_open_when_not_required() -> None:
    store = _Store(StorageUnavailable("provider 503"))
    probe = StorageHealthProbe(_local_config(required=False), cast(ObjectStore, store))
    health = probe.snapshot()
    assert health.status == STATUS_DEGRADED
    assert health.failed is False
    assert health.detail == "StorageUnavailable"
    assert health.payload()["backend"] == BACKEND_LOCAL


def test_required_storage_fails_hard_immediately() -> None:
    store = _Store(StorageUnavailable("provider 503"))
    probe = StorageHealthProbe(_s3_config(required=True), cast(ObjectStore, store))
    health = probe.snapshot()
    assert health.status == STATUS_UNAVAILABLE
    assert health.failed is True


def test_sustained_failure_fails_hard_even_when_optional() -> None:
    store = _Store(StorageUnavailable("provider 503"))
    clock = _Clock()
    probe = StorageHealthProbe(
        _local_config(required=False),
        cast(ObjectStore, store),
        sustained_seconds=300.0,
        clock=clock,
    )
    assert probe.snapshot().status == STATUS_DEGRADED
    clock.now += 299.0
    probe.invalidate()
    assert probe.snapshot().status == STATUS_DEGRADED
    clock.now += 2.0
    probe.invalidate()
    assert probe.snapshot().status == STATUS_UNAVAILABLE


def test_recovery_clears_the_sustained_failure_window() -> None:
    store = _Store(StorageUnavailable("provider 503"))
    clock = _Clock()
    probe = StorageHealthProbe(_local_config(), cast(ObjectStore, store), clock=clock)
    assert probe.snapshot().status == STATUS_DEGRADED
    store.error = None
    clock.now += 31.0
    assert probe.snapshot().status == STATUS_OK
    store.error = StorageUnavailable("provider 503")
    clock.now += 31.0
    assert probe.snapshot().status == STATUS_DEGRADED


def test_slow_probe_times_out_as_degraded() -> None:
    store = _Store(delay=1.0)
    probe = StorageHealthProbe(
        _local_config(),
        cast(ObjectStore, store),
        probe_timeout_seconds=0.05,
    )
    started = time.monotonic()
    health = probe.snapshot()
    elapsed = time.monotonic() - started
    assert health.status == STATUS_DEGRADED
    assert health.failed is False
    assert health.detail == "TimeoutError"
    assert elapsed < 0.5


def test_slow_required_probe_is_unavailable() -> None:
    store = _Store(delay=1.0)
    probe = StorageHealthProbe(
        _s3_config(required=True),
        cast(ObjectStore, store),
        probe_timeout_seconds=0.05,
    )
    health = probe.snapshot()
    assert health.status == STATUS_UNAVAILABLE
    assert health.failed is True
    assert health.detail == "TimeoutError"


def test_concurrent_snapshot_does_not_block_on_the_probe_lock() -> None:
    store = _Store(delay=1.0)
    probe = StorageHealthProbe(
        _local_config(),
        cast(ObjectStore, store),
        probe_timeout_seconds=0.1,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(probe.snapshot)
        time.sleep(0.02)
        started = time.monotonic()
        second = probe.snapshot()
        elapsed = time.monotonic() - started
        assert first.result().status in (STATUS_OK, STATUS_DEGRADED)
    assert elapsed < 0.5
    assert second.status == STATUS_DEGRADED


def test_os_error_is_a_transient_failure() -> None:
    store = _Store(OSError("storage root is gone"))
    probe = StorageHealthProbe(_local_config(), cast(ObjectStore, store))
    health = probe.snapshot()
    assert health.status == STATUS_DEGRADED
    assert health.detail == "OSError"


def test_unexpected_exception_is_reraised() -> None:
    store = _Store(RuntimeError("programming error"))
    probe = StorageHealthProbe(_local_config(), cast(ObjectStore, store))
    with pytest.raises(RuntimeError, match="programming error"):
        probe.snapshot()


def test_missing_container_is_a_boot_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store(StorageConfigError("Azure container is missing or inaccessible: data"))

    def fake_build(_config: StorageConfig) -> ObjectStore:
        return cast(ObjectStore, store)

    monkeypatch.setattr("app.storage.bootstrap.build_store", fake_build)
    settings = dev_settings(HA_UPLOAD_DIR=str(tmp_path))
    with pytest.raises(StorageConfigError, match="missing or inaccessible"):
        open_configured_store(settings)
    assert store.closed is True


@pytest.mark.asyncio
async def test_health_endpoint_reports_storage_ok(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        application.state,
        "storage_health",
        StorageHealthProbe(_local_config(), cast(ObjectStore, _Store())),
        raising=False,
    )
    response = await async_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["storage"]["status"] == STATUS_OK
    assert body["storage"]["backend"] == BACKEND_LOCAL


@pytest.mark.asyncio
async def test_health_endpoint_degrades_open_on_transient_failure(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        application.state,
        "storage_health",
        StorageHealthProbe(
            _local_config(), cast(ObjectStore, _Store(StorageUnavailable("provider 503")))
        ),
        raising=False,
    )
    response = await async_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["storage"]["status"] == STATUS_DEGRADED
    assert body["storage"]["detail"] == "StorageUnavailable"


@pytest.mark.asyncio
async def test_health_endpoint_fails_when_storage_is_required(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        application.state,
        "storage_health",
        StorageHealthProbe(
            _s3_config(required=True),
            cast(ObjectStore, _Store(StorageUnavailable("provider 503"))),
        ),
        raising=False,
    )
    response = await async_client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["storage"]["status"] == STATUS_UNAVAILABLE
