"""Storage bootstrap for the family object-storage contract.

``Settings`` is the single source of truth: ``.env`` and the process
environment populate the canonical ``HA_*`` fields, and this module maps the
resolved values into the contract's environment mapping. The contract never
reads ``os.environ`` itself (that would miss ``.env``-only values).

The built store is a process singleton: the FastAPI app exposes it as
``app.state.objects`` while request handlers, Celery workers and scripts
reach the same instance through :func:`current_store`. A worker is a separate
process with no ``app.state`` — ``app.workers.celery_app`` probes and
registers the store in ``worker_process_init``.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.core.config import Settings
from app.core.config import settings as global_settings

from .object_storage import (
    BACKEND_LOCAL,
    ENTRYPOINT_SERVER,
    ObjectStore,
    StorageConfig,
    StorageError,
    build_store,
)

logger = logging.getLogger(__name__)

PREFIX = "HA"

_ENV_FIELDS = {
    "CLOUD_PROFILE": "HA_CLOUD_PROFILE",
    "OBJECT_STORAGE_BACKEND": "HA_OBJECT_STORAGE_BACKEND",
    "OBJECT_STORAGE_REQUIRED": "HA_OBJECT_STORAGE_REQUIRED",
    "OBJECT_STORAGE_LOCAL_ROOT": "HA_OBJECT_STORAGE_LOCAL_ROOT",
    "OBJECT_STORAGE_MAX_MB": "HA_OBJECT_STORAGE_MAX_MB",
    "OBJECT_STORAGE_PRESIGN_TTL": "HA_OBJECT_STORAGE_PRESIGN_TTL",
    "S3_BUCKET": "HA_S3_BUCKET",
    "S3_REGION": "HA_S3_REGION",
    "S3_ENDPOINT_URL": "HA_S3_ENDPOINT_URL",
    "S3_ACCESS_KEY_ID": "HA_S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY": "HA_S3_SECRET_ACCESS_KEY",
    "S3_SESSION_TOKEN": "HA_S3_SESSION_TOKEN",
    "S3_PATH_STYLE": "HA_S3_PATH_STYLE",
    "S3_SSE": "HA_S3_SSE",
    "S3_KMS_KEY_ID": "HA_S3_KMS_KEY_ID",
    "S3_PREFIX": "HA_S3_PREFIX",
    "S3_VERIFY_TLS": "HA_S3_VERIFY_TLS",
    "S3_CONNECT_TIMEOUT": "HA_S3_CONNECT_TIMEOUT",
    "S3_READ_TIMEOUT": "HA_S3_READ_TIMEOUT",
    "S3_MAX_ATTEMPTS": "HA_S3_MAX_ATTEMPTS",
    "AZURE_STORAGE_CONTAINER": "HA_AZURE_STORAGE_CONTAINER",
    "AZURE_STORAGE_CONNECTION_STRING": "HA_AZURE_STORAGE_CONNECTION_STRING",
    "AZURE_STORAGE_ACCOUNT_URL": "HA_AZURE_STORAGE_ACCOUNT_URL",
    "AZURE_STORAGE_ACCOUNT_NAME": "HA_AZURE_STORAGE_ACCOUNT_NAME",
    "AZURE_STORAGE_CREDENTIAL": "HA_AZURE_STORAGE_CREDENTIAL",
    "AZURE_STORAGE_PREFIX": "HA_AZURE_STORAGE_PREFIX",
    "AZURE_CONNECT_TIMEOUT": "HA_AZURE_CONNECT_TIMEOUT",
    "AZURE_READ_TIMEOUT": "HA_AZURE_READ_TIMEOUT",
    "AZURE_MAX_ATTEMPTS": "HA_AZURE_MAX_ATTEMPTS",
}

_lock = threading.RLock()
_current: ObjectStore | None = None


def storage_entrypoint(_settings: Settings) -> str:
    """Health is a server-only product: the contract entrypoint is ``server``.

    There is no desktop shell to protect, so the desktop/local-only branch of
    the entrypoint law never applies here. A declared cloud profile still
    forbids the local backend (the contract enforces it).
    """
    return ENTRYPOINT_SERVER


def _env_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text or None


def storage_env(settings: Settings) -> dict[str, str]:
    """The contract's ``HA_*`` mapping from the resolved Settings values."""
    env: dict[str, str] = {}
    for name, field in _ENV_FIELDS.items():
        value = _env_value(getattr(settings, field, None))
        if value is not None:
            env[f"{PREFIX}_{name}"] = value
    return env


