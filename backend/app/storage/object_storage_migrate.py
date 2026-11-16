# VENDORED FILE — family canonical (ADR-0031 / ADR-0057).
# Synced byte-identically from the family object-storage migration module and
# checked by the family wiring verifier. NEVER edit the vendored copy in-repo —
# change the family canonical, then re-run the sync.
"""Resumable local → object-storage migration (ADR-0057 / plan 55 P4).

The family contract answers *how bytes move*; this module answers *how a host
copies an existing local tree into the configured store without losing track
of what it already copied*. It is deliberately small and stdlib-only: hosts
own the domain mapping (which local file becomes which key), the database
backfill and the CLI surface.

Shape:

* :class:`MigrationEntry` — one source file, its target key, size, sha256 and
  a status (``pending`` → ``copied`` → ``verified``, or a terminal
  ``failed``/``missing``/``unreadable``/``collision``/``skipped``).
* :class:`Manifest` — the resumable ledger. JSON on disk, written atomically
  (temp file + ``os.replace``) after **every** entry so an interrupted run
  resumes where it stopped.
* :func:`plan_entries` — walk a source root, ask the host for a key per
  relative POSIX path, hash each file and report missing/unreadable files and
  key collisions. It never writes.
* :func:`apply` — copy planned entries into any duck-typed ``ObjectStore``
  (``put``/``get``/``stat``/``list``), verify size + sha256, and record the
  status. Idempotent: a copied and verified entry is skipped, a vanished
  target is recopied.
* :func:`verify` — re-read every copied object and compare size and sha256.
* :func:`non_empty_target_prefix` — the target-prefix guard. A foreign object
  under the deployment prefix means the operator pointed the tool at a live
  bucket; ``--force`` is required to continue.

No database, SQLAlchemy, host ``Settings`` or cloud SDK import lives here, and
no secret is read or logged. The store is duck-typed: this module only calls
the protocol methods, so it has no import dependency on
``object_storage.py`` and cannot drift from it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

MANIFEST_VERSION = "object-storage-migration/1"
DEFAULT_MANIFEST_NAME = "object-storage-manifest.json"

STATUS_PENDING = "pending"
STATUS_COPIED = "copied"
STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
STATUS_MISSING = "missing"
STATUS_UNREADABLE = "unreadable"
STATUS_COLLISION = "collision"
STATUS_SKIPPED = "skipped"
STATUSES = (
    STATUS_PENDING,
    STATUS_COPIED,
    STATUS_VERIFIED,
    STATUS_FAILED,
    STATUS_MISSING,
    STATUS_UNREADABLE,
    STATUS_COLLISION,
    STATUS_SKIPPED,
)
#: Statuses that mean "the target object is present and was checked".
TRANSFERRED_STATUSES = (STATUS_COPIED, STATUS_VERIFIED)
#: Statuses that carry a usable key but are not themselves a copy failure.
_NON_COPY_STATUSES = (STATUS_SKIPPED, STATUS_COLLISION)

CHUNK_SIZE = 64 * 1024


class ManifestError(ValueError):
    """The manifest is missing required fields, corrupt or from a newer tool."""


class TargetNotEmpty(RuntimeError):
    """The deployment prefix holds objects the manifest does not explain."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        shown = ", ".join(keys[:5])
        more = f" (+{len(keys) - 5} more)" if len(keys) > 5 else ""
        super().__init__(
            f"target prefix already has {len(keys)} foreign object(s): {shown}{more}. "
            "Refusing without --force."
        )


