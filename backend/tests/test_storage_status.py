"""Read-only storage status API for system admins (plan 55 P4b2).

The operator surface returns deployment facts, never a credential,
connection string, presigned URL or full endpoint URL. The health row is the
same cached/bounded probe `/health` uses.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.main import app
from app.storage.bootstrap import resolve_storage_config
from app.storage.health import StorageHealthProbe
from app.storage.object_storage import (
    BACKEND_AZURE_BLOB,
    BACKEND_S3,
    ENTRYPOINT_SERVER,
    AzureBlobConfig,
    ObjectStore,
    S3Config,
    StorageConfig,
    build_store,
)
from tests._auth_helpers import auth_headers, create_user


class _Store:
    def __init__(self) -> None:
        self.calls = 0

    def health(self) -> None:
        self.calls += 1


def _s3_config() -> StorageConfig:
    return StorageConfig(
        backend=BACKEND_S3,
        entrypoint=ENTRYPOINT_SERVER,
        cloud_profile="aws",
        required=True,
        s3=S3Config(
            bucket="health-status-bucket",
            region="eu-north-1",
            endpoint_url="https://s3.eu-north-1.amazonaws.com",
            access_key_id="AKIAEXAMPLEKEY",
            secret_access_key="super-secret-value",
            session_token="session-secret-value",
            sse="aws:kms",
            kms_key_id="arn:aws:kms:eu-north-1:123456789012:key/abc",
            prefix="prod/",
        ),
    ).validate()


def _probe(config: StorageConfig, store: _Store) -> StorageHealthProbe:
    return StorageHealthProbe(config, cast(ObjectStore, store))


@pytest.fixture(autouse=True)
def local_storage_probe() -> Iterator[StorageHealthProbe]:
    """Install a real local probe: the ASGI test client skips lifespan."""
    config = resolve_storage_config(settings)
    store = build_store(config)
    probe = StorageHealthProbe(config, store)
    previous = getattr(app.state, "storage_health", None)
    app.state.storage_health = probe
    try:
        yield probe
    finally:
        store.close()
        if previous is None:
            delattr(app.state, "storage_health")
        else:
            app.state.storage_health = previous


async def test_system_admin_reads_local_status(
    async_client: AsyncClient, system_admin_headers: dict[str, str]
) -> None:
    response = await async_client.get("/api/v1/admin/system/storage", headers=system_admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "local"
    assert body["entrypoint"] == "server"
    assert body["cloud_profile"] is None
    assert body["bucket"] is None
    assert body["container"] is None
    assert body["region"] is None
    assert body["endpoint_host"] is None
    assert body["prefix"] == ""
    assert body["sse"] == "n/a"
    assert body["max_object_mb"] == 1024
    assert body["presign_ttl_seconds"] == 300
    assert body["required"] is False
    assert body["status"] == "ok"
    assert body["healthy"] is True
    assert body["checked_at"]


async def test_non_admin_is_forbidden(async_client: AsyncClient) -> None:
    from app.models.enums import Role

    user = await create_user(role=Role.USER, full_name="Plain User")
    headers = await auth_headers(user)
    denied = await async_client.get("/api/v1/admin/system/storage", headers=headers)
    assert denied.status_code == 403


async def test_s3_status_redacts_credentials_and_bare_endpoint_host(
    async_client: AsyncClient, system_admin_headers: dict[str, str]
) -> None:
    store = _Store()
    app.state.storage_health = _probe(_s3_config(), store)
    response = await async_client.get(
        "/api/v1/admin/system/storage?refresh=true", headers=system_admin_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "s3"
    assert body["cloud_profile"] == "aws"
    assert body["bucket"] == "health-status-bucket"
    assert body["region"] == "eu-north-1"
    assert body["endpoint_host"] == "s3.eu-north-1.amazonaws.com"
    assert body["prefix"] == "prod"
    assert body["sse"] == "aws:kms"
    assert body["required"] is True
    assert body["healthy"] is True
    assert store.calls == 1
    for secret in (
        "super-secret-value",
        "session-secret-value",
        "AKIAEXAMPLEKEY",
        "arn:aws:kms",
        "X-Amz-",
    ):
        assert secret not in response.text


async def test_azure_connection_string_yields_host_without_the_secret(
    async_client: AsyncClient, system_admin_headers: dict[str, str]
) -> None:
    config = StorageConfig(
        backend=BACKEND_AZURE_BLOB,
        entrypoint=ENTRYPOINT_SERVER,
        cloud_profile="azure",
        required=True,
        azure_blob=AzureBlobConfig(
            container="health-status-data",
            connection_string=(
                "DefaultEndpointsProtocol=https;AccountName=healthaccount;"
                "AccountKey=azure-secret-key;EndpointSuffix=core.windows.net"
            ),
            prefix="prod/",
        ),
    ).validate()
    app.state.storage_health = _probe(config, _Store())
    response = await async_client.get("/api/v1/admin/system/storage", headers=system_admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["container"] == "health-status-data"
    assert body["bucket"] is None
    assert body["endpoint_host"] == "healthaccount.blob.core.windows.net"
    assert body["sse"] == "provider-managed"
    assert "azure-secret-key" not in response.text
    assert "AccountKey" not in response.text


async def test_health_is_cached_and_refresh_forces_a_probe(
    async_client: AsyncClient, system_admin_headers: dict[str, str]
) -> None:
    store = _Store()
    app.state.storage_health = _probe(_s3_config(), store)

    first = await async_client.get("/api/v1/admin/system/storage", headers=system_admin_headers)
    second = await async_client.get("/api/v1/admin/system/storage", headers=system_admin_headers)
    assert first.status_code == second.status_code == 200
    assert store.calls == 1
    assert second.json()["checked_at"] == first.json()["checked_at"]

    refreshed = await async_client.get(
        "/api/v1/admin/system/storage?refresh=true", headers=system_admin_headers
    )
    assert refreshed.status_code == 200
    assert store.calls == 2
    assert refreshed.json()["checked_at"] is not None


async def test_status_never_exposes_unexpected_fields(
    async_client: AsyncClient, system_admin_headers: dict[str, str]
) -> None:
    body: dict[str, Any] = (
        await async_client.get("/api/v1/admin/system/storage", headers=system_admin_headers)
    ).json()
    assert set(body) == {
        "backend",
        "entrypoint",
        "cloud_profile",
        "bucket",
        "container",
        "region",
        "endpoint_host",
        "prefix",
        "sse",
        "max_object_mb",
        "presign_ttl_seconds",
        "required",
        "status",
        "healthy",
        "detail",
        "checked_at",
    }