def local_root(settings: Settings) -> str:
    """The local backend root: an explicit override, else ``HA_UPLOAD_DIR``."""
    return settings.HA_OBJECT_STORAGE_LOCAL_ROOT or settings.HA_UPLOAD_DIR


def resolve_storage_config(settings: Settings) -> StorageConfig:
    """Validate the deployment storage configuration (raises at boot).

    The per-object cap must cover the **largest** host object class. When the
    operator leaves ``HA_OBJECT_STORAGE_MAX_MB`` unset it binds to
    ``max(MAX_UPLOAD_SIZE, HA_EXPORT_MAX_MB)``: an export archive is much
    larger than one upload, and the store would otherwise refuse every full
    backup over 50 MB. Upload handlers still enforce
    ``min(MAX_UPLOAD_SIZE, contract cap)`` — uploads never inherit the export
    ceiling.
    """
    env = storage_env(settings)
    host_object_cap = max(int(settings.MAX_UPLOAD_SIZE), int(settings.HA_EXPORT_MAX_MB))
    env.setdefault(f"{PREFIX}_OBJECT_STORAGE_MAX_MB", str(host_object_cap))
    return StorageConfig.from_env(
        env,
        PREFIX,
        entrypoint=storage_entrypoint(settings),
        default_local_root=local_root(settings),
    )


def effective_max_upload_bytes(config: StorageConfig, settings: Settings) -> int:
    """The upload cap actually enforced: the smaller of host and contract."""
    host = int(settings.MAX_UPLOAD_SIZE) * 1024 * 1024
    return max(1, min(host, config.max_object_bytes))


def export_max_bytes(settings: Settings | None = None) -> int:
    """The export ceiling enforced before the archive object is written."""
    resolved = settings or global_settings
    return max(1, int(resolved.HA_EXPORT_MAX_MB)) * 1024 * 1024


def storage_target(config: StorageConfig) -> str:
    """The bucket/container name, or the local root for the filesystem backend."""
    if config.s3 is not None:
        return config.s3.bucket
    if config.azure_blob is not None:
        return config.azure_blob.container
    return str(config.local_root or "")


def storage_sse(config: StorageConfig) -> str:
    """The encryption mode the store applies; Azure encrypts provider-side."""
    if config.s3 is not None:
        return config.s3.sse
    if config.azure_blob is not None:
        return "provider-managed"
    return "n/a"


def log_storage_boot(config: StorageConfig) -> None:
    """Log one redacted storage boot line: no credentials, no endpoints."""
    logger.info(
        "object storage: backend=%s bucket/container=%s prefix=%s sse=%s",
        config.backend,
        storage_target(config),
        config.key_prefix or "-",
        storage_sse(config),
    )


def open_configured_store(settings: Settings) -> tuple[StorageConfig, ObjectStore]:
    """Build and health-probe the configured store; close it on probe failure."""
    config = resolve_storage_config(settings)
    store = build_store(config)
    try:
        store.health()
    except StorageError:
        store.close()
        raise
    log_storage_boot(config)
    return config, store


def register(config: StorageConfig, store: ObjectStore) -> None:
    """Make ``store`` the process store (the app/worker calls this once)."""
    global _current
    with _lock:
        _current = store


def current_store(settings: Settings | None = None) -> ObjectStore:
    """The process store, built and probed lazily when no app booted yet.

    API workers use this after ``lifespan`` registered the store; Celery
    workers, scripts and tests reach the same lazy singleton without needing
    ``app.state``.
    """
    global _current
    with _lock:
        if _current is not None:
            return _current
    resolved = settings or global_settings
    config, store = open_configured_store(resolved)
    with _lock:
        if _current is None:
            _current = store
            register(config, store)
            return store
        store.close()
        return _current


def current_config(settings: Settings | None = None) -> StorageConfig:
    """The resolved contract config (the store's config once registered)."""
    with _lock:
        if _current is not None:
            return _current.config
    return resolve_storage_config(settings or global_settings)


def reset_current_store() -> None:
    """Drop the singleton (tests and shutdown); closes the store it owned."""
    global _current
    with _lock:
        store, _current = _current, None
    if store is not None:
        store.close()


def is_object_backend(settings: Settings | None = None) -> bool:
    """True when the resolved deployment stores objects outside the host."""
    return current_config(settings).backend != BACKEND_LOCAL