class ManifestTargetMismatch(RuntimeError):
    """The manifest was written for a different store/host/source root."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class VerifyRequired(RuntimeError):
    """A backfill was requested before every copyable entry verified."""

    def __init__(self, entries: list[MigrationEntry]) -> None:
        self.entries = entries
        shown = ", ".join(entry.source for entry in entries[:5])
        more = f" (+{len(entries) - 5} more)" if len(entries) > 5 else ""
        super().__init__(
            f"backfill requires a clean verify: {len(entries)} entr"
            f"{'y' if len(entries) == 1 else 'ies'} not verified ({shown}{more}). "
            "Run --verify first."
        )


@dataclass(frozen=True)
class TargetDescriptor:
    """The redacted identity of a migration target (never credentials).

    ``backend``/``bucket``/``prefix``/``endpoint_host`` are the deployment
    identity the manifest is bound to. ``local_root`` is populated only for
    the local backend, where there is no bucket to distinguish two roots.
    """

    backend: str
    bucket: str = ""
    prefix: str = ""
    endpoint_host: str = ""
    local_root: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "backend": self.backend,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "endpoint_host": self.endpoint_host,
            "local_root": self.local_root,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> TargetDescriptor | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ManifestError("manifest 'target' must be an object")
        values: dict[str, str] = {}
        for name in ("backend", "bucket", "prefix", "endpoint_host", "local_root"):
            value = raw.get(name, "")
            if not isinstance(value, str):
                raise ManifestError(f"manifest target '{name}' must be a string")
            values[name] = value
        if not values["backend"]:
            raise ManifestError("manifest target needs a non-empty 'backend'")
        return cls(**values)

    def label(self) -> str:
        target = self.bucket or self.local_root or "-"
        endpoint = f" endpoint={self.endpoint_host}" if self.endpoint_host else ""
        return f"{self.backend}/{target} prefix={self.prefix or '-'}{endpoint}"


def _norm_prefix(value: str | None) -> str:
    return (value or "").strip("/")


def _norm_root(value: Path | str | None) -> str:
    if not value:
        return ""
    return str(Path(value).expanduser().resolve())


def _store_config(store: MigrationStore) -> Any:
    return getattr(store, "config", None)


def target_descriptor(store: MigrationStore) -> TargetDescriptor:
    """The redacted target identity, suitable for a manifest (no secrets)."""
    config = _store_config(store)
    backend = str(
        getattr(store, "backend", None) or getattr(config, "backend", "") or ""
    )
    bucket = ""
    endpoint_host = ""
    if config is not None:
        s3 = getattr(config, "s3", None)
        azure = getattr(config, "azure_blob", None)
        if backend == "s3" and s3 is not None:
            bucket = str(getattr(s3, "bucket", "") or "")
            endpoint_host = urlparse(str(getattr(s3, "endpoint_url", "") or "")).hostname or ""
        elif backend == "azure_blob" and azure is not None:
            bucket = str(getattr(azure, "container", "") or "")
            endpoint_host = (
                urlparse(str(getattr(azure, "account_url", "") or "")).hostname or ""
            )
    prefix = _norm_prefix(getattr(config, "key_prefix", "") if config is not None else "")
    root = getattr(store, "root", None)
    local_root = _norm_root(root) if backend == "local" and root else ""
    return TargetDescriptor(
        backend=backend,
        bucket=bucket,
        prefix=prefix,
        endpoint_host=endpoint_host,
        local_root=local_root,
    )


def bind_target(manifest: Manifest, store: MigrationStore) -> Manifest:
    """Bind a fresh manifest to the target it is about to write."""
    manifest.target = target_descriptor(store)
    return manifest


def check_binding(
    manifest: Manifest,
    store: MigrationStore,
    *,
    host: str | None = None,
    source_root: Path | str | None = None,
) -> None:
    """Refuse to act on a manifest written for another target/source/host."""
    if manifest.target is None:
        raise ManifestTargetMismatch(
            "manifest has no target binding. Re-run with --rebind against the "
            "intended target; nothing was changed."
        )
    current = target_descriptor(store)
    expected = manifest.target
    mismatches: list[str] = []
    for name in ("backend", "bucket", "prefix", "endpoint_host", "local_root"):
        if getattr(expected, name) != getattr(current, name):
            mismatches.append(
                f"{name}: manifest={getattr(expected, name) or '-'} "
                f"current={getattr(current, name) or '-'}"
            )
    if host is not None and host != manifest.host:
        mismatches.append(f"host: manifest={manifest.host} current={host}")
    if source_root is not None and _norm_root(source_root) != _norm_root(manifest.source_root):
        mismatches.append(
            "source_root: manifest="
            f"{_norm_root(manifest.source_root) or '-'} current={_norm_root(source_root) or '-'}"
        )
    if mismatches:
        raise ManifestTargetMismatch(
            "manifest target does not match this run ("
            + "; ".join(mismatches)
            + "). Pass --rebind to adopt the current target; transferred entries "
            "will be reset to pending and must be copied and verified again."
        )


def rebind_manifest(
    manifest: Manifest,
    store: MigrationStore,
    *,
    host: str | None = None,
    source_root: Path | str | None = None,
) -> Manifest:
    """Adopt the current target and reset transferred entries to pending."""
    manifest.target = target_descriptor(store)
    if host is not None:
        manifest.host = host
    if source_root is not None:
        manifest.source_root = str(source_root)
    for entry in manifest.entries:
        if entry.status in TRANSFERRED_STATUSES:
            entry.status = STATUS_PENDING
            entry.error = ""
    return manifest


def require_verified(manifest: Manifest) -> None:
    """Raise unless every non-skipped entry is verified."""
    blocking = [
        entry for entry in manifest.entries if entry.status not in (STATUS_SKIPPED, STATUS_VERIFIED)
    ]
    if blocking:
        raise VerifyRequired(blocking)


def _is_storage_error(exc: BaseException) -> bool:
    """True for the contract taxonomy or an OS error, never for a bug."""
    if isinstance(exc, OSError):
        return True
    names = {"StorageError", "StorageConfigError", "StorageAuthError", "StorageUnavailable",
             "ObjectNotFound", "ObjectTooLarge", "InvalidObjectKey", "UnsupportedOperation"}
    return any(cls.__name__ in names for cls in type(exc).__mro__)


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_CREDENTIAL_HINT_RE = re.compile(
    r"(?i)(access[_-]?key|secret|token|signature|sig|password|credential)=([^&\s]+)"
)
_PATH_RE = re.compile(r"(?<![\w])/(?:[^\s:]+)")


def _safe_error(exc: BaseException) -> str:
    """A manifest-safe error: class name plus a redacted, bounded message."""
    text = _URL_RE.sub("<redacted-url>", str(exc))
    text = _CREDENTIAL_HINT_RE.sub(r"\1=<redacted>", text)
    text = _PATH_RE.sub("<redacted-path>", text)
    text = text.replace("\n", " ").strip()
    return f"{type(exc).__name__}: {text[:300]}" if text else type(exc).__name__


class MigrationStore(Protocol):
    """The subset of ``ObjectStore`` this module needs (duck-typed)."""

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Any: ...

    def get(self, key: str) -> bytes | None: ...

    def stat(self, key: str) -> Any | None: ...

    def list(self, prefix: str = "") -> Iterable[Any]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _relative_source(root: Path, source: str) -> Path:
    """Resolve a manifest source under ``root``, refusing traversal."""
    candidate = Path(source)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError(f"unsafe manifest source: {source!r}")
    return root / candidate


def sha256_file(path: Path) -> str:
    """Streaming sha256 of a file (never reads it whole into memory)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------ entries


