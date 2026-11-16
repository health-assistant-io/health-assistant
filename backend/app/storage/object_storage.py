# VENDORED FILE — family canonical (ADR-0031 / ADR-0057).
# Synced byte-identically from the family object-storage contract and
# checked by the family wiring verifier; the shared fixture table loads
# alongside it. NEVER edit the vendored copy in-repo — change the family
# canonical, then re-run the sync.
"""Family object-storage contract (ADR-0057 / plan 55) — the shared law.

Products store **user data objects** (documents, images, drawings, OCR
exports, generated artefacts) through one small protocol. The deployment
backend is configuration, never a code path: **local** filesystem on
desktop and self-host, **s3** on AWS and any S3-compatible service
(MinIO, Cloudflare R2, Backblaze B2, Wasabi, Ceph, OCI), and
**azure_blob** on Azure-native deployments.

The contract is deliberately *not* a filesystem wrapper and *not* a
database. It owns:

* the closed backend/profile vocabulary and the entrypoint law —
  **desktop is always local; a cloud profile is never local**;
* opaque object-key validation and the shared key builders (content
  addressed, tenant scoped), so no user-supplied filename becomes a path;
* the backend-neutral ``ObjectStore`` protocol, its error taxonomy, the
  size/presign bounds and the boot health probe;
* the local and S3/Azure adapters, with third-party SDKs imported
  **lazily** so the shared module stays importable with no extras;
* ``run_conformance(store)`` — the same acceptance wall every host runs
  against its configured backend.

**Server-mediated writes.** Users upload through the application; the
server validates type, size and ownership, then writes the object. The
contract never mints presigned **PUT** URLs. Presigned **GET** is
allowed for downloads/previews, is bounded (60–3600 s), and is never
logged. Hosts that cannot presign (local, or an Azure SAS without a key)
fall back to streaming the response.

**Desktop stays simple.** A desktop launch resolves to ``local`` even
when object-storage variables leak into the environment. Asking for an
object backend on desktop is a configuration error, not a silent
fallback — the boundary is enforced, not merely ignored.

Pure stdlib at import time — no SQLAlchemy, boto3 or Azure SDK. The
adapters import their SDK inside ``__init__`` and raise
``StorageConfigError`` with an install hint when the extra is absent.
The fixture table (``contracts/object-storage.fixtures.json``) is the
cross-product acceptance wall for this module.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, Self, runtime_checkable

#: Closed backend vocabulary. ``local`` is the desktop/self-host default;
#: ``s3`` covers AWS S3 and every S3-compatible service; ``azure_blob`` is
#: Azure's native API (an Azure deployment may instead point ``s3`` at an
#: S3-compatible gateway, e.g. MinIO running on Azure).
BACKEND_LOCAL = "local"
BACKEND_S3 = "s3"
BACKEND_AZURE_BLOB = "azure_blob"
BACKENDS = (BACKEND_LOCAL, BACKEND_S3, BACKEND_AZURE_BLOB)
OBJECT_BACKENDS = (BACKEND_S3, BACKEND_AZURE_BLOB)

#: Closed entrypoint vocabulary (ADR-0013). ``desktop`` launches the
#: product shell; ``server`` is docker/web/PaaS.
ENTRYPOINT_DESKTOP = "desktop"
ENTRYPOINT_SERVER = "server"
ENTRYPOINTS = (ENTRYPOINT_DESKTOP, ENTRYPOINT_SERVER)

#: Deployment profile. A cloud profile forbids local storage; an empty,
#: ``none``, ``local`` or ``self_hosted`` profile keeps local optional.
CLOUD_PROFILE_AWS = "aws"
CLOUD_PROFILE_AZURE = "azure"
CLOUD_PROFILE_GCP = "gcp"
CLOUD_PROFILE_GENERIC = "cloud"
CLOUD_PROFILES = (
    CLOUD_PROFILE_AWS,
    CLOUD_PROFILE_AZURE,
    CLOUD_PROFILE_GCP,
    CLOUD_PROFILE_GENERIC,
)
PROFILE_DEFAULT_BACKEND = {
    CLOUD_PROFILE_AWS: BACKEND_S3,
    CLOUD_PROFILE_AZURE: BACKEND_AZURE_BLOB,
    CLOUD_PROFILE_GCP: BACKEND_S3,
    CLOUD_PROFILE_GENERIC: BACKEND_S3,
}

SSE_NONE = "none"
SSE_AES256 = "AES256"
SSE_KMS = "aws:kms"
SSE_MODES = (SSE_NONE, SSE_AES256, SSE_KMS)
CLOUD_REQUIRED_SSE = (SSE_AES256, SSE_KMS)

CONTRACT_VERSION = "object-storage/1.1"

DEFAULT_PRESIGN_TTL_SECONDS = 300
MIN_PRESIGN_TTL_SECONDS = 60
MAX_PRESIGN_TTL_SECONDS = 3600
DEFAULT_MAX_OBJECT_MB = 100
HARD_MAX_OBJECT_MB = 1024
MAX_KEY_LENGTH = 1024
MAX_SEGMENT_LENGTH = 255
DEFAULT_PART_SIZE = 64 * 1024

# --------------------------------------------------------------------- errors


class StorageError(Exception):
    """Base class for every object-storage failure."""


class StorageConfigError(StorageError):
    """Invalid or incomplete configuration (fail at boot, never at upload)."""


class InvalidObjectKey(StorageError, ValueError):
    """An object key violates the family key law."""


class ObjectNotFound(StorageError):
    """The object does not exist."""


class ObjectTooLarge(StorageError):
    """The object exceeds the configured byte limit."""


class StorageUnavailable(StorageError):
    """Transient backend failure (network, throttling, 5xx)."""


class StorageAuthError(StorageError):
    """Credentials rejected or missing permissions."""


class UnsupportedOperation(StorageError):
    """The backend cannot perform the operation (e.g. local presign)."""


# ----------------------------------------------------------------------- keys

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_S3_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_AZURE_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_EXT_RE = re.compile(r"^[a-z0-9]{1,16}$")


def normalize_key(key: str) -> str:
    """Validate and return an object key.

    Keys are opaque, ``/``-separated, ASCII, and relative. Rejected:
    empty keys, absolute paths, backslashes, NUL/control characters,
    ``.``/``..`` segments, empty segments, trailing slashes, non-ASCII
    bytes and keys longer than :data:`MAX_KEY_LENGTH`. A leading or
    trailing space is significant and therefore rejected by the segment
    rule (which requires an alphanumeric first character).
    """

    if not isinstance(key, str) or not key:
        raise InvalidObjectKey("object key must be a non-empty string")
    if len(key) > MAX_KEY_LENGTH:
        raise InvalidObjectKey(f"object key exceeds {MAX_KEY_LENGTH} characters")
    if key.startswith("/"):
        raise InvalidObjectKey("object key must be relative (no leading '/')")
    if key.endswith("/"):
        raise InvalidObjectKey("object key must not end with '/'")
    if "\\" in key:
        raise InvalidObjectKey("object key must not contain backslashes")
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in key):
        raise InvalidObjectKey("object key must be printable ASCII")
    if "//" in key:
        raise InvalidObjectKey("object key must not contain empty segments")
    for segment in key.split("/"):
        if segment in (".", ".."):
            raise InvalidObjectKey("object key must not contain '.' or '..' segments")
        if len(segment) > MAX_SEGMENT_LENGTH:
            raise InvalidObjectKey(f"object key segment exceeds {MAX_SEGMENT_LENGTH} characters")
        if not _SEGMENT_RE.match(segment):
            raise InvalidObjectKey(f"invalid object key segment: {segment!r}")
    return key


def join_key(*parts: str) -> str:
    """Join already-validated segments into one normalized key."""

    if not parts:
        raise InvalidObjectKey("join_key needs at least one segment")
    cleaned = [part.strip("/") for part in parts if part not in (None, "")]
    if not cleaned:
        raise InvalidObjectKey("join_key produced an empty key")
    return normalize_key("/".join(cleaned))


def content_key(sha256: str, prefix: str = "") -> str:
    """Content-addressed key: ``<prefix>sha256/aa/bb/<sha256>``.

    Two-level fan-out keeps directory listings bounded on local
    filesystems; the key is stable on every backend.
    """

    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256.lower()):
        raise InvalidObjectKey("content key needs a 64-character hex sha256")
    digest = sha256.lower()
    key = f"sha256/{digest[:2]}/{digest[2:4]}/{digest}"
    return join_key(prefix, key) if prefix else key


def tenant_key(tenant_id: str, category: str, object_id: str, extension: str = "", prefix: str = "") -> str:
    """Tenant-scoped key: ``<prefix>tenants/<tenant>/<category>/<object><ext>``.

    ``tenant_id`` and ``object_id`` are opaque host identifiers (UUID or
    equivalent), never display names or user-supplied paths. ``category``
    is a short host-owned noun (``documents``, ``drawings``, ``exports``).
    """

    suffix = safe_extension(extension)
    key = join_key("tenants", tenant_id, category, f"{object_id}{suffix}")
    return join_key(prefix, key) if prefix else key


def safe_extension(value: str) -> str:
    """Return a lower-case dotted extension from an extension token.

    ``"pdf"``, ``".pdf"`` and ``"a.tar.gz"`` all reduce to a single
    ``[a-z0-9]{1,16}`` suffix; anything else is dropped. This is a name
    for MIME/type detection, never a path component.
    """

    if not value:
        return ""
    token = value.strip().lower()
    if "/" in token or "\\" in token:
        return ""
    if "." in token:
        token = token.rsplit(".", 1)[-1]
    token = token.lstrip(".")
    if not token or not _EXT_RE.match(token):
        return ""
    return f".{token}"


def filename_extension(filename: str) -> str:
    """Return the safe dotted suffix of a filename (``PHOTO.JPG`` -> ``.jpg``)."""

    if not filename:
        return ""
    return safe_extension(Path(filename).suffix)


# ---------------------------------------------------------------------- config


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    raise StorageConfigError(f"invalid boolean value: {value!r}")


def _int(value: Any, default: int, *, name: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise StorageConfigError(f"{name} must be an integer") from exc


def _float(value: Any, default: float, *, name: str) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise StorageConfigError(f"{name} must be a number") from exc


def env_name(prefix: str, name: str) -> str:
    """Canonical env name: ``<P>_<NAME>`` (prefixed-only, family security law)."""

    token = prefix.upper().rstrip("_")
    if not token or not _ENV_RE.match(token):
        raise StorageConfigError(f"invalid env prefix: {prefix!r}")
    if not _ENV_RE.match(name):
        raise StorageConfigError(f"invalid env name: {name!r}")
    return f"{token}_{name}"


def read_env(env: Mapping[str, str], prefix: str, name: str, default: str | None = None) -> str | None:
    value = env.get(env_name(prefix, name))
    if value is None or value == "":
        return default
    return value


@dataclass(frozen=True)
class S3Config:
    """AWS S3 or any S3-compatible endpoint.

    ``access_key_id``/``secret_access_key`` may be omitted together to use
    the SDK default chain (instance role, ECS task role, IRSA, shared
    config). Supplying only one is a configuration error.
    """

    bucket: str
    region: str | None = None
    endpoint_url: str | None = None
    access_key_id: str | None = field(default=None, repr=False)
    secret_access_key: str | None = field(default=None, repr=False)
    session_token: str | None = field(default=None, repr=False)
    path_style: bool = False
    sse: str = SSE_AES256
    kms_key_id: str | None = None
    prefix: str = ""
    verify_tls: bool = True
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    max_attempts: int = 3

    def issues(self) -> list[str]:
        problems: list[str] = []
        if not self.bucket or not _S3_BUCKET_RE.match(self.bucket):
            problems.append("S3 bucket must be 3-63 lower-case letters, digits, dots or hyphens")
        if bool(self.access_key_id) != bool(self.secret_access_key):
            problems.append("S3 access key id and secret access key must be set together")
        if self.sse not in SSE_MODES:
            problems.append(f"S3 SSE must be one of {SSE_MODES}")
        if self.sse == SSE_KMS and self.kms_key_id and not self.kms_key_id.startswith("arn:"):
            problems.append("S3 KMS key id must be a KMS key ARN when set")
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            problems.append("S3 timeouts must be positive")
        if not 0 <= int(self.max_attempts) <= 10:
            problems.append("S3 max_attempts must be between 0 and 10")
        if self.prefix:
            try:
                normalize_key(self.prefix.rstrip("/"))
            except InvalidObjectKey as exc:
                problems.append(f"S3 prefix: {exc}")
        return problems

    @property
    def safe_prefix(self) -> str:
        return self.prefix.strip("/")

    def redacted(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "region": self.region,
            "endpoint_url": self.endpoint_url,
            "path_style": self.path_style,
            "sse": self.sse,
            "kms_key_id": self.kms_key_id,
            "prefix": self.safe_prefix,
            "verify_tls": self.verify_tls,
            "access_key_id": _redact(self.access_key_id),
            "secret_access_key": _redact(self.secret_access_key),
            "session_token": _redact(self.session_token),
        }


@dataclass(frozen=True)
class AzureBlobConfig:
    """Azure Blob Storage container.

    Supply either ``connection_string`` or ``account_url`` (+ optional
    ``credential``: account key, SAS token, or a token credential object
    supplied by the host). Presigning requires an account key; a
    SAS/token credential raises :class:`UnsupportedOperation` so the host
    can stream instead.
    """

    container: str
    connection_string: str | None = field(default=None, repr=False)
    account_url: str | None = None
    account_name: str | None = None
    # A string account key/SAS, or a host-supplied TokenCredential object.
    credential: object | None = field(default=None, repr=False)
    # Use azure.identity.DefaultAzureCredential (managed identity / workload
    # identity) instead of a static credential. Mutually exclusive with
    # ``credential`` and ``connection_string``.
    managed_identity: bool = False
    prefix: str = ""
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    max_attempts: int = 3

    def issues(self) -> list[str]:
        problems: list[str] = []
        if not self.container or not _AZURE_CONTAINER_RE.match(self.container):
            problems.append(
                "Azure container must be 3-63 lower-case letters, digits or hyphens"
            )
        if not self.connection_string and not self.account_url:
            problems.append("Azure needs AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_URL")
        if self.connection_string and self.account_url:
            problems.append("Azure connection string and account URL are mutually exclusive")
        if self.connection_string and self.managed_identity:
            problems.append("Azure connection string and managed identity are mutually exclusive")
        if self.managed_identity and self.credential:
            problems.append("Azure managed identity and a static credential are mutually exclusive")
        if self.account_url and not self.credential and not self.managed_identity:
            problems.append(
                "Azure account URL needs AZURE_STORAGE_CREDENTIAL, "
                "AZURE_STORAGE_CREDENTIAL=managed_identity, or a connection string"
            )
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            problems.append("Azure timeouts must be positive")
        if not 0 <= int(self.max_attempts) <= 10:
            problems.append("Azure max_attempts must be between 0 and 10")
        if self.prefix:
            try:
                normalize_key(self.prefix.rstrip("/"))
            except InvalidObjectKey as exc:
                problems.append(f"Azure prefix: {exc}")
        return problems

    @property
    def safe_prefix(self) -> str:
        return self.prefix.strip("/")

    def redacted(self) -> dict[str, Any]:
        return {
            "container": self.container,
            "account_url": self.account_url,
            "account_name": self.account_name,
            "managed_identity": self.managed_identity,
            "prefix": self.safe_prefix,
            "connection_string": _redact(self.connection_string),
            "credential": _redact(self.credential),
        }


def _redact(secret: object | None) -> str:
    """Redaction marker for a configured secret (``None`` stays absent)."""

    return "***" if secret else ""


def safe_download_filename(value: str | None, fallback: str = "download") -> str:
    """A quote-free, ASCII attachment name for a presigned response."""

    if not value:
        return fallback
    name = value.replace("\\", "/").rsplit("/", 1)[-1].strip().replace('"', "_")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name[:180] or fallback


def _azure_connection_parts(connection_string: str | None) -> dict[str, str]:
    """Parse a connection string without importing the Azure SDK."""

    parts: dict[str, str] = {}
    for raw in (connection_string or "").split(";"):
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key.strip():
            parts[key.strip()] = value.strip()
    return parts


def _azure_presign_parts(config: AzureBlobConfig) -> tuple[str | None, str | None]:
    """Return ``(account_name, account_key)`` when a SAS can be signed.

    The credential may be an account key or a SAS/token. Only an account
    key can sign a new SAS; a connection string carries both the name and
    the key, so the common Azure configuration presigns without extra
    variables.
    """

    connection = _azure_connection_parts(config.connection_string)
    account_name = config.account_name or connection.get("AccountName")
    account_key = config.credential or connection.get("AccountKey")
    return account_name, account_key


def _is_azure_account_key(value: str | None) -> bool:
    if not value:
        return False
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return False
    return len(decoded) in (32, 64)


@dataclass(frozen=True)
class StorageConfig:
    backend: str
    entrypoint: str = ENTRYPOINT_SERVER
    cloud_profile: str | None = None
    required: bool = False
    local_root: Path | None = None
    s3: S3Config | None = None
    azure_blob: AzureBlobConfig | None = None
    max_object_mb: int = DEFAULT_MAX_OBJECT_MB
    presign_ttl_seconds: int = DEFAULT_PRESIGN_TTL_SECONDS

    def issues(self) -> list[str]:
        problems: list[str] = []
        if self.backend not in BACKENDS:
            problems.append(f"unknown object-storage backend: {self.backend!r}")
        if self.entrypoint not in ENTRYPOINTS:
            problems.append(f"unknown entrypoint: {self.entrypoint!r}")
        if not 1 <= int(self.max_object_mb) <= HARD_MAX_OBJECT_MB:
            problems.append(
                f"max_object_mb must be between 1 and {HARD_MAX_OBJECT_MB}"
            )
        if not MIN_PRESIGN_TTL_SECONDS <= int(self.presign_ttl_seconds) <= MAX_PRESIGN_TTL_SECONDS:
            problems.append(
                "presign_ttl_seconds must be between "
                f"{MIN_PRESIGN_TTL_SECONDS} and {MAX_PRESIGN_TTL_SECONDS}"
            )
        try:
            resolved = resolve_backend(
                self.entrypoint,
                self.backend,
                self.cloud_profile,
                require_object_storage=self.required,
            )
        except StorageConfigError as exc:
            problems.append(str(exc))
        else:
            if resolved != self.backend:
                problems.append(f"backend {self.backend!r} resolves to {resolved!r}")
        if self.backend == BACKEND_LOCAL:
            if self.local_root is None:
                problems.append("local backend requires local_root")
        elif self.backend == BACKEND_S3:
            if self.s3 is None:
                problems.append("s3 backend requires an S3Config")
            else:
                problems.extend(self.s3.issues())
                endpoint = (self.s3.endpoint_url or "").lower()
                if endpoint and not endpoint.startswith(("http://", "https://")):
                    problems.append("S3 endpoint URL must be http(s)")
                if self.cloud_profile and endpoint and not endpoint.startswith("https://"):
                    problems.append("cloud deployments require an https S3 endpoint")
                if self.cloud_profile and not self.s3.verify_tls:
                    problems.append("cloud deployments cannot disable S3 TLS verification")
            if self.cloud_profile and self.s3 and self.s3.sse not in CLOUD_REQUIRED_SSE:
                problems.append("cloud deployments require SSE AES256 or aws:kms")
        elif self.backend == BACKEND_AZURE_BLOB:
            if self.azure_blob is None:
                problems.append("azure_blob backend requires an AzureBlobConfig")
            else:
                problems.extend(self.azure_blob.issues())
                account_url = (self.azure_blob.account_url or "").lower()
                if account_url and not account_url.startswith(("http://", "https://")):
                    problems.append("Azure account URL must be http(s)")
                if self.cloud_profile and account_url and not account_url.startswith("https://"):
                    problems.append("cloud deployments require an https Azure account URL")
                connection = (self.azure_blob.connection_string or "").lower()
                if self.cloud_profile and re.search(
                    r"defaultendpointsprotocol\s*=\s*http(?:;|$)", connection
                ):
                    problems.append("cloud deployments require https in the Azure connection string")
        return problems

    def validate(self) -> StorageConfig:
        problems = self.issues()
        if problems:
            raise StorageConfigError("; ".join(problems))
        return self

    @property
    def is_object(self) -> bool:
        return self.backend in OBJECT_BACKENDS

    @property
    def max_object_bytes(self) -> int:
        return int(self.max_object_mb) * 1024 * 1024

    @property
    def key_prefix(self) -> str:
        if self.backend == BACKEND_S3 and self.s3:
            return self.s3.safe_prefix
        if self.backend == BACKEND_AZURE_BLOB and self.azure_blob:
            return self.azure_blob.safe_prefix
        return ""

    def redacted(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "entrypoint": self.entrypoint,
            "cloud_profile": self.cloud_profile,
            "required": self.required,
            "local_root": str(self.local_root) if self.local_root else None,
            "max_object_mb": self.max_object_mb,
            "presign_ttl_seconds": self.presign_ttl_seconds,
            "s3": self.s3.redacted() if self.s3 else None,
            "azure_blob": self.azure_blob.redacted() if self.azure_blob else None,
        }

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        prefix: str,
        *,
        entrypoint: str = ENTRYPOINT_SERVER,
        default_local_root: str | Path | None = None,
    ) -> StorageConfig:
        """Build and validate a config from ``<P>_OBJECT_STORAGE_*`` env.

        Boot-time failure is the point: a misspelled backend, a missing
        bucket or a key without a secret must stop the process, not the
        first user upload.
        """

        profile = normalize_cloud_profile(read_env(env, prefix, "CLOUD_PROFILE"))
        requested = read_env(env, prefix, "OBJECT_STORAGE_BACKEND")
        required = _bool(read_env(env, prefix, "OBJECT_STORAGE_REQUIRED"), default=False)
        if profile:
            required = True
        backend = resolve_backend(
            entrypoint, requested, profile, require_object_storage=required
        )

        local_root_raw = read_env(env, prefix, "OBJECT_STORAGE_LOCAL_ROOT")
        local_root = (
            Path(local_root_raw or default_local_root)
            if (local_root_raw or default_local_root)
            else None
        )

        s3 = None
        if backend == BACKEND_S3:
            endpoint_url = read_env(env, prefix, "S3_ENDPOINT_URL")
            # SSE is mandatory in a cloud profile. Outside one, a custom
            # endpoint (MinIO/R2/B2) may reject the AWS SSE header, so the
            # default is `none` there unless the operator opts in.
            sse_default = SSE_AES256 if (profile or not endpoint_url) else SSE_NONE
            s3 = S3Config(
                bucket=read_env(env, prefix, "S3_BUCKET", "") or "",
                region=read_env(env, prefix, "S3_REGION"),
                endpoint_url=endpoint_url,
                access_key_id=read_env(env, prefix, "S3_ACCESS_KEY_ID"),
                secret_access_key=read_env(env, prefix, "S3_SECRET_ACCESS_KEY"),
                session_token=read_env(env, prefix, "S3_SESSION_TOKEN"),
                path_style=_bool(
                    read_env(env, prefix, "S3_PATH_STYLE"), default=bool(endpoint_url)
                ),
                sse=(read_env(env, prefix, "S3_SSE", sse_default) or sse_default),
                kms_key_id=read_env(env, prefix, "S3_KMS_KEY_ID"),
                prefix=read_env(env, prefix, "S3_PREFIX", "") or "",
                verify_tls=_bool(read_env(env, prefix, "S3_VERIFY_TLS"), default=True),
                connect_timeout=_float(
                    read_env(env, prefix, "S3_CONNECT_TIMEOUT"), 5.0, name="S3_CONNECT_TIMEOUT"
                ),
                read_timeout=_float(
                    read_env(env, prefix, "S3_READ_TIMEOUT"), 30.0, name="S3_READ_TIMEOUT"
                ),
                max_attempts=_int(
                    read_env(env, prefix, "S3_MAX_ATTEMPTS"), 3, name="S3_MAX_ATTEMPTS"
                ),
            )

        azure = None
        if backend == BACKEND_AZURE_BLOB:
            credential_raw = read_env(env, prefix, "AZURE_STORAGE_CREDENTIAL")
            managed_identity = (credential_raw or "").strip().lower() in (
                "managed_identity",
                "managed-identity",
                "default_azure",
                "default-azure",
                "defaultazurecredential",
            )
            azure = AzureBlobConfig(
                container=read_env(env, prefix, "AZURE_STORAGE_CONTAINER", "") or "",
                connection_string=read_env(env, prefix, "AZURE_STORAGE_CONNECTION_STRING"),
                account_url=read_env(env, prefix, "AZURE_STORAGE_ACCOUNT_URL"),
                account_name=read_env(env, prefix, "AZURE_STORAGE_ACCOUNT_NAME"),
                credential=None if managed_identity else credential_raw,
                managed_identity=managed_identity,
                prefix=read_env(env, prefix, "AZURE_STORAGE_PREFIX", "") or "",
                connect_timeout=_float(
                    read_env(env, prefix, "AZURE_CONNECT_TIMEOUT"), 5.0, name="AZURE_CONNECT_TIMEOUT"
                ),
                read_timeout=_float(
                    read_env(env, prefix, "AZURE_READ_TIMEOUT"), 30.0, name="AZURE_READ_TIMEOUT"
                ),
                max_attempts=_int(
                    read_env(env, prefix, "AZURE_MAX_ATTEMPTS"), 3, name="AZURE_MAX_ATTEMPTS"
                ),
            )

        return cls(
            backend=backend,
            entrypoint=entrypoint,
            cloud_profile=profile,
            required=required,
            local_root=local_root,
            s3=s3,
            azure_blob=azure,
            max_object_mb=_int(
                read_env(env, prefix, "OBJECT_STORAGE_MAX_MB"),
                DEFAULT_MAX_OBJECT_MB,
                name="OBJECT_STORAGE_MAX_MB",
            ),
            presign_ttl_seconds=_int(
                read_env(env, prefix, "OBJECT_STORAGE_PRESIGN_TTL"),
                DEFAULT_PRESIGN_TTL_SECONDS,
                name="OBJECT_STORAGE_PRESIGN_TTL",
            ),
        ).validate()


def normalize_cloud_profile(value: str | None) -> str | None:
    """Map a deployment profile string to the closed vocabulary.

    ``""``, ``none``, ``local``, ``on_prem``/``self_hosted`` mean "not a
    cloud deployment". An unknown non-empty value is a configuration
    error — a typo must never silently disable the cloud rule.
    """

    if value is None:
        return None
    token = str(value).strip().lower().replace("-", "_")
    if token in ("", "none", "local", "on_prem", "onprem", "self_hosted", "selfhosted"):
        return None
    if token in CLOUD_PROFILES:
        return token
    raise StorageConfigError(f"unknown cloud profile: {value!r}")


def resolve_backend(
    entrypoint: str,
    requested: str | None,
    cloud_profile: str | None,
    *,
    require_object_storage: bool = False,
) -> str:
    """Apply the entrypoint/cloud law and return the effective backend.

    * ``desktop`` -> always ``local``. A non-local request is an error.
    * ``server`` + no request -> the profile default (``aws``/``gcp``/
      ``cloud`` -> ``s3``, ``azure`` -> ``azure_blob``) or ``local``.
    * a cloud profile (or ``require_object_storage``) forbids ``local``.
    """

    entry = (entrypoint or "").strip().lower()
    if entry not in ENTRYPOINTS:
        raise StorageConfigError(f"unknown entrypoint: {entrypoint!r}")
    profile = normalize_cloud_profile(cloud_profile)
    choice = (requested or "").strip().lower() or None
    if choice is not None and choice not in BACKENDS:
        raise StorageConfigError(f"unknown object-storage backend: {requested!r}")

    if entry == ENTRYPOINT_DESKTOP:
        if profile is not None:
            raise StorageConfigError("desktop entrypoint cannot declare a cloud profile")
        if choice is not None and choice != BACKEND_LOCAL:
            raise StorageConfigError(
                f"desktop entrypoint is local-only; refusing {choice!r}"
            )
        return BACKEND_LOCAL

    if profile is not None and choice == BACKEND_LOCAL:
        raise StorageConfigError(
            f"cloud profile {profile!r} forbids local object storage"
        )
    if profile in (CLOUD_PROFILE_AWS, CLOUD_PROFILE_GCP) and choice == BACKEND_AZURE_BLOB:
        raise StorageConfigError(
            f"cloud profile {profile!r} cannot use azure_blob; use s3"
        )
    if require_object_storage and choice == BACKEND_LOCAL:
        raise StorageConfigError("object storage is required but backend is local")
    if choice is not None:
        return choice
    if profile is not None:
        return PROFILE_DEFAULT_BACKEND[profile]
    return BACKEND_LOCAL


def clamp_presign_ttl(ttl_seconds: int | str | None) -> int:
    """Clamp a presign TTL into the family bounds (default 300 s)."""

    if ttl_seconds is None or ttl_seconds == "":
        return DEFAULT_PRESIGN_TTL_SECONDS
    try:
        value = int(ttl_seconds)
    except (TypeError, ValueError) as exc:
        raise StorageConfigError("presign TTL must be an integer") from exc
    return max(MIN_PRESIGN_TTL_SECONDS, min(MAX_PRESIGN_TTL_SECONDS, value))


# ---------------------------------------------------------------------- errors


_S3_NOT_FOUND = {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}
_S3_AUTH = {
    "AccessDenied",
    "AccessDeniedException",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "ExpiredToken",
    "InvalidToken",
    "403",
}
_S3_UNAVAILABLE = {
    "SlowDown",
    "ServiceUnavailable",
    "InternalError",
    "RequestTimeout",
    "RequestTimeTooSkewed",
    "Throttling",
    "ThrottlingException",
    "503",
    "500",
}
_AZURE_NOT_FOUND = {"ResourceNotFoundError", "BlobNotFound", "ContainerNotFound", "404"}
_AZURE_AUTH = {"ClientAuthenticationError", "AuthenticationFailed", "AuthorizationFailure", "403"}
_AZURE_UNAVAILABLE = {
    "ServiceRequestError",
    "ServiceResponseError",
    "ServerBusy",
    "OperationTimedOut",
    "500",
    "503",
}


def classify_backend_error(backend: str, code: str | int | None) -> str:
    """Map a provider error code to the contract error class name.

    The adapters use the same table, so hosts catch the same six classes
    regardless of backend. Unknown codes fall back to
    ``StorageUnavailable`` (never a raw provider exception).
    """

    token = "" if code is None else str(code)
    if backend == BACKEND_S3:
        if token in _S3_NOT_FOUND:
            return "ObjectNotFound"
        if token in _S3_AUTH:
            return "StorageAuthError"
        if token in _S3_UNAVAILABLE:
            return "StorageUnavailable"
        if token in ("EntityTooLarge", "413"):
            return "ObjectTooLarge"
        return "StorageUnavailable"
    if backend == BACKEND_AZURE_BLOB:
        if token in _AZURE_NOT_FOUND:
            return "ObjectNotFound"
        if token in _AZURE_AUTH:
            return "StorageAuthError"
        if token in _AZURE_UNAVAILABLE:
            return "StorageUnavailable"
        if token in ("413", "RequestBodyTooLarge"):
            return "ObjectTooLarge"
        return "StorageUnavailable"
    return "StorageError"


_ERROR_CLASS = {
    "ObjectNotFound": ObjectNotFound,
    "StorageAuthError": StorageAuthError,
    "StorageUnavailable": StorageUnavailable,
    "ObjectTooLarge": ObjectTooLarge,
    "StorageError": StorageError,
}


def error_for(backend: str, code: str | int | None, message: str = "") -> StorageError:
    cls = _ERROR_CLASS[classify_backend_error(backend, code)]
    return cls(message or f"{backend} error: {code}")


# ------------------------------------------------------------------- protocol


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    sha256: str | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class ObjectStore(Protocol):
    """The backend-neutral store every product writes through."""

    backend: str
    config: StorageConfig

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectInfo: ...

    def get(self, key: str) -> bytes | None: ...

    def stream(self, key: str, chunk_size: int = DEFAULT_PART_SIZE) -> Iterator[bytes]: ...

    def exists(self, key: str) -> bool: ...

    def stat(self, key: str) -> ObjectInfo | None: ...

    def delete(self, key: str) -> bool: ...

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]: ...

    def presign_get(
        self,
        key: str,
        ttl_seconds: int | None = None,
        *,
        download_filename: str | None = None,
        inline: bool = False,
    ) -> str: ...

    def health(self) -> None: ...

    def close(self) -> None: ...


class _BaseStore:
    backend = "base"

    def __init__(self, config: StorageConfig) -> None:
        self.config = config

    def _full_key(self, key: str) -> str:
        normalized = normalize_key(key)
        prefix = self.config.key_prefix
        return join_key(prefix, normalized) if prefix else normalized

    def _check_size(self, data: bytes) -> None:
        if len(data) > self.config.max_object_bytes:
            raise ObjectTooLarge(
                f"object is {len(data)} bytes; limit is {self.config.max_object_bytes}"
            )

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectInfo:
        raise NotImplementedError

    def get(self, key: str) -> bytes | None:
        raise NotImplementedError

    def stream(self, key: str, chunk_size: int = DEFAULT_PART_SIZE) -> Iterator[bytes]:
        data = self.get(key)
        if data is None:
            raise ObjectNotFound(key)
        for offset in range(0, len(data), max(1, chunk_size)):
            yield data[offset : offset + max(1, chunk_size)]

    def exists(self, key: str) -> bool:
        return self.stat(key) is not None

    def stat(self, key: str) -> ObjectInfo | None:
        raise NotImplementedError

    def delete(self, key: str) -> bool:
        raise NotImplementedError

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        raise NotImplementedError

    def presign_get(
        self,
        key: str,
        ttl_seconds: int | None = None,
        *,
        download_filename: str | None = None,
        inline: bool = False,
    ) -> str:
        raise UnsupportedOperation(f"{self.backend} cannot presign downloads")

    def health(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------- local adapter


class LocalObjectStore(_BaseStore):
    """Filesystem backend for desktop and self-host deployments.

    Atomic writes (temp file + ``os.replace``), root-confined key
    resolution, and the same ``ObjectInfo`` shape as the cloud adapters.
    Presigned downloads are unsupported; the host streams through its API.
    """

    backend = BACKEND_LOCAL

    def __init__(self, config: StorageConfig) -> None:
        super().__init__(config)
        if config.local_root is None:
            raise StorageConfigError("local backend requires local_root")
        self.root = Path(config.local_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        full = self._full_key(key)
        path = (self.root / full).resolve()
        if path != self.root and self.root not in path.parents:
            raise InvalidObjectKey(f"key escapes local root: {key!r}")
        return path

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectInfo:
        self._check_size(data)
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = _digest(data)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        return ObjectInfo(
            key=normalize_key(key),
            size=len(data),
            sha256=digest,
            content_type=content_type,
            etag=digest,
            metadata=dict(metadata or {}),
        )

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return path.read_bytes()

    def stream(self, key: str, chunk_size: int = DEFAULT_PART_SIZE) -> Iterator[bytes]:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(normalize_key(key))
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(max(1, chunk_size))
                if not chunk:
                    break
                yield chunk

    def stat(self, key: str) -> ObjectInfo | None:
        path = self._path(key)
        if not path.is_file():
            return None
        stat = path.stat()
        return ObjectInfo(
            key=normalize_key(key),
            size=stat.st_size,
            etag=None,
            last_modified=str(int(stat.st_mtime)),
        )

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        if prefix:
            prefix = normalize_key(prefix.rstrip("/"))
        full_prefix = self._full_key(prefix) if prefix else self.config.key_prefix
        base = self.root / full_prefix if full_prefix else self.root
        if not base.exists():
            return
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            rel = path.relative_to(self.root).as_posix()
            if full_prefix and not rel.startswith(full_prefix):
                continue
            key = rel[len(self.config.key_prefix) + 1 :] if self.config.key_prefix else rel
            yield ObjectInfo(
                key=key,
                size=path.stat().st_size,
                last_modified=str(int(path.stat().st_mtime)),
            )

    def health(self) -> None:
        probe = self.root / ".health"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageUnavailable(f"local storage root is not writable: {self.root}") from exc


# ----------------------------------------------------------------- s3 adapter


class S3ObjectStore(_BaseStore):
    """AWS S3 and S3-compatible endpoints (boto3, imported lazily)."""

    backend = BACKEND_S3

    def __init__(self, config: StorageConfig) -> None:
        super().__init__(config)
        if config.s3 is None:
            raise StorageConfigError("s3 backend requires an S3Config")
        try:
            import boto3
            from botocore.config import Config as BotoConfig
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError as exc:  # pragma: no cover - exercised in optional-dep test
            raise StorageConfigError(
                "s3 backend requires boto3; install the 'object-storage-s3' extra"
            ) from exc
        self._errors = (ClientError, BotoCoreError)
        cfg = config.s3
        client_kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": cfg.region,
            "endpoint_url": cfg.endpoint_url,
            "aws_access_key_id": cfg.access_key_id,
            "aws_secret_access_key": cfg.secret_access_key,
            "aws_session_token": cfg.session_token,
            "verify": cfg.verify_tls,
            "config": BotoConfig(
                signature_version="s3v4",
                # Dotted bucket names break virtual-host TLS certificates;
                # path-style keeps `my.backup.bucket` on the endpoint host.
                s3={
                    "addressing_style": "path"
                    if (cfg.path_style or "." in cfg.bucket)
                    else "auto"
                },
                retries={"max_attempts": cfg.max_attempts, "mode": "standard"},
                connect_timeout=cfg.connect_timeout,
                read_timeout=cfg.read_timeout,
            ),
        }
        self._client = boto3.client(**client_kwargs)

    def _map(self, exc: Exception) -> StorageError:
        code = None
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = (response.get("Error") or {}).get("Code")
            status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
            if code is None and status is not None:
                code = status
        return error_for(self.backend, code, str(exc))

    def _object_kwargs(self, content_type: str | None) -> dict[str, Any]:
        cfg = self.config.s3
        assert cfg is not None
        kwargs: dict[str, Any] = {}
        if content_type:
            kwargs["ContentType"] = content_type
        if cfg.sse != SSE_NONE:
            kwargs["ServerSideEncryption"] = cfg.sse
            if cfg.sse == SSE_KMS and cfg.kms_key_id:
                kwargs["SSEKMSKeyId"] = cfg.kms_key_id
        return kwargs

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectInfo:
        self._check_size(data)
        full = self._full_key(key)
        digest = _digest(data)
        meta = {str(k): str(v) for k, v in (metadata or {}).items()}
        meta.setdefault("sha256", digest)
        meta.setdefault("contract", CONTRACT_VERSION)
        args = self._object_kwargs(content_type or "application/octet-stream")
        args.update({"Bucket": self.config.s3.bucket, "Key": full, "Body": data, "Metadata": meta})
        try:
            response = self._client.put_object(**args)
        except self._errors as exc:
            raise self._map(exc) from exc
        return ObjectInfo(
            key=normalize_key(key),
            size=len(data),
            sha256=digest,
            content_type=content_type,
            etag=(response.get("ETag") or "").strip('"') or None,
            metadata=meta,
        )

    def get(self, key: str) -> bytes | None:
        full = self._full_key(key)
        try:
            response = self._client.get_object(Bucket=self.config.s3.bucket, Key=full)
        except self._errors as exc:
            error = self._map(exc)
            if isinstance(error, ObjectNotFound):
                return None
            raise error from exc
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def stream(self, key: str, chunk_size: int = DEFAULT_PART_SIZE) -> Iterator[bytes]:
        full = self._full_key(key)
        try:
            response = self._client.get_object(Bucket=self.config.s3.bucket, Key=full)
        except self._errors as exc:
            raise self._map(exc) from exc
        body = response["Body"]
        try:
            yield from body.iter_chunks(chunk_size=max(1, chunk_size))
        finally:
            body.close()

    def exists(self, key: str) -> bool:
        return self.stat(key) is not None

    def stat(self, key: str) -> ObjectInfo | None:
        full = self._full_key(key)
        try:
            response = self._client.head_object(Bucket=self.config.s3.bucket, Key=full)
        except self._errors as exc:
            error = self._map(exc)
            if isinstance(error, ObjectNotFound):
                return None
            raise error from exc
        meta = {str(k): str(v) for k, v in (response.get("Metadata") or {}).items()}
        return ObjectInfo(
            key=normalize_key(key),
            size=int(response.get("ContentLength") or 0),
            sha256=meta.get("sha256"),
            content_type=response.get("ContentType"),
            etag=(response.get("ETag") or "").strip('"') or None,
            last_modified=str(response.get("LastModified") or ""),
            metadata=meta,
        )

    def delete(self, key: str) -> bool:
        if self.stat(key) is None:
            return False
        full = self._full_key(key)
        try:
            self._client.delete_object(Bucket=self.config.s3.bucket, Key=full)
        except self._errors as exc:
            raise self._map(exc) from exc
        return True

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        base = self._full_key(prefix.rstrip("/")) if prefix else self.config.key_prefix
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self.config.s3.bucket, Prefix=base):
                for item in page.get("Contents") or []:
                    key = item["Key"]
                    rel = key[len(self.config.key_prefix) + 1 :] if self.config.key_prefix else key
                    yield ObjectInfo(
                        key=rel,
                        size=int(item.get("Size") or 0),
                        etag=(item.get("ETag") or "").strip('"') or None,
                        last_modified=str(item.get("LastModified") or ""),
                    )
        except self._errors as exc:
            raise self._map(exc) from exc

    def presign_get(
        self,
        key: str,
        ttl_seconds: int | None = None,
        *,
        download_filename: str | None = None,
        inline: bool = False,
    ) -> str:
        full = self._full_key(key)
        ttl = clamp_presign_ttl(
            self.config.presign_ttl_seconds if ttl_seconds is None else ttl_seconds
        )
        params: dict[str, Any] = {"Bucket": self.config.s3.bucket, "Key": full}
        if not inline:
            filename = safe_download_filename(
                download_filename or full.rsplit("/", 1)[-1]
            )
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
            params["ResponseCacheControl"] = "no-store"
        try:
            return self._client.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=ttl
            )
        except self._errors as exc:
            raise self._map(exc) from exc

    def health(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.config.s3.bucket)
        except self._errors as exc:
            error = self._map(exc)
            if isinstance(error, ObjectNotFound):
                raise StorageConfigError(
                    f"S3 bucket is missing or inaccessible: {self.config.s3.bucket}"
                ) from exc
            raise error from exc

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


# ------------------------------------------------------------- azure adapter


class AzureBlobObjectStore(_BaseStore):
    """Azure Blob Storage (azure-storage-blob, imported lazily)."""

    backend = BACKEND_AZURE_BLOB

    def __init__(self, config: StorageConfig) -> None:
        super().__init__(config)
        if config.azure_blob is None:
            raise StorageConfigError("azure_blob backend requires an AzureBlobConfig")
        try:
            from azure.core.exceptions import (
                ClientAuthenticationError,
                ResourceNotFoundError,
                ServiceRequestError,
            )
            from azure.storage.blob import BlobServiceClient, ContentSettings
        except ImportError as exc:  # pragma: no cover - optional-dep path
            raise StorageConfigError(
                "azure_blob backend requires azure-storage-blob; "
                "install the 'object-storage-azure' extra"
            ) from exc
        self._ContentSettings = ContentSettings
        self._not_found = ResourceNotFoundError
        self._auth = ClientAuthenticationError
        self._request_error = ServiceRequestError
        cfg = config.azure_blob
        credential: object | None = cfg.credential
        if cfg.managed_identity:
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:  # pragma: no cover - optional-dep path
                raise StorageConfigError(
                    "azure managed identity requires azure-identity; "
                    "install the 'object-storage-azure' extra"
                ) from exc
            credential = DefaultAzureCredential()
        client_options = {
            "connection_timeout": cfg.connect_timeout,
            "read_timeout": cfg.read_timeout,
            "retry_total": cfg.max_attempts,
        }
        if cfg.connection_string:
            self._service = BlobServiceClient.from_connection_string(
                cfg.connection_string, **client_options
            )
        else:
            self._service = BlobServiceClient(
                account_url=cfg.account_url,
                credential=credential,
                **client_options,
            )
        self._container = self._service.get_container_client(cfg.container)

    @property
    def _timeout(self) -> float | None:
        cfg = self.config.azure_blob
        return cfg.read_timeout if cfg else None

    def _map(self, exc: Exception) -> StorageError:
        if isinstance(exc, self._not_found):
            return ObjectNotFound(str(exc))
        if isinstance(exc, self._auth):
            return StorageAuthError(str(exc))
        if isinstance(exc, self._request_error):
            return StorageUnavailable(str(exc))
        status = getattr(exc, "status_code", None)
        return error_for(self.backend, status, str(exc))

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectInfo:
        self._check_size(data)
        full = self._full_key(key)
        digest = _digest(data)
        meta = {str(k): str(v) for k, v in (metadata or {}).items()}
        meta.setdefault("sha256", digest)
        meta.setdefault("contract", CONTRACT_VERSION)
        timeout = self.config.azure_blob.read_timeout if self.config.azure_blob else None
        try:
            self._container.upload_blob(
                name=full,
                data=data,
                overwrite=True,
                content_settings=self._ContentSettings(
                    content_type=content_type or "application/octet-stream"
                ),
                metadata=meta,
                timeout=timeout,
            )
        except Exception as exc:  # pragma: no cover - SDK error surface
            raise self._map(exc) from exc
        return ObjectInfo(
            key=normalize_key(key),
            size=len(data),
            sha256=digest,
            content_type=content_type,
            metadata=meta,
        )

    def get(self, key: str) -> bytes | None:
        full = self._full_key(key)
        try:
            return self._container.download_blob(full, timeout=self._timeout).readall()
        except Exception as exc:  # pragma: no cover - SDK error surface
            if isinstance(exc, self._not_found):
                return None
            raise self._map(exc) from exc

    def stream(self, key: str, chunk_size: int = DEFAULT_PART_SIZE) -> Iterator[bytes]:
        full = self._full_key(key)
        size = max(1, chunk_size)
        try:
            downloader = self._container.download_blob(full, timeout=self._timeout)
            # Azure's StorageStreamDownloader.chunks() takes no size argument;
            # re-chunk its blocks so the contract still honours chunk_size.
            buffer = bytearray()
            for block in downloader.chunks():
                buffer.extend(block)
                while len(buffer) >= size:
                    yield bytes(buffer[:size])
                    del buffer[:size]
            if buffer:
                yield bytes(buffer)
        except Exception as exc:  # pragma: no cover - SDK error surface
            raise self._map(exc) from exc

    def exists(self, key: str) -> bool:
        return self._container.get_blob_client(self._full_key(key)).exists()

    def stat(self, key: str) -> ObjectInfo | None:
        full = self._full_key(key)
        try:
            props = self._container.get_blob_client(full).get_blob_properties(
                timeout=self._timeout
            )
        except Exception as exc:  # pragma: no cover - SDK error surface
            if isinstance(exc, self._not_found):
                return None
            raise self._map(exc) from exc
        meta = {str(k): str(v) for k, v in (props.metadata or {}).items()}
        return ObjectInfo(
            key=normalize_key(key),
            size=int(props.size or 0),
            sha256=meta.get("sha256"),
            content_type=props.content_settings.content_type if props.content_settings else None,
            etag=props.etag,
            last_modified=str(props.last_modified or ""),
            metadata=meta,
        )

    def delete(self, key: str) -> bool:
        full = self._full_key(key)
        try:
            self._container.delete_blob(full, timeout=self._timeout)
        except Exception as exc:  # pragma: no cover - SDK error surface
            if isinstance(exc, self._not_found):
                return False
            raise self._map(exc) from exc
        return True

    def list(self, prefix: str = "") -> Iterator[ObjectInfo]:
        base = self._full_key(prefix.rstrip("/")) if prefix else self.config.key_prefix
        try:
            for item in self._container.list_blobs(
                name_starts_with=base or None, timeout=self._timeout
            ):
                rel = item.name[len(self.config.key_prefix) + 1 :] if self.config.key_prefix else item.name
                yield ObjectInfo(
                    key=rel,
                    size=int(item.size or 0),
                    etag=item.etag,
                    last_modified=str(item.last_modified or ""),
                )
        except Exception as exc:  # pragma: no cover - SDK error surface
            raise self._map(exc) from exc

    def presign_get(
        self,
        key: str,
        ttl_seconds: int | None = None,
        *,
        download_filename: str | None = None,
        inline: bool = False,
    ) -> str:
        cfg = self.config.azure_blob
        assert cfg is not None
        account_name, account_key = _azure_presign_parts(cfg)
        if not account_name or not account_key:
            raise UnsupportedOperation(
                "azure presign needs an account name and key "
                "(AZURE_STORAGE_ACCOUNT_NAME + account key, or a connection string)"
            )
        if not _is_azure_account_key(account_key):
            raise UnsupportedOperation(
                "azure presign needs an account key; a SAS/token credential cannot sign"
            )
        try:
            from azure.storage.blob import BlobSasPermissions, generate_blob_sas
        except ImportError as exc:  # pragma: no cover - optional-dep path
            raise StorageConfigError("azure presign requires azure-storage-blob") from exc
        ttl = clamp_presign_ttl(
            self.config.presign_ttl_seconds if ttl_seconds is None else ttl_seconds
        )
        sas_kwargs: dict[str, Any] = {}
        if not inline:
            filename = safe_download_filename(
                download_filename or self._full_key(key).rsplit("/", 1)[-1]
            )
            sas_kwargs["content_disposition"] = f'attachment; filename="{filename}"'
            sas_kwargs["cache_control"] = "no-store"
        sas = generate_blob_sas(
            account_name=account_name,
            container_name=cfg.container,
            blob_name=self._full_key(key),
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(UTC) + timedelta(seconds=ttl),
            **sas_kwargs,
        )
        return f"{self._service.url.rstrip('/')}/{cfg.container}/{self._full_key(key)}?{sas}"

    def health(self) -> None:
        try:
            self._container.get_container_properties(timeout=self._timeout)
        except Exception as exc:  # pragma: no cover - SDK error surface
            error = self._map(exc)
            if isinstance(error, ObjectNotFound):
                raise StorageConfigError(
                    f"Azure container is missing or inaccessible: {self.config.azure_blob.container}"
                ) from exc
            raise error from exc


# --------------------------------------------------------------------- factory


def build_store(config: StorageConfig) -> ObjectStore:
    """Build the adapter for a validated config."""

    config.validate()
    if config.backend == BACKEND_LOCAL:
        return LocalObjectStore(config)
    if config.backend == BACKEND_S3:
        return S3ObjectStore(config)
    if config.backend == BACKEND_AZURE_BLOB:
        return AzureBlobObjectStore(config)
    raise StorageConfigError(f"unknown object-storage backend: {config.backend!r}")


def open_store(
    env: Mapping[str, str] | None = None,
    prefix: str = "APP",
    *,
    entrypoint: str = ENTRYPOINT_SERVER,
    default_local_root: str | Path | None = None,
    probe: bool = True,
) -> ObjectStore:
    """One-call host bootstrap: config from env, build, optional health probe."""

    config = StorageConfig.from_env(
        os.environ if env is None else env,
        prefix,
        entrypoint=entrypoint,
        default_local_root=default_local_root,
    )
    store = build_store(config)
    if probe:
        try:
            store.health()
        except StorageError:
            store.close()
            raise
    return store


# -------------------------------------------------------------- materialize


@contextmanager
def materialize(
    store: ObjectStore,
    key: str,
    *,
    directory: str | Path | None = None,
    suffix: str | None = None,
) -> Iterator[Path]:
    """Yield a private temporary file containing one object.

    Libraries that demand a real path (Celery OCR, pydicom, PDF/Office
    conversion) use this instead of a durable host path: the file is
    ``0600``, lives only for the ``with`` block, and is removed on every
    exit path. Prefer ``stream``/``get`` when the library accepts bytes.
    """

    normalized = normalize_key(key)
    ext = safe_extension(suffix) if suffix else filename_extension(normalized.rsplit("/", 1)[-1])
    fd, name = tempfile.mkstemp(prefix="nx-object-", suffix=ext, dir=directory)
    path = Path(name)
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "wb") as handle:
            for chunk in store.stream(normalized):
                handle.write(chunk)
        yield path
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------- conformance


def run_conformance(store: ObjectStore) -> list[str]:
    """Run the family acceptance wall against one configured store.

    Returns a list of human-readable failures (empty = pass). Hosts run
    this against ``local`` in unit tests and against a real bucket in the
    cloud smoke test; the same function is the contract's proof.
    """

    failures: list[str] = []
    key = "conformance/probe.txt"
    payload = b"neuronection-object-storage/1\n"

    def check(name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{name}: {detail}" if detail else name)

    try:
        info = store.put(key, payload, content_type="text/plain")
        digest = _digest(payload)
        check("put-sha", info.sha256 == digest, f"{info.sha256} != {digest}")
        check("put-size", info.size == len(payload), f"{info.size}")
        check("put-key", info.key == key, info.key)
        check("exists-after-put", store.exists(key))
        check("get-roundtrip", store.get(key) == payload)
        stat = store.stat(key)
        check("stat-present", stat is not None)
        if stat is not None:
            check("stat-size", stat.size == len(payload), f"{stat.size}")
            check("stat-sha", stat.sha256 in (None, digest), str(stat.sha256))
        streamed = b"".join(store.stream(key, chunk_size=4))
        check("stream-roundtrip", streamed == payload, f"{len(streamed)} bytes")
        with materialize(store, key) as path:
            check("materialize-roundtrip", path.read_bytes() == payload)
            check("materialize-mode", (path.stat().st_mode & 0o777) == 0o600)
        check("materialize-cleaned", not path.exists())
        check("list-contains", any(item.key == key for item in store.list("conformance")))
        try:
            url = store.presign_get(key, ttl_seconds=0)
            check("presign-nonempty", bool(url), "empty URL")
        except UnsupportedOperation:
            pass
        check("delete", store.delete(key))
        check("get-after-delete", store.get(key) is None)
        check("stat-after-delete", store.stat(key) is None)
        check("delete-missing", store.delete(key) is False)
    except StorageError as exc:  # pragma: no cover - real backend failure
        failures.append(f"backend-error: {type(exc).__name__}: {exc}")
    finally:
        try:
            store.delete(key)
        except StorageError:
            pass
    return failures
