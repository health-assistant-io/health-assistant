"""The seed source service — registration, sync, upload, approval lifecycle
and the apply/adopt/retire doors (ADR 0056 Phases 3-4, program Phase 10A).

The API layer projects rows; everything stateful lives here:

* **Registration** — a source is one origin: ``kind``
  (``file|git|registry``; ``bundled`` is boot-only and cannot be created),
  an https ``uri`` (git/registry), a pinned ``ref_spec``/``subpath`` (git),
  the precedence ``priority`` inside the scope's band (system 0-199,
  tenant 200-999) and the scope-tenant coherence the table's CHECK
  enforces. ``auth_secret_ref`` names a secret SLOT; the slot resolves at
  fetch time and the value never persists.
* **Sync** — the transport fetches (startup never does), the pack parses
  through the same contract as every other lane, and the **manifest is
  pinned to the source row at registration**: a manifest whose
  ``scope``/``priority`` disagree with the source row is a typed refusal
  (``SCOPE_MISMATCH``/``PRIORITY_MISMATCH``) recorded in ``last_error`` —
  the planner/applier later trust the source row's values alone (program
  Phase 10 item 5). A digest already registered (``UNIQUE(source_id,
  digest)``) is "nothing new"; a new digest lands as a **pending** version
  with the fetch's pins (commit sha / etag). Failures keep every pin and
  land in ``last_check_at``/``last_error``.
* **Upload** — a ``file`` source's content door: a pack zip (guarded
  entries) or a single item-file JSON (wrapped into a manifest carrying the
  source's scope/priority) → parsed → digest → pending version. Uploads
  persist their pack content in the content-addressed cache so plan/apply
  never need the network.
* **Lifecycle** — ``pending → approved/rejected``, ``approved → revoked``,
  digest-bound. Approval flips the status only (the pointer advances on a
  successful apply — a failed apply never moves it). Revocation re-applies
  the previous approved version (the applier's retirement-restore flips
  what the revoked version had retired back to ``active``) or leaves the
  source pointer-less. The bundled lane refuses approve/revoke (trusted,
  auto-approved — D9).
* **Apply** — approval-checked (``VERSION_NOT_APPROVED`` otherwise), under
  the applier's advisory locks; ``plan`` is the read-only
  :meth:`SeedPackApplier.plan_only` run. The explicit re-apply door for a
  PARTIAL bundled apply is the same apply endpoint.
* **Adopt** — "update available" made explicit: clearing ``user_modified``
  on the current version's provenance rows and re-applying lets the pack
  content finally win (ADR 0056 assumption 5). Optionally scoped to one
  ``seed_key``.
* **Retire** — a source leaves: its items are retired (provenance
  ``retired``, concept rows ``status=retired``) only where no other active
  approved source still provides them; rows are never deleted. The action
  is recorded as run items of a dedicated apply-mode run (the durable
  history), and the source is disabled.
* **Audit** — every mutation writes an ``AuditEvent`` on the caller's
  transaction (fail-closed), with ``tenant_id`` from the source (system
  lane: NULL).

Every function takes the caller's session and never commits — the endpoint
(or the sync task) owns the transaction, matching the repo's transaction
law.
"""

from __future__ import annotations

import io
import logging
import uuid
import zipfile
from datetime import UTC, datetime
from functools import partial
from typing import Any

import anyio
from nx_auth.secrets import redact_secret
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.seed_pack.items import ParsedPack
from app.core.seed_pack.manifest import (
    SYSTEM_PRIORITY_MAX,
    TENANT_PRIORITY_MIN,
    parse_pack,
)
from app.models.enums import (
    SeedItemAction,
    SeedItemState,
    SeedRunMode,
    SeedRunStatus,
    SeedSourceKind,
    SeedSourceScope,
    SeedVersionStatus,
)
from app.models.seed_source_model import (
    SeedDeferredRef,
    SeedDeferredStatus,
    SeedImportRun,
    SeedImportRunItem,
    SeedItemProvenance,
    SeedSource,
    SeedSourceVersion,
)
from app.models.user_model import Role
from app.services.audit.audit_service import log_audit_action
from app.services.seed import transport
from app.services.seed.applier import PackApplication, SeedPackApplier
from app.services.seed.secrets import resolve_secret_slot
from app.services.seed.transport import SeedTransportError

logger = logging.getLogger(__name__)

APPLY_ACTIONS = (
    SeedItemAction.CREATE,
    SeedItemAction.UPDATE,
    SeedItemAction.NOOP,
    SeedItemAction.OVERRIDE,
)