@dataclass
class MigrationEntry:
    """One planned copy: a local source and the object key it becomes."""

    source: str
    key: str
    size: int = 0
    sha256: str = ""
    status: str = STATUS_PENDING
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "key": self.key,
            "size": int(self.size),
            "sha256": self.sha256,
            "status": self.status,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> MigrationEntry:
        if not isinstance(raw, dict):
            raise ManifestError("manifest entry must be an object")
        source = raw.get("source")
        key = raw.get("key")
        if not isinstance(source, str) or not isinstance(key, str):
            raise ManifestError("manifest entry needs string 'source' and 'key'")
        status = raw.get("status", STATUS_PENDING)
        if status not in STATUSES:
            raise ManifestError(f"unknown manifest entry status: {status!r}")
        try:
            size = int(raw.get("size") or 0)
        except (TypeError, ValueError) as exc:
            raise ManifestError("manifest entry 'size' must be an integer") from exc
        sha = raw.get("sha256") or ""
        error = raw.get("error") or ""
        if not isinstance(sha, str) or not isinstance(error, str):
            raise ManifestError("manifest entry 'sha256'/'error' must be strings")
        return cls(
            source=source,
            key=key,
            size=size,
            sha256=sha,
            status=status,
            error=error,
        )


@dataclass
class Manifest:
    """The resumable migration ledger (JSON on disk)."""

    host: str
    entries: list[MigrationEntry] = field(default_factory=list)
    version: str = MANIFEST_VERSION
    source_root: str = ""
    prefix: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    target: TargetDescriptor | None = None
    # Not serialized: where this manifest was loaded from / saves to.
    path: str | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "host": self.host,
            "source_root": self.source_root,
            "prefix": self.prefix,
            "target": self.target.to_dict() if self.target is not None else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, raw: Any, *, path: str | None = None) -> Manifest:
        if not isinstance(raw, dict):
            raise ManifestError("manifest root must be a JSON object")
        if raw.get("version") != MANIFEST_VERSION:
            raise ManifestError(
                f"unsupported manifest version {raw.get('version')!r}; "
                f"expected {MANIFEST_VERSION!r}"
            )
        host = raw.get("host")
        if not isinstance(host, str) or not host:
            raise ManifestError("manifest needs a non-empty 'host'")
        entries_raw = raw.get("entries")
        if not isinstance(entries_raw, list):
            raise ManifestError("manifest 'entries' must be a list")
        entries = [MigrationEntry.from_dict(item) for item in entries_raw]
        source_root = raw.get("source_root") or ""
        prefix = raw.get("prefix") or ""
        target = TargetDescriptor.from_dict(raw.get("target"))
        created_at = raw.get("created_at") or _now()
        updated_at = raw.get("updated_at") or created_at
        if not all(isinstance(value, str) for value in (source_root, prefix, created_at, updated_at)):
            raise ManifestError("manifest timestamps/roots must be strings")
        return cls(
            host=host,
            entries=entries,
            version=MANIFEST_VERSION,
            source_root=source_root,
            prefix=prefix,
            created_at=created_at,
            updated_at=updated_at,
            target=target,
            path=path,
        )

    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys(STATUSES, 0)
        for entry in self.entries:
            counts[entry.status] = counts.get(entry.status, 0) + 1
        counts["total"] = len(self.entries)
        return counts

    def keys(self, statuses: Iterable[str] = TRANSFERRED_STATUSES) -> set[str]:
        wanted = set(statuses)
        return {entry.key for entry in self.entries if entry.key and entry.status in wanted}

    def owned_keys(self) -> set[str]:
        """Every key this manifest intends to own, whatever its status.

        A failed or pending entry may still have stale bytes at its key from
        earlier run; those are ours to overwrite, not foreign bucket data.
        """
        return {
            entry.key
            for entry in self.entries
            if entry.key and entry.status not in _NON_COPY_STATUSES
        }


