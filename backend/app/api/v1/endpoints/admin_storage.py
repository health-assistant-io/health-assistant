"""Read-only deployment storage status for the system operator surface.

Object storage is deployment configuration, not a user setting: credentials
are env/instance-role only, and switching backends is a verified migration,
not a save. This route exposes the redacted resolved target plus the cached,
bounded health probe so a system admin can confirm *which* store the process
is using and *whether* it is reachable.

Never returned: credentials, connection strings, presigned URLs or the full
endpoint URL (only the host, which the operator needs to distinguish a
provider endpoint from an AWS region).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast
from urllib.parse import urlsplit

import anyio
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.core.security import RoleChecker
from app.models.enums import Role
from app.schemas.user import TokenData
from app.storage.bootstrap import storage_sse
from app.storage.health import STATUS_OK
from app.storage.object_storage import StorageConfig

router = APIRouter(prefix="/admin/system", tags=["admin"])

StorageStatus = Literal["ok", "degraded", "unavailable"]


class StorageStatusOut(BaseModel):
    backend: str
    entrypoint: str
    cloud_profile: str | None = None
    bucket: str | None = None
    container: str | None = None
    region: str | None = None
    endpoint_host: str | None = None
    prefix: str = ""
    sse: str
    max_object_mb: int
    presign_ttl_seconds: int
    required: bool
    status: StorageStatus
    healthy: bool
    detail: str | None = None
    checked_at: datetime | None = None


def _host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _azure_endpoint_host(config: StorageConfig) -> str | None:
    azure = config.azure_blob
    if azure is None:
        return None
    if azure.account_url:
        return _host(azure.account_url)
    if azure.connection_string:
        # Parse only the endpoint; never echo the connection string (it
        # carries AccountKey). Keys are case-insensitive in Azure's format.
        for raw in azure.connection_string.split(";"):
            key, _, value = raw.partition("=")
            if key.strip().lower() == "blobendpoint":
                return _host(value.strip())
        for raw in azure.connection_string.split(";"):
            key, _, value = raw.partition("=")
            if key.strip().lower() == "accountname" and value.strip():
                return f"{value.strip()}.blob.core.windows.net"
    if azure.account_name:
        return f"{azure.account_name}.blob.core.windows.net"
    return None


def _endpoint_host(config: StorageConfig) -> str | None:
    if config.s3 is not None:
        return _host(config.s3.endpoint_url)
    return _azure_endpoint_host(config)


@router.get("/storage", response_model=StorageStatusOut)
async def get_storage_status(
    request: Request,
    refresh: bool = Query(default=False),
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),  # noqa: B008
) -> StorageStatusOut:
    """The resolved deployment target plus a cached live health row.

    ``refresh=true`` drops the health cache before probing, so the operator
    "test connection" action is a real check instead of a repeat of a
    snapshot up to 30 s old.
    """
    del current_user  # the dependency is the authorization gate
    probe = request.app.state.storage_health
    config = probe.config
    if refresh:
        probe.invalidate()
    health = await anyio.to_thread.run_sync(probe.snapshot)
    return StorageStatusOut(
        backend=config.backend,
        entrypoint=config.entrypoint,
        cloud_profile=config.cloud_profile,
        bucket=config.s3.bucket if config.s3 else None,
        container=config.azure_blob.container if config.azure_blob else None,
        region=config.s3.region if config.s3 else None,
        endpoint_host=_endpoint_host(config),
        prefix=config.key_prefix,
        sse=storage_sse(config),
        max_object_mb=config.max_object_mb,
        presign_ttl_seconds=config.presign_ttl_seconds,
        required=config.required,
        status=cast(StorageStatus, health.status),
        healthy=health.status == STATUS_OK,
        detail=health.detail,
        checked_at=probe.checked_at,
    )