class SeedSourceError(RuntimeError):
    """A typed seed-source refusal (nothing was written; the API maps
    ``code`` onto HTTP status + a typed detail object)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _sync_lock_key(source_id: uuid.UUID) -> int:
    import hashlib

    digest = hashlib.sha256(f"seed-sync:{source_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def _apply_lane_lock_keys(source: SeedSource) -> list[int]:
    """The advisory-lock keys of the **apply** lane for one source (the same
    keys a concurrent apply of this source holds): one global key for the
    system lane, the per-source key for a tenant overlay. A mutation that
    rewrites applied rows (retire) must hold them, or it could interleave
    with a live apply."""
    from app.services.seed.applier import _lock_key

    if source.scope is not None and source.scope.value == "tenant":
        return [_lock_key("tenant", str(source.id))]
    return [_lock_key("system")]


# -- content cache (pack bytes, content-addressed per source+digest) --------


def _cache_path(source_id: uuid.UUID, digest: str) -> Any:
    """The fallback local location (dual-read only; new packs are objects)."""
    from pathlib import Path

    return Path(get_settings().HA_UPLOAD_DIR) / "seed-packs" / str(source_id) / f"{digest}.zip"


def _digest_is_safe(digest: str) -> bool:
    return len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest)


def store_pack_cache(
    source_id: uuid.UUID, digest: str, manifest: dict, files: dict[str, bytes]
) -> None:
    """Persist a version's pack content (one stored object, content-addressed).

    The cache is what plan/apply read — startup and applies never fetch; a
    missing cache entry is re-fetched once (and re-verified against the
    digest) instead of making apply impossible after a cache wipe. The key is
    ``tenants/system/seed-packs/<source>/<digest>.zip``; the digest check at
    read time is unchanged.
    """
    if not _digest_is_safe(digest):
        raise SeedSourceError("INVALID_DIGEST", "version digest is not a sha256 hex string")
    from app.storage.artifacts import ArtifactStorage, seed_pack_key

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", _canonical_json(manifest))
        for name in sorted(files):
            zf.writestr(name, files[name])
    ArtifactStorage().put_bytes(
        seed_pack_key(source_id, digest),
        buffer.getvalue(),
        content_type="application/zip",
    )


def read_pack_cache(source_id: uuid.UUID, digest: str) -> bytes | None:
    """The cached pack zip bytes, or ``None`` (caller re-fetches)."""
    if not _digest_is_safe(digest):
        return None
    from app.storage.artifacts import ArtifactStorage, seed_pack_key

    return ArtifactStorage().read_with_fallback(
        seed_pack_key(source_id, digest), _cache_path(source_id, digest)
    )


def _canonical_json(manifest: dict) -> str:
    import json

    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# -- actor-scoped row access (hidden 404 cross-tenant) -----------------------


def _is_system_admin(actor) -> bool:
    return actor is not None and getattr(actor, "role", None) == Role.SYSTEM_ADMIN


async def get_source_for_actor(session: AsyncSession, source_id: str, actor) -> SeedSource:
    """The source row, or ``SOURCE_NOT_FOUND`` — a cross-tenant id is a 404,
    never a 403 (the row's existence is not disclosed)."""
    try:
        pk = uuid.UUID(str(source_id))
    except ValueError as exc:
        raise SeedSourceError("SOURCE_NOT_FOUND", "no such source") from exc
    source = await session.get(SeedSource, pk)
    if source is None:
        raise SeedSourceError("SOURCE_NOT_FOUND", "no such source")
    if not _is_system_admin(actor) and (
        source.scope is None
        or source.scope is not SeedSourceScope.TENANT
        or str(source.tenant_id) != str(getattr(actor, "tenant_id", None))
    ):
        raise SeedSourceError("SOURCE_NOT_FOUND", "no such source")
    return source


async def get_version_for_actor(session: AsyncSession, version_id: str, actor) -> SeedSourceVersion:
    """The version row (through its source's actor scoping), or ``SOURCE_NOT_FOUND``."""
    try:
        pk = uuid.UUID(str(version_id))
    except ValueError as exc:
        raise SeedSourceError("SOURCE_NOT_FOUND", "no such version") from exc
    version = await session.get(SeedSourceVersion, pk)
    if version is None:
        raise SeedSourceError("SOURCE_NOT_FOUND", "no such version")
    await get_source_for_actor(session, str(version.source_id), actor)
    return version


# -- registration ------------------------------------------------------------


async def create_source(
    session: AsyncSession,
    *,
    kind: str,
    priority: int,
    scope: str,
    actor,
    uri: str | None = None,
    ref_spec: str | None = None,
    subpath: str | None = None,
    auth_secret_ref: str | None = None,
    tenant_id: uuid.UUID | None = None,
) -> SeedSource:
    """Register one source (validated; the row is flushed, not committed)."""
    try:
        source_kind = SeedSourceKind(kind)
    except ValueError as exc:
        raise SeedSourceError(
            "INVALID_KIND",
            f"kind {kind!r} is not one of file|git|registry (bundled is boot-only)",
        ) from exc
    if source_kind is SeedSourceKind.BUNDLED:
        raise SeedSourceError("INVALID_KIND", "the bundled lane is boot-only and cannot be created")
    try:
        source_scope = SeedSourceScope(scope)
    except ValueError as exc:
        raise SeedSourceError("INVALID_SCOPE", f"scope {scope!r} is not system|tenant") from exc

    if not _is_system_admin(actor):
        if source_scope is not SeedSourceScope.TENANT:
            raise SeedSourceError(
                "FORBIDDEN_SCOPE", "only a SYSTEM_ADMIN registers system-scope sources"
            )
        tenant_id = getattr(actor, "tenant_id", None)
        if tenant_id is None:
            raise SeedSourceError("FORBIDDEN_SCOPE", "the caller has no tenant to scope into")
    elif source_scope is SeedSourceScope.TENANT:
        if tenant_id is None:
            raise SeedSourceError(
                "INVALID_SCOPE", "a tenant-scope source requires the tenant it overlays"
            )
    else:
        tenant_id = None

    if not isinstance(priority, int) or isinstance(priority, bool):
        raise SeedSourceError("INVALID_PRIORITY", f"priority {priority!r} is not an integer")
    if source_scope is SeedSourceScope.SYSTEM:
        if not 0 <= priority <= SYSTEM_PRIORITY_MAX:
            raise SeedSourceError(
                "INVALID_PRIORITY",
                f"system sources use 0-{SYSTEM_PRIORITY_MAX} (bundled pins 0); got {priority}",
            )
    elif not TENANT_PRIORITY_MIN <= priority <= 999:
        raise SeedSourceError(
            "INVALID_PRIORITY",
            f"tenant overlays use {TENANT_PRIORITY_MIN}-999; got {priority}",
        )

    if source_kind is SeedSourceKind.GIT or source_kind is SeedSourceKind.REGISTRY:
        uri = transport.validate_https_url(uri, purpose="uri")
    else:
        uri = (uri or "").strip() or f"upload://{source_scope.value}/{uuid.uuid4().hex[:12]}"
    ref_spec = transport.validate_git_ref(ref_spec) if source_kind is SeedSourceKind.GIT else None
    subpath = transport.validate_subpath(subpath) if source_kind is SeedSourceKind.GIT else None

    source = SeedSource(
        kind=source_kind,
        uri=uri,
        ref_spec=ref_spec,
        subpath=subpath,
        priority=priority,
        scope=source_scope,
        tenant_id=tenant_id,
        auth_secret_ref=(auth_secret_ref or "").strip() or None,
        enabled=True,
    )
    session.add(source)
    await session.flush()
    await log_audit_action(
        tenant_id=source.tenant_id,
        user_id=getattr(actor, "user_id", None),
        action="seed.source.create",
        resource_type="seed_source",
        resource_id=source.id,
        outcome="ok",
        new_value={
            "kind": source.kind.value,
            "scope": source.scope.value,
            "priority": source.priority,
            "auth_secret_ref": source.auth_secret_ref,
        },
        required=True,
        session=session,
    )
    return source


# -- sync (the fetch + register door; startup never calls this) --------------


async def sync_source(session: AsyncSession, source: SeedSource, *, actor=None) -> dict:
    """Fetch the source's current pack and register a new digest as pending.

    Returns a summary dict (``status`` in ``registered|up_to_date``, the
    version views, a ``note`` for upload-driven file sources). Transport or
    validation failures record ``last_error`` (redacted, capped) and
    re-raise — every pin stays, nothing is written.
    """
    if source.kind is SeedSourceKind.BUNDLED:
        raise SeedSourceError(
            "BUNDLED_IMMUTABLE", "the bundled lane syncs at boot, never over the wire"
        )
    if source.kind is SeedSourceKind.FILE:
        source.last_check_at = datetime.now(UTC)
        source.last_error = None
        return {
            "status": "up_to_date",
            "note": "file sources receive packs through upload",
            "versions": [],
        }

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"), {"k": _sync_lock_key(source.id)}
    )
    token = None
    if source.auth_secret_ref:
        token = resolve_secret_slot(source.auth_secret_ref)
        if token is None:
            message = (
                f"secret slot {source.auth_secret_ref!r} did not resolve "
                "(keyring/env fallback empty) — fetch refused"
            )
            source.last_check_at = datetime.now(UTC)
            source.last_error = message
            raise SeedSourceError("SECRET_MISSING", message)

    if_none_match = await _latest_etag(session, source)
    try:
        fetched = await transport.fetch_pack(source, token=token, if_none_match=if_none_match)
    except SeedTransportError as exc:
        source.last_check_at = datetime.now(UTC)
        source.last_error = redact_secret(str(exc), token)
        raise
    source.last_check_at = datetime.now(UTC)
    if fetched.is_not_modified:
        source.last_error = None
        return {"status": "up_to_date", "note": "registry answered 304", "versions": []}

    parsed, errors = parse_pack(fetched.manifest, fetched.files)
    if errors or parsed is None:
        message = "; ".join(str(error) for error in errors[:5])
        source.last_error = message
        raise SeedSourceError("PACK_INVALID", message)
    try:
        _check_manifest_coherence(source, parsed.manifest)
    except SeedSourceError as exc:
        source.last_error = f"{exc.code}: {str(exc).split(': ', 1)[-1]}"[:500]
        raise

    existing = (
        await session.execute(
            select(SeedSourceVersion).where(
                SeedSourceVersion.source_id == source.id,
                SeedSourceVersion.digest == parsed.digest,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        source.last_error = None
        return {
            "status": "up_to_date",
            "note": "digest already registered",
            "versions": [existing.to_dict()],
        }

    version = SeedSourceVersion(
        source_id=source.id,
        digest=parsed.digest,
        commit_sha=fetched.commit_sha,
        etag=fetched.etag,
        manifest=parsed.manifest,
        contract_version=str(parsed.manifest.get("schema_version", "1.0")),
        status=SeedVersionStatus.PENDING,
        reason=f"registered by sync ({source.kind.value})",
    )
    session.add(version)
    await session.flush()
    await anyio.to_thread.run_sync(
        partial(store_pack_cache, source.id, parsed.digest, parsed.manifest, fetched.files)
    )
    source.last_error = None
    await log_audit_action(
        tenant_id=source.tenant_id,
        user_id=getattr(actor, "user_id", None) if actor else None,
        action="seed.source.sync",
        resource_type="seed_source",
        resource_id=source.id,
        outcome="ok",
        new_value={
            "version_id": str(version.id),
            "digest": parsed.digest[:12],
            "commit_sha": fetched.commit_sha,
            "pack_id": parsed.manifest.get("id"),
            "pack_version": parsed.manifest.get("version"),
        },
        required=True,
        session=session,
    )
    return {"status": "registered", "versions": [version.to_dict()]}


def _check_manifest_coherence(source: SeedSource, manifest: dict) -> None:
    """The manifest is pinned to the source row at registration (item 5).

    The planner/applier later trust the source row's values alone; a pack
    whose declared scope/priority disagree with the row is refused here, so
    the two can never drift apart after approval.
    """
    manifest_scope = manifest.get("scope")
    expected_scope = source.scope.value if source.scope else "system"
    if manifest_scope != expected_scope:
        raise SeedSourceError(
            "SCOPE_MISMATCH",
            f"pack declares scope {manifest_scope!r} but the source row is {expected_scope!r} "
            "— register a source matching the pack (or fix the pack manifest)",
        )
    manifest_priority = manifest.get("priority")
    if manifest_priority != source.priority:
        raise SeedSourceError(
            "PRIORITY_MISMATCH",
            f"pack declares priority {manifest_priority!r} but the source row has "
            f"{source.priority} — they are pinned together at registration",
        )


async def _latest_etag(session: AsyncSession, source: SeedSource) -> str | None:
    """The newest version's etag (the conditional-GET handle)."""
    row = (
        await session.execute(
            select(SeedSourceVersion.etag)
            .where(SeedSourceVersion.source_id == source.id, SeedSourceVersion.etag.is_not(None))
            .order_by(SeedSourceVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


# -- upload (the file-source content door) -----------------------------------


async def register_upload(
    session: AsyncSession,
    source: SeedSource,
    *,
    content: bytes,
    filename: str,
    actor=None,
    pack_id: str | None = None,
    version_str: str | None = None,
    license_id: str = "CC0-1.0",
) -> SeedSourceVersion:
    """Register one uploaded pack (zip or single item-file JSON) as pending.

    The archive runs the guarded entry rules (``manifest.json`` +
    ``items/**.json`` only; traversal and oversized entries are typed
    refusals) and the single-JSON path wraps the envelope into a manifest
    carrying the source's scope/priority — coherence by construction. The
    parsed digest pins the pending version (re-uploading the same content
    returns the existing row).
    """
    from app.services.seed.transport import build_pack_from_single_json, build_pack_from_zip

    if source.kind is not SeedSourceKind.FILE:
        raise SeedSourceError(
            "INVALID_KIND", "only file sources accept uploads (git/registry sources sync)"
        )
    if zipfile.is_zipfile(io.BytesIO(content)):
        manifest, files, parsed, errors = build_pack_from_zip(content)
        if errors or parsed is None:
            raise SeedSourceError("PACK_INVALID", "; ".join(str(error) for error in errors[:5]))
        _check_manifest_coherence(source, manifest)
    else:
        if not pack_id:
            pack_id = f"upload-{source.id.hex[:8]}"
        version_str = version_str or await _next_upload_version(session, source)
        manifest, files, parsed, errors = build_pack_from_single_json(
            filename,
            content,
            pack_id=pack_id,
            version=version_str,
            license_id=license_id,
            scope=source.scope.value if source.scope else "system",
            priority=int(source.priority or 0),
        )
        if errors or parsed is None:
            raise SeedSourceError("PACK_INVALID", "; ".join(str(error) for error in errors[:5]))
    digest = parsed.digest
    existing = (
        await session.execute(
            select(SeedSourceVersion).where(
                SeedSourceVersion.source_id == source.id,
                SeedSourceVersion.digest == digest,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    version = SeedSourceVersion(
        source_id=source.id,
        digest=digest,
        manifest=parsed.manifest,
        contract_version=str(parsed.manifest.get("schema_version", "1.0")),
        status=SeedVersionStatus.PENDING,
        reason=f"uploaded {filename[:120]}",
    )
    session.add(version)
    await session.flush()
    await anyio.to_thread.run_sync(
        partial(store_pack_cache, source.id, digest, parsed.manifest, files)
    )
    await log_audit_action(
        tenant_id=source.tenant_id,
        user_id=getattr(actor, "user_id", None) if actor else None,
        action="seed.source.upload",
        resource_type="seed_source",
        resource_id=source.id,
        outcome="ok",
        new_value={
            "version_id": str(version.id),
            "digest": digest[:12],
            "filename": filename[:120],
            "pack_id": parsed.manifest.get("id"),
        },
        required=True,
        session=session,
    )
    return version


async def _next_upload_version(session: AsyncSession, source: SeedSource) -> str:
    """The next upload version label (``1.0.<count>`` — deterministic, no
    timestamps; digest stability is the pack's own law)."""
    from sqlalchemy import func

    count = (
        await session.execute(
            select(func.count())
            .select_from(SeedSourceVersion)
            .where(SeedSourceVersion.source_id == source.id)
        )
    ).scalar_one()
    return f"1.0.{int(count or 0)}"


# -- pack materialization (cache-first; apply never needs the network) -------


async def pack_for_version(
    session: AsyncSession, source: SeedSource, version: SeedSourceVersion
) -> ParsedPack:
    """The parsed pack of one version — cache first, one re-fetch on a miss.

    Every materialization re-verifies the digest against the version row:
    a cache that does not hash to the pinned digest (or an upstream that
    moved under the same ref) is a typed ``DIGEST_DRIFT`` refusal — the
    approval anchor is the digest, never a file.
    """
    if source.kind is SeedSourceKind.BUNDLED:
        pack = _bundled_pack()
        if pack is None or pack.digest != version.digest:
            raise SeedSourceError(
                "DIGEST_DRIFT",
                "the bundled pack no longer hashes to this version's digest — "
                "sync at boot registers the new digest",
            )
        return pack
    cached = await anyio.to_thread.run_sync(
        partial(read_pack_cache, source.id, version.digest)
    )
    if cached is not None:
        parsed = _parse_cached(source, version, cached)
        if parsed is not None:
            return parsed
    token = resolve_secret_slot(source.auth_secret_ref) if source.auth_secret_ref else None
    if source.auth_secret_ref and token is None:
        raise SeedSourceError(
            "SECRET_MISSING",
            f"secret slot {source.auth_secret_ref!r} did not resolve — cannot materialize "
            "the version content (cache is empty)",
        )
    fetched = await transport.fetch_pack(source, token=token)
    if fetched.is_not_modified or not fetched.files:
        raise SeedSourceError("PACK_NOT_FOUND", "the source returned no pack content")
    parsed, errors = parse_pack(fetched.manifest, fetched.files)
    if errors or parsed is None:
        raise SeedSourceError("PACK_INVALID", "; ".join(str(error) for error in errors[:5]))
    if parsed.digest != version.digest:
        raise SeedSourceError(
            "DIGEST_DRIFT",
            f"the source now hashes to {parsed.digest[:12]}…, not the pinned "
            f"{version.digest[:12]}… — sync to register the new digest",
        )
    await anyio.to_thread.run_sync(
        partial(store_pack_cache, source.id, version.digest, parsed.manifest, fetched.files)
    )
    return parsed


def _bundled_pack() -> ParsedPack | None:
    from app.core.seed_pack.bundled import load_bundled_pack

    try:
        return load_bundled_pack()
    except Exception:
        return None


def _parse_cached(
    source: SeedSource, version: SeedSourceVersion, cached: bytes
) -> ParsedPack | None:
    """Parse the cached zip; ``None`` when the cache is unreadable (the
    caller re-fetches instead of failing the apply)."""
    try:
        manifest, files = transport.split_archive(cached)
        parsed, errors = parse_pack(manifest, files)
        if errors or parsed is None or parsed.digest != version.digest:
            return None
        return parsed
    except Exception:
        return None


# -- version lifecycle --------------------------------------------------------


async def approve_version(
    session: AsyncSession, version: SeedSourceVersion, *, actor
) -> SeedSourceVersion:
    """``pending → approved`` — the explicit act on the exact digest.

    The pointer moves only on a successful apply; approving never writes
    catalog rows. The bundled lane refuses (auto-approved by definition).
    """
    source = await session.get(SeedSource, version.source_id)
    if source is not None and source.kind is SeedSourceKind.BUNDLED:
        raise SeedSourceError(
            "BUNDLED_IMMUTABLE", "bundled versions are auto-approved (trusted lane)"
        )
    if version.status is not SeedVersionStatus.PENDING:
        raise SeedSourceError(
            "VERSION_NOT_PENDING",
            f"version is {version.status.value if version.status else '?'}, not pending",
        )
    version.status = SeedVersionStatus.APPROVED
    version.approved_by = getattr(actor, "user_id", None)
    version.approved_at = datetime.now(UTC)
    version.reason = None
    await log_audit_action(
        tenant_id=source.tenant_id if source else None,
        user_id=getattr(actor, "user_id", None),
        action="seed.version.approve",
        resource_type="seed_source_version",
        resource_id=version.id,
        outcome="ok",
        new_value={"digest": version.digest[:12], "status": "approved"},
        required=True,
        session=session,
    )
    return version


async def reject_version(
    session: AsyncSession, version: SeedSourceVersion, *, actor, reason: str | None = None
) -> SeedSourceVersion:
    """``pending → rejected`` with the typed problems (or the admin note)."""
    source = await session.get(SeedSource, version.source_id)
    if source is not None and source.kind is SeedSourceKind.BUNDLED:
        raise SeedSourceError("BUNDLED_IMMUTABLE", "bundled versions cannot be rejected")
    if version.status is not SeedVersionStatus.PENDING:
        raise SeedSourceError(
            "VERSION_NOT_PENDING",
            f"version is {version.status.value if version.status else '?'}, not pending",
        )
    version.status = SeedVersionStatus.REJECTED
    version.reason = (reason or "rejected by admin")[:500]
    await log_audit_action(
        tenant_id=source.tenant_id if source else None,
        user_id=getattr(actor, "user_id", None),
        action="seed.version.reject",
        resource_type="seed_source_version",
        resource_id=version.id,
        outcome="ok",
        new_value={"digest": version.digest[:12], "status": "rejected", "reason": version.reason},
        required=True,
        session=session,
    )
    return version


async def revoke_version(session: AsyncSession, version: SeedSourceVersion, *, actor) -> dict:
    """``approved → revoked`` — and the rollback: the previous approved
    version re-applies (restoring the items this version retired).

    With no previous approved version the source is left pointer-less
    (``current_version_id`` NULL — the FK's ``SET NULL`` shape). The
    bundled lane refuses (its rollback is the previous bundled digest at
    boot).
    """
    source = await session.get(SeedSource, version.source_id)
    if source is not None and source.kind is SeedSourceKind.BUNDLED:
        raise SeedSourceError(
            "BUNDLED_IMMUTABLE", "bundled versions cannot be revoked (trusted lane)"
        )
    if version.status is not SeedVersionStatus.APPROVED:
        raise SeedSourceError(
            "VERSION_NOT_APPROVED",
            f"version is {version.status.value if version.status else '?'}, not approved",
        )
    version.status = SeedVersionStatus.REVOKED
    await session.refresh(source)
    was_current = source is not None and source.current_version_id == version.id
    restored: str | None = None
    if was_current and source is not None:
        previous = await _previous_approved_version(session, source, exclude=version.id)
        if previous is not None:
            pack = await pack_for_version(session, source, previous)
            await SeedPackApplier().apply(
                [PackApplication(source=source, version=previous, pack=pack)]
            )
            restored = str(previous.id)
        else:
            source.current_version_id = None
    await session.refresh(version)
    await log_audit_action(
        tenant_id=source.tenant_id if source else None,
        user_id=getattr(actor, "user_id", None),
        action="seed.version.revoke",
        resource_type="seed_source_version",
        resource_id=version.id,
        outcome="ok",
        old_value={"status": "approved", "was_current": was_current},  # gate-allow: *, audit diff
        new_value={
            "digest": version.digest[:12],
            "status": "revoked",
            "restored_version_id": restored,
        },
        required=True,
        session=session,
    )
    return {"version": version.to_dict(), "restored_version_id": restored}


async def _previous_approved_version(
    session: AsyncSession, source: SeedSource, *, exclude: uuid.UUID
) -> SeedSourceVersion | None:
    """The most recently approved remaining version (the revocation's
    rollback target — the one that re-applies and restores)."""
    return (
        await session.execute(
            select(SeedSourceVersion)
            .where(
                SeedSourceVersion.source_id == source.id,
                SeedSourceVersion.id != exclude,
                SeedSourceVersion.status == SeedVersionStatus.APPROVED,
            )
            .order_by(
                SeedSourceVersion.approved_at.desc().nulls_last(),
                SeedSourceVersion.created_at.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


# -- plan / apply ------------------------------------------------------------


async def plan_version(
    session: AsyncSession, source: SeedSource, version: SeedSourceVersion
) -> Any:
    """The read-only plan run (the preview): stores a plan-mode run, writes
    nothing else. Pending versions plan too — the preview precedes approval."""
    pack = await pack_for_version(session, source, version)
    return await SeedPackApplier().plan_only(
        PackApplication(source=source, version=version, pack=pack)
    )


async def apply_version(
    session: AsyncSession, source: SeedSource, version: SeedSourceVersion, *, actor=None
) -> Any:
    """The real apply — approval-checked, under the applier's locks.

    The run's final status (``ok|partial|failed|conflicted``) is the
    surfaced answer; the applier audits the pointer advance itself. This is
    also the explicit re-apply door: re-running a version whose last apply
    was PARTIAL retries exactly the failed items (their plan entries are
    the only ones that still write).
    """
    if version.status is not SeedVersionStatus.APPROVED:
        raise SeedSourceError(
            "VERSION_NOT_APPROVED",
            f"version is {version.status.value if version.status else '?'}, not approved — "
            "approve the digest before applying",
        )
    pack = await pack_for_version(session, source, version)
    return await SeedPackApplier().apply(
        [PackApplication(source=source, version=version, pack=pack)]
    )


# -- adopt / disable / retire --------------------------------------------------


async def adopt_updates(
    session: AsyncSession, source: SeedSource, *, actor, seed_key: str | None = None
) -> dict:
    """The explicit "update available" adoption (ADR 0056 assumption 5).

    Clears ``user_modified`` on the provenance rows this source's versions
    last wrote (optionally only ``seed_key``) and re-applies the current
    version — the formerly frozen rows now plan UPDATE and the pack content
    wins. The clears commit before the re-apply: the applier plans on its
    own lock-holding connection, so an uncommitted ``user_modified=False``
    would be invisible to the re-plan and adoption would be a no-op. Rows
    the pack no longer differs on stay untouched.

    The re-apply's failure is audited exactly like its success — one
    ``seed.source.adopt`` row either way (outcome ``error`` with the
    failure) — and then surfaces as a typed ``ADOPT_APPLY_FAILED``. The
    committed clears are never left un-audited state.
    """
    await session.refresh(source)
    if source.current_version_id is None:
        raise SeedSourceError("VERSION_NOT_APPROVED", "the source has no current approved version")
    stmt = (
        select(SeedItemProvenance)
        .join(SeedSourceVersion, SeedSourceVersion.id == SeedItemProvenance.source_version_id)
        .where(
            SeedSourceVersion.source_id == source.id,
            SeedItemProvenance.user_modified.is_(True),
        )
    )
    if seed_key:
        stmt = stmt.where(SeedItemProvenance.seed_key == seed_key)
    rows = (await session.execute(stmt)).scalars().all()
    for prov in rows:
        prov.user_modified = False
    await session.commit()
    version = await session.get(SeedSourceVersion, source.current_version_id)
    outcome = None
    failure: str | None = None
    if version is not None:
        try:
            pack = await pack_for_version(session, source, version)
            outcome = await SeedPackApplier().apply(
                [PackApplication(source=source, version=version, pack=pack)]
            )
        except Exception as exc:
            await session.rollback()
            failure = str(exc)[:400]
    await log_audit_action(
        tenant_id=source.tenant_id,
        user_id=getattr(actor, "user_id", None),
        action="seed.source.adopt",
        resource_type="seed_source",
        resource_id=source.id,
        outcome="error" if failure is not None else "ok",
        new_value={
            "cleared": len(rows),
            "seed_key": seed_key,
            "version_id": str(source.current_version_id),
            "statuses": {
                str(k): s.value if s else None
                for k, s in (outcome.statuses.items() if outcome else [])
            },
            "error": failure,
        },
        required=True,
        session=session,
    )
    if failure is not None:
        raise SeedSourceError(
            "ADOPT_APPLY_FAILED",
            f"the re-apply failed after clearing {len(rows)} frozen row(s) — "
            "the freeze-clears stand; fix the pack and re-adopt. Cause: " + failure,
        )
    return {"cleared": len(rows), "seed_key": seed_key}


async def set_enabled(
    session: AsyncSession, source: SeedSource, enabled: bool, *, actor
) -> SeedSource:
    """Enable/disable a source (a disabled source provides no keys and is
    hidden from the planners). The bundled lane cannot be disabled — it is
    the boot lane."""
    if source.kind is SeedSourceKind.BUNDLED and not enabled:
        raise SeedSourceError("BUNDLED_IMMUTABLE", "the bundled boot lane cannot be disabled")
    source.enabled = bool(enabled)
    await log_audit_action(
        tenant_id=source.tenant_id,
        user_id=getattr(actor, "user_id", None),
        action="seed.source.enable" if enabled else "seed.source.disable",
        resource_type="seed_source",
        resource_id=source.id,
        outcome="ok",
        new_value={"enabled": bool(enabled)},
        required=True,
        session=session,
    )
    return source


async def retire_source(session: AsyncSession, source: SeedSource, *, actor) -> dict:
    """Retire a source: its items are retired (only where nothing else
    provides them), the source is disabled — rows are never deleted.

    The item set comes from the source's **run history** (the durable claim
    record): provenance alone undercounts once a higher-priority source has
    taken a key over (the provenance row follows the winner). The per-item
    outcome is recorded as run items of a dedicated apply-mode run on the
    current version. The apply lane's advisory lock is held for the whole
    retirement (a concurrent apply of this source waits, exactly as it
    would behind another apply) — released when the caller's transaction
    ends.
    """
    if source.kind is SeedSourceKind.BUNDLED:
        raise SeedSourceError("BUNDLED_IMMUTABLE", "the bundled lane cannot be retired")
    for key in _apply_lane_lock_keys(source):
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})
    await session.refresh(source)
    version_ids = (
        (
            await session.execute(
                select(SeedSourceVersion.id)
                .where(SeedSourceVersion.source_id == source.id)
                .order_by(SeedSourceVersion.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    retired, kept = 0, 0
    run: SeedImportRun | None = None
    if version_ids:
        claims = (
            await session.execute(
                select(
                    SeedImportRunItem.entity_type,
                    SeedImportRunItem.entity_id,
                    SeedImportRunItem.seed_key,
                )
                .join(SeedImportRun, SeedImportRun.id == SeedImportRunItem.run_id)
                .where(
                    SeedImportRun.source_version_id.in_(version_ids),
                    SeedImportRun.mode == SeedRunMode.APPLY,
                    SeedImportRun.status.in_((SeedRunStatus.OK, SeedRunStatus.PARTIAL)),
                    SeedImportRunItem.action.in_(APPLY_ACTIONS),
                    SeedImportRunItem.entity_id.is_not(None),
                )
                .distinct()
            )
        ).all()
        anchor_id = source.current_version_id or (version_ids[0] if version_ids else None)
        run = SeedImportRun(
            source_version_id=anchor_id,
            mode=SeedRunMode.APPLY,
            status=SeedRunStatus.RUNNING,
            plan={},
            stats={"action": "retire_source", "source_id": str(source.id)},
        )
        session.add(run)
        await session.flush()
        from app.services.seed.applier import _retire_entity

        for entity_type, entity_id, seed_key in sorted(claims, key=lambda c: (c[0], str(c[1]))):
            still_provided = await _other_source_provides(
                session,
                exclude_source_id=source.id,
                entity_type=entity_type,
                seed_key=seed_key or "",
                entity_id=entity_id,
            )
            if still_provided:
                kept += 1
                session.add(
                    SeedImportRunItem(
                        run_id=run.id,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        seed_key=seed_key,
                        action=SeedItemAction.NOOP,
                        conflict={"reason": "still_provided_by_active_source"},
                    )
                )
                continue
            prov = await _get_provenance_row(session, entity_type, entity_id)
            if prov is not None:
                prov.state = SeedItemState.RETIRED
            await _retire_entity(session, entity_type, entity_id)
            retired += 1
            session.add(
                SeedImportRunItem(
                    run_id=run.id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    seed_key=seed_key,
                    action=SeedItemAction.RETIRE,
                    conflict={"reason": "retire_source"},
                )
            )
        run.status = SeedRunStatus.OK
        run.stats = {**run.stats, "retired": retired, "kept": kept}
        run.finished_at = datetime.now(UTC)
    source.enabled = False
    await log_audit_action(
        tenant_id=source.tenant_id,
        user_id=getattr(actor, "user_id", None),
        action="seed.source.retire",
        resource_type="seed_source",
        resource_id=source.id,
        outcome="ok",
        new_value={"retired": retired, "kept": kept, "enabled": False},
        required=True,
        session=session,
    )
    return {"retired": retired, "kept": kept}


async def _get_provenance_row(
    session: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> SeedItemProvenance | None:
    """The provenance row of one entity (``None`` = never seed-written)."""
    return (
        await session.execute(
            select(SeedItemProvenance).where(
                SeedItemProvenance.entity_type == entity_type,
                SeedItemProvenance.entity_id == entity_id,
            )
        )
    ).scalar_one_or_none()


async def _other_source_provides(
    session: AsyncSession,
    *,
    exclude_source_id: uuid.UUID,
    entity_type: str,
    seed_key: str,
    entity_id: uuid.UUID | None,
) -> bool:
    """Whether another enabled, approved source last applied this key
    successfully (the retire-only-when-unclaimed rule; matched per entity
    id so another tenant's overlay claim never blocks)."""
    from app.models.enums import SeedRunStatus as _RunStatus
    from app.models.enums import SeedVersionStatus as _VersionStatus

    stmt = (
        select(SeedImportRunItem.id)
        .join(SeedImportRun, SeedImportRun.id == SeedImportRunItem.run_id)
        .join(SeedSourceVersion, SeedSourceVersion.id == SeedImportRun.source_version_id)
        .join(SeedSource, SeedSource.id == SeedSourceVersion.source_id)
        .where(
            SeedImportRunItem.entity_type == entity_type,
            SeedImportRunItem.seed_key == seed_key,
            SeedImportRunItem.action.in_(APPLY_ACTIONS),
            SeedImportRun.mode == SeedRunMode.APPLY,
            SeedImportRun.status.in_((_RunStatus.OK, _RunStatus.PARTIAL)),
            SeedSourceVersion.status == _VersionStatus.APPROVED,
            SeedSource.enabled.is_(True),
            SeedSource.id != exclude_source_id,
        )
        .limit(1)
    )
    if entity_id is not None:
        stmt = stmt.where(SeedImportRunItem.entity_id == entity_id)
    return (await session.execute(stmt)).first() is not None


# -- views --------------------------------------------------------------------


async def list_sources_for_actor(session: AsyncSession, actor) -> list[dict]:
    """The actor's sources with version + latest-run summaries (system admins
    see everything; a tenant admin sees own-tenant overlays)."""
    stmt = select(SeedSource).order_by(SeedSource.priority, SeedSource.created_at)
    if not _is_system_admin(actor):
        tenant_id = getattr(actor, "tenant_id", None)
        stmt = stmt.where(
            SeedSource.scope == SeedSourceScope.TENANT,
            SeedSource.tenant_id == tenant_id,
        )
    sources = (await session.execute(stmt)).scalars().all()
    return [await source_view(session, source) for source in sources]


async def source_view(session: AsyncSession, source: SeedSource) -> dict:
    """One source's admin view: the row plus version summaries (each with
    its manifest-derived pack metadata — id/version/scope/priority and the
    declared file + item counts the Sources table renders — and the latest
    run's mode/status: the history + PARTIAL probe)."""
    versions = (
        (
            await session.execute(
                select(SeedSourceVersion)
                .where(SeedSourceVersion.source_id == source.id)
                .order_by(SeedSourceVersion.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    latest_runs = await _latest_runs(session, [v.id for v in versions])
    return {
        **source.to_dict(),
        "current_version_id": str(source.current_version_id) if source.current_version_id else None,
        "versions": [
            {
                **v.to_dict(),
                "is_current": source.current_version_id == v.id,
                **_pack_summary(v.manifest),
                "latest_run": latest_runs.get(v.id),
            }
            for v in versions
        ],
    }


def _pack_summary(manifest: dict | None) -> dict:
    """The manifest-derived summary of one pack version: ``pack_id`` /
    ``pack_version`` / ``pack_scope`` / ``pack_priority`` plus the declared
    ``pack_files`` / ``pack_items`` counts (the item counts the Sources
    table shows without loading the pack)."""
    manifest = manifest or {}
    item_files = manifest.get("item_files")
    files = len(item_files) if isinstance(item_files, list) else None
    return {
        "pack_id": manifest.get("id"),
        "pack_version": manifest.get("version"),
        "pack_scope": manifest.get("scope"),
        "pack_priority": manifest.get("priority"),
        "pack_files": files,
    }


async def _latest_runs(session: AsyncSession, version_ids) -> dict:
    """``version_id → {run_id, mode, status, started_at, stats}`` of the most
    recent run per version (one window-function query, no N+1)."""
    if not version_ids:
        return {}
    rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT ON (r.source_version_id)
                       r.source_version_id AS vid, r.id AS run_id, r.mode AS mode,
                       r.status AS status, r.started_at AS started_at, r.stats AS stats
                FROM seed_import_runs r
                WHERE r.source_version_id = ANY(:vids)
                ORDER BY r.source_version_id, r.started_at DESC, r.created_at DESC
                """
            ),
            {"vids": [str(v) for v in version_ids]},
        )
    ).mappings()
    out = {}
    for row in rows:
        out[uuid.UUID(str(row["vid"]))] = {
            "run_id": str(row["run_id"]),
            "mode": row["mode"],
            "status": row["status"],
            "started_at": row["started_at"].isoformat() if row["started_at"] else None,
            "stats": row["stats"] or {},
        }
    return out


# -- deferred refs (the sync task's retry surface) -----------------------------


async def pending_deferred_count(session: AsyncSession, source: SeedSource) -> int:
    """This source's pending deferred refs (the "unresolved" count a UI can
    surface next to the versions)."""
    from sqlalchemy import func

    return (
        await session.execute(
            select(func.count())
            .select_from(SeedDeferredRef)
            .join(SeedSourceVersion, SeedSourceVersion.id == SeedDeferredRef.source_version_id)
            .where(
                SeedSourceVersion.source_id == source.id,
                SeedDeferredRef.status == SeedDeferredStatus.PENDING,
            )
        )
    ).scalar_one()