def new_manifest(
    host: str,
    source_root: Path | str,
    *,
    prefix: str = "",
    path: Path | str | None = None,
    target: TargetDescriptor | None = None,
) -> Manifest:
    """A fresh, empty manifest for one host/root (bind it to a store next)."""
    return Manifest(
        host=host,
        source_root=str(source_root),
        prefix=prefix.strip("/"),
        path=str(path) if path is not None else None,
        target=target,
    )


# -------------------------------------------------------------- manifest I/O


def load_manifest(path: Path | str) -> Manifest:
    """Load and validate a manifest; raise :class:`ManifestError` when corrupt."""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {target}: {exc}") from exc
    return Manifest.from_dict(payload, path=str(target))


def save_manifest(path: Path | str, manifest: Manifest) -> Manifest:
    """Atomically write the manifest (temp file + fsync + ``os.replace``)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest.updated_at = _now()
    payload = json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)
    try:
        dir_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        dir_fd = None
    if dir_fd is not None:
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    manifest.path = str(target)
    return manifest


# ----------------------------------------------------------------- planning


def merge_entries(
    manifest: Manifest,
    entries: list[MigrationEntry],
) -> Manifest:
    """Re-plan in place, keeping already transferred entries transferred.

    A migration is normally planned once and then resumed. Re-planning (to
    pick up files that arrived during a maintenance window) must not turn a
    copied object into a "foreign" object: an entry with the same key, size
    and sha256 as a previously copied/verified entry keeps its transferred
    status. Changed bytes stay pending so ``apply`` copies the new version.
    """
    previous = {entry.key: entry for entry in manifest.entries if entry.key}
    for entry in entries:
        old = previous.get(entry.key)
        if (
            old is not None
            and old.status in TRANSFERRED_STATUSES
            and old.size == entry.size
            and old.sha256 == entry.sha256
        ):
            entry.status = old.status
            entry.error = ""
    manifest.entries = entries
    return manifest


def plan_entries(
    root: Path | str,
    key_for: Callable[[str], str | None],
) -> list[MigrationEntry]:
    """Enumerate ``root`` and build one entry per file.

    ``key_for`` receives the relative POSIX path and returns a validated
    object key, or ``None`` to report the file as skipped (no stable key).
    Hidden files are ignored (the local health probe's ``.health`` and atomic
    write temporaries). Two sources mapping to one key are both marked
    ``collision``: the operator must fix the mapping, never guess.
    """
    source_root = Path(root)
    if not source_root.is_dir():
        raise FileNotFoundError(f"source root is not a directory: {source_root}")
    resolved_root = source_root.resolve()
    entries: list[MigrationEntry] = []
    by_key: dict[str, MigrationEntry] = {}
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        relative = path.relative_to(source_root).as_posix()
        if path.is_symlink():
            entries.append(
                MigrationEntry(
                    source=relative,
                    key="",
                    status=STATUS_UNREADABLE,
                    error="symlink is not followed by the migration",
                )
            )
            continue
        try:
            resolved = path.resolve()
        except OSError as exc:
            entries.append(
                MigrationEntry(
                    source=relative,
                    key="",
                    status=STATUS_UNREADABLE,
                    error=f"cannot resolve source: {exc}",
                )
            )
            continue
        if resolved_root not in resolved.parents:
            entries.append(
                MigrationEntry(
                    source=relative,
                    key="",
                    status=STATUS_UNREADABLE,
                    error="resolved path escapes the source root",
                )
            )
            continue
        try:
            key = key_for(relative)
        except (ValueError, OSError) as exc:
            entries.append(
                MigrationEntry(
                    source=relative,
                    key="",
                    status=STATUS_UNREADABLE,
                    error=f"key mapping failed: {exc}",
                )
            )
            continue
        if not key:
            entry = MigrationEntry(
                source=relative,
                key="",
                status=STATUS_SKIPPED,
                error="no stable object key for this file",
            )
            try:
                entry.size = path.stat().st_size
            except OSError:
                entry.status = STATUS_UNREADABLE
                entry.error = "source file disappeared during planning"
            entries.append(entry)
            continue
        entry = MigrationEntry(source=relative, key=key, size=0, sha256="")
        try:
            entry.size = path.stat().st_size
            entry.sha256 = sha256_file(path)
        except FileNotFoundError:
            entry.status = STATUS_MISSING
            entry.error = "source file disappeared during planning"
        except OSError as exc:
            entry.status = STATUS_UNREADABLE
            entry.error = f"cannot read source: {exc}"
        previous = by_key.get(key)
        if previous is not None:
            entry.status = STATUS_COLLISION
            entry.error = f"duplicate key also produced by {previous.source}"
            previous.status = STATUS_COLLISION
            previous.error = f"duplicate key also produced by {entry.source}"
        else:
            by_key[key] = entry
        entries.append(entry)
    return entries


# ------------------------------------------------------------------ target


def non_empty_target_prefix(
    store: MigrationStore,
    prefix: str,
    exclude: Iterable[str] = (),
) -> list[str]:
    """Keys under ``prefix`` that the manifest does not already account for."""
    cleaned = (prefix or "").strip("/")
    excluded = set(exclude)
    foreign: list[str] = []
    for info in store.list(cleaned):
        key = getattr(info, "key", None)
        if not isinstance(key, str):
            continue
        if cleaned and not (key == cleaned or key.startswith(f"{cleaned}/")):
            continue
        if key not in excluded:
            foreign.append(key)
    return foreign


def _target_ok(store: MigrationStore, entry: MigrationEntry) -> bool:
    info = store.stat(entry.key)
    if info is None:
        return False
    size = getattr(info, "size", None)
    if size is not None and int(size) != entry.size:
        return False
    remote_sha = getattr(info, "sha256", None)
    if remote_sha and entry.sha256 and str(remote_sha) != entry.sha256:
        return False
    return True


# --------------------------------------------------------------------- apply


def _remaining(manifest: Manifest) -> int:
    """Entries that still need an ``apply`` (copied/verified/skipped are done)."""
    return sum(
        1
        for entry in manifest.entries
        if entry.status not in (STATUS_COPIED, STATUS_VERIFIED, STATUS_SKIPPED)
    )


def apply(
    store: MigrationStore,
    manifest: Manifest,
    *,
    dry_run: bool = False,
    force: bool = False,
    content_type_for: Callable[[MigrationEntry], str | None] | None = None,
    save: bool = True,
    host: str | None = None,
    source_root: Path | str | None = None,
) -> dict[str, int]:
    """Copy every pending entry into ``store``; idempotent and resumable.

    The manifest must be bound to this target (``check_binding``). A dry run
    never writes an object or the manifest and never refuses a non-empty
    target — it reports ``foreign`` and warns; only a real apply requires
    ``--force``. Every copy records its status and the manifest is saved
    atomically after each entry.
    """
    check_binding(manifest, store, host=host, source_root=source_root)
    prefix = manifest.prefix
    foreign = non_empty_target_prefix(store, prefix, manifest.owned_keys())
    if not dry_run and not force and foreign:
        raise TargetNotEmpty(foreign)

    root = Path(manifest.source_root)
    resolved_root = root.resolve()
    config = _store_config(store)
    limit = int(getattr(config, "max_object_bytes", 0) or 0)
    planned = 0
    skipped = 0
    failed = 0
    copied = 0

    if dry_run:
        for entry in manifest.entries:
            if not entry.key or entry.status in _NON_COPY_STATUSES:
                skipped += 1
                continue
            if entry.status in (STATUS_MISSING, STATUS_UNREADABLE):
                skipped += 1
                continue
            if _target_ok(store, entry):
                skipped += 1
            else:
                planned += 1
        return {
            "dry_run": 1,
            "planned": planned,
            "copied": 0,
            "skipped": skipped,
            "failed": 0,
            "remaining": planned,
            "pending": manifest.counts()[STATUS_PENDING],
            "foreign": len(foreign),
        }

    for entry in manifest.entries:
        if not entry.key or entry.status in _NON_COPY_STATUSES:
            skipped += 1
            continue
        if entry.status in TRANSFERRED_STATUSES and _target_ok(store, entry):
            skipped += 1
            continue
        if limit and entry.size > limit:
            entry.status = STATUS_FAILED
            entry.error = (
                f"source is {entry.size} bytes; the store cap is {limit} bytes "
                "(raise the object-storage max-MB setting)"
            )
            failed += 1
            if save and manifest.path:
                save_manifest(manifest.path, manifest)
            continue
        try:
            source = _relative_source(resolved_root, entry.source)
        except ValueError as exc:
            entry.status = STATUS_UNREADABLE
            entry.error = _safe_error(exc)
            failed += 1
            if save and manifest.path:
                save_manifest(manifest.path, manifest)
            continue
        if source.is_symlink():
            entry.status = STATUS_UNREADABLE
            entry.error = "symlink is not followed by the migration"
            failed += 1
            if save and manifest.path:
                save_manifest(manifest.path, manifest)
            continue
        try:
            if resolved_root not in source.resolve().parents:
                entry.status = STATUS_UNREADABLE
                entry.error = "resolved path escapes the source root"
                failed += 1
                if save and manifest.path:
                    save_manifest(manifest.path, manifest)
                continue
            data = source.read_bytes()
        except FileNotFoundError:
            entry.status = STATUS_MISSING
            entry.error = "source file is missing"
            failed += 1
            if save and manifest.path:
                save_manifest(manifest.path, manifest)
            continue
        except OSError as exc:
            entry.status = STATUS_UNREADABLE
            entry.error = _safe_error(exc)
            failed += 1
            if save and manifest.path:
                save_manifest(manifest.path, manifest)
            continue
        digest = hashlib.sha256(data).hexdigest()
        if entry.sha256 and digest != entry.sha256:
            entry.status = STATUS_FAILED
            entry.error = "source changed since planning (sha256 mismatch); re-plan"
            failed += 1
            if save and manifest.path:
                save_manifest(manifest.path, manifest)
            continue
        entry.sha256 = digest
        entry.size = len(data)
        content_type = content_type_for(entry) if content_type_for is not None else None
        try:
            written = store.put(entry.key, data, content_type=content_type)
            remote = store.stat(entry.key)
        except Exception as exc:  # noqa: BLE001 - classify below
            if not _is_storage_error(exc):
                raise
            entry.status = STATUS_FAILED
            entry.error = _safe_error(exc)
            failed += 1
            if save and manifest.path:
                save_manifest(manifest.path, manifest)
            continue
        written_sha = getattr(written, "sha256", None)
        remote_sha = getattr(remote, "sha256", None) if remote is not None else None
        remote_size = getattr(remote, "size", None) if remote is not None else None
        mismatch = (
            remote is None
            or (remote_size is not None and int(remote_size) != len(data))
            or (remote_sha is not None and str(remote_sha) != digest)
            or (written_sha is not None and str(written_sha) != digest)
        )
        if mismatch:
            entry.status = STATUS_FAILED
            entry.error = "target verification failed after put (size/sha256)"
            failed += 1
        else:
            entry.status = STATUS_COPIED
            entry.error = ""
            copied += 1
        if save and manifest.path:
            save_manifest(manifest.path, manifest)
    if save and manifest.path:
        save_manifest(manifest.path, manifest)
    counts = manifest.counts()
    return {
        "dry_run": 0,
        "planned": 0,
        "copied": copied,
        "skipped": skipped,
        "failed": failed,
        "remaining": _remaining(manifest),
        "pending": counts[STATUS_PENDING],
        "foreign": len(foreign),
    }


# -------------------------------------------------------------------- verify


def verify(
    store: MigrationStore,
    manifest: Manifest,
    *,
    save: bool = True,
    host: str | None = None,
    source_root: Path | str | None = None,
    rehash_sources: bool = True,
) -> dict[str, Any]:
    """Re-read every copyable object and compare size and sha256.

    Metadata is not trusted: target bytes are fetched and hashed. When
    ``rehash_sources`` is on (the default) and a source still exists, it is
    hashed too, so a file changed after the copy cannot verify clean.
    ``blocking`` counts every non-skipped entry that is not ``verified`` —
    missing/unreadable/collision/failed entries always block. Hosts exit 1
    when ``blocking`` is non-zero.
    """
    check_binding(manifest, store, host=host, source_root=source_root)
    resolved_root = Path(manifest.source_root).resolve()
    checked = 0
    failed = 0
    skipped = 0
    rehashed = 0

    for entry in manifest.entries:
        if entry.status == STATUS_SKIPPED:
            skipped += 1
            continue
        if entry.status in (STATUS_MISSING, STATUS_UNREADABLE, STATUS_COLLISION):
            # A planning/backfill failure stays visible and blocking.
            if save and manifest.path:
                save_manifest(manifest.path, manifest)
            continue
        if not entry.key:
            entry.status = STATUS_FAILED
            entry.error = "entry has no object key"
            failed += 1
            if save and manifest.path:
                save_manifest(manifest.path, manifest)
            continue
        checked += 1
        if rehash_sources:
            try:
                source = _relative_source(resolved_root, entry.source)
            except ValueError as exc:
                entry.status = STATUS_FAILED
                entry.error = _safe_error(exc)
                failed += 1
                if save and manifest.path:
                    save_manifest(manifest.path, manifest)
                continue
            if source.is_symlink():
                entry.status = STATUS_FAILED
                entry.error = "source became a symlink; refusing to verify against it"
                failed += 1
                if save and manifest.path:
                    save_manifest(manifest.path, manifest)
                continue
            try:
                if source.is_file():
                    digest = sha256_file(source)
                    rehashed += 1
                    if entry.sha256 and digest != entry.sha256:
                        entry.status = STATUS_FAILED
                        entry.error = "source changed since planning (sha256 mismatch)"
                        failed += 1
                        if save and manifest.path:
                            save_manifest(manifest.path, manifest)
                        continue
                    entry.sha256 = digest
                    entry.size = source.stat().st_size
            except OSError:
                # A vanished source does not invalidate an already copied
                # object; target verification below is authoritative.
                pass
        try:
            data = store.get(entry.key)
        except Exception as exc:  # noqa: BLE001 - classify below
            if not _is_storage_error(exc):
                raise
            entry.status = STATUS_FAILED
            entry.error = _safe_error(exc)
            failed += 1
            if save and manifest.path:
                save_manifest(manifest.path, manifest)
            continue
        if data is None:
            entry.status = STATUS_FAILED
            entry.error = "object is missing from the target"
            failed += 1
        elif len(data) != entry.size:
            entry.status = STATUS_FAILED
            entry.error = f"size mismatch: target {len(data)}, manifest {entry.size}"
            failed += 1
        elif entry.sha256 and hashlib.sha256(data).hexdigest() != entry.sha256:
            entry.status = STATUS_FAILED
            entry.error = "sha256 mismatch between target and manifest"
            failed += 1
        else:
            if not entry.sha256:
                entry.sha256 = hashlib.sha256(data).hexdigest()
            entry.size = len(data)
            entry.status = STATUS_VERIFIED
            entry.error = ""
        if save and manifest.path:
            save_manifest(manifest.path, manifest)
    if save and manifest.path:
        save_manifest(manifest.path, manifest)

    blocking_entries = [
        entry for entry in manifest.entries if entry.status not in (STATUS_SKIPPED, STATUS_VERIFIED)
    ]
    return {
        "checked": checked,
        "verified": manifest.counts()[STATUS_VERIFIED],
        "failed": failed,
        "skipped": skipped,
        "blocking": len(blocking_entries),
        "blocking_entries": [entry.source for entry in blocking_entries],
        "rehashed": rehashed,
    }
