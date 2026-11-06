"""lifecycle — the imported-extension source lane (plan 7 W2).

Health's persistence seams for the family import pipeline (ADR-0040): the
charter is the law, this module only lands health's rows for it. Content
resolution is digest-pinned and fail-closed at every step:

1. a source descriptor validates against the vendored
   ``extension-sources.schema.json`` (production stays https-only by
   structure; the local dev variant rides ``$defs.localSourceDescriptor``
   and is gated off outside development boots, confined to allowlisted
   roots);
2. resolved content is hashed into a content digest (regular files only —
   symlinks never contribute and never execute) and materialized
   content-addressed under the extensions data dir;
3. every discovered manifest goes through the ONE registration decision
   (``contract.validate_registration``) with the source's trust tier as the
   caller claim — a claim that contradicts the ``derive_tier`` derivation is
   the typed ``provenance derives tier …`` problem;
4. the outcome is a ROW: ``pending`` versions await approval, rejected
   versions keep their typed problems in ``reason`` — a self-disable is
   never a log-and-skip (plan 37 6.1). A digest change is a NEW row
   requiring re-approval; nothing auto-upgrades a running instance.

Git/registry transports are not part of this sitting: a git/registry source
syncs to the typed ``not-ready`` (the source row + its pins are honest),
while local sources import end-to-end on a dev box.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extensions import contract, isolation
from app.extensions.errors import ExtensionError
from app.models.enums import (
    ExtensionSourceKind,
    ExtensionSourceTrustTier,
    ExtensionTier,
    ExtensionVersionStatus,
)
from app.models.extension_source_model import (
    ExtensionIsolationPosture,
    ExtensionSource,
    ExtensionSourceVersion,
)

logger = logging.getLogger(__name__)

_EXTENSIONS_SUBDIR = "extensions"
_TREE_MAX_FILES = 2000
_TREE_MAX_BYTES = 64 * 1024 * 1024
_MANIFEST_NAME = "manifest.json"
_CONTRACT_VERSION = contract.ENVELOPE_API_VERSION
_DEPLOYMENT_ENVS = ("development", "test", "testing")


def _contracts_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "contracts"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtensionError(
            "not-ready", f"vendored contract set incomplete ({path.name}): {exc}"
        ) from exc


def _sources_schema() -> dict:
    return _load_json(_contracts_dir() / "extension-sources.schema.json")


def _manifest_schema() -> dict:
    return _load_json(_contracts_dir() / "extension-manifest.schema.json")


def _docs_schema() -> dict:
    return _load_json(_contracts_dir() / "docs-tree.schema.json")


def _charter() -> dict:
    from app.extensions.registry import _charter as registry_charter

    return registry_charter()


def _profile() -> dict:
    from app.extensions.registry import _profile as registry_profile

    return registry_profile()


def disclosures() -> dict[str, str]:
    """The charter's consent disclosures, VERBATIM (render law: never
    paraphrase — the surface keys its text to ``disclosureKey``)."""
    consent = _charter().get("consent") or {}
    return {
        "t0DeclarativeDisclosure": str(consent.get("t0DeclarativeDisclosure") or ""),
        "t2SandboxDisclosure": str(consent.get("t2SandboxDisclosure") or ""),
        "t3PerimeterDisclosure": str(consent.get("t3PerimeterDisclosure") or ""),
    }


def disclosure_for_tier(tier: str) -> tuple[str, str]:
    """The (disclosure text, disclosure key) a tier's consent must render."""
    if tier == "T0":
        return disclosures()["t0DeclarativeDisclosure"], "t0DeclarativeDisclosure"
    if tier == "T2":
        return disclosures()["t2SandboxDisclosure"], "t2SandboxDisclosure"
    return disclosures()["t3PerimeterDisclosure"], "t3PerimeterDisclosure"


# ----------------------------------------------------------- local sources


def _local_source_roots() -> list[Path]:
    from app.core.config import settings

    raw = getattr(settings, "HA_EXTENSION_LOCAL_SOURCE_ROOTS", "") or ""
    return [Path(part).expanduser().resolve() for part in raw.split(",") if part.strip()]


def local_sources_available() -> bool:
    """The local dev kind needs a non-production boot AND an allowlisted
    root (fail-closed both ways)."""
    from app.core.config import settings

    return settings.HA_APP_ENV in _DEPLOYMENT_ENVS and bool(_local_source_roots())


def _check_local_root(path_str: str) -> Path:
    from app.core.config import settings

    if settings.HA_APP_ENV not in _DEPLOYMENT_ENVS:
        raise ExtensionError(
            "path-denied",
            "local extension sources are gated off in production boots — fail-closed",
        )
    roots = _local_source_roots()
    if not roots:
        raise ExtensionError(
            "path-denied",
            "local extension sources need an allowlisted root "
            "(HA_EXTENSION_LOCAL_SOURCE_ROOTS) — none configured, fail-closed",
        )
    resolved = Path(path_str).expanduser().resolve()
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if not resolved.exists():
            raise ExtensionError("not-found", f"local source path does not exist: {resolved}")
        return resolved
    raise ExtensionError(
        "path-denied",
        f"local source {resolved} is outside every allowlisted root — refusing",
    )


# --------------------------------------------------------------- digests


def digest_tree(root: Path) -> str:
    """Deterministic sha256 over a content tree: regular files only
    (symlinks and specials are SKIPPED — they never contribute and never
    execute), sorted by relative path, path+NUL+size+NUL+bytes per file.
    Adversarial caps: file count and total bytes (typed ``invalid`` beyond).
    """
    digest = hashlib.sha256()
    files: list[tuple[str, Path]] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        files.append((path.relative_to(root).as_posix(), path))
        total += path.stat().st_size
    if len(files) > _TREE_MAX_FILES or total > _TREE_MAX_BYTES:
        raise ExtensionError(
            "invalid",
            f"extension content exceeds caps ({len(files)} files / {total} bytes)",
        )
    for rel, path in files:
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(path.stat().st_size).encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _copy_tree(src: Path, dest: Path) -> None:
    """Materialize the extension content — regular files only; symlinks
    never cross (the copied tree is exactly what the digest describes and
    what a spawn can execute)."""
    dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.rglob("*")):
        if ".git" in path.relative_to(src).parts:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        target = dest / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def extensions_data_root() -> Path:
    """The content-addressed store root (charter D24 shape:
    ``<data>/extensions/<id>/<digest>/``)."""
    from app.core.config import settings

    return Path(settings.HA_UPLOAD_DIR) / _EXTENSIONS_SUBDIR


def _materialize_content(content_src: Path, extension_id: str, expected_digest: str) -> Path:
    """Land (or REUSE) a content-addressed tree with integrity: an existing
    ``<id>/<digest>/`` is re-hashed before use — altered bytes are purged
    and re-copied, never trusted."""
    dest = extensions_data_root() / extension_id / expected_digest
    if dest.exists():
        if digest_tree(dest) == expected_digest:
            return dest
        shutil.rmtree(dest)
    _copy_tree(content_src, dest)
    landed = digest_tree(dest)
    if landed != expected_digest:
        shutil.rmtree(dest, ignore_errors=True)
        raise ExtensionError(
            "io",
            f"materialized content does not match its digest ({landed} != {expected_digest})",
        )
    return dest


def discover_manifests(tree: Path, subpath: str = "") -> list[dict[str, Any]]:
    """Every ``manifest.json`` under ``tree/subpath`` (bounded depth, sorted,
    the envelope FILE — never code-declared)."""
    base = tree / subpath if subpath else tree
    if not base.is_dir():
        raise ExtensionError("not-found", f"content subpath does not exist: {subpath or '/'}")
    found: list[dict[str, Any]] = []
    for path in sorted(base.rglob(_MANIFEST_NAME)):
        if path.is_symlink() or ".git" in path.relative_to(base).parts:
            continue
        depth = len(path.relative_to(base).parts) - 1
        if depth > 2:
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExtensionError("invalid", f"{path}: unreadable manifest ({exc})") from exc
        found.append(manifest)
    return found


# -------------------------------------------------------------- registration


def consent_echo(manifest: dict, digest: str) -> dict:
    """The exact echo a consent surface hands back (plan 37 Phase 3.1):
    ``{digest, capabilities, version}`` — host-derived, closed keys."""
    return {
        "digest": digest,
        "capabilities": list(manifest.get("capabilities") or []),
        "version": str(manifest.get("version") or ""),
    }


def verify_consent_echo(echo: Any, manifest: dict, digest: str) -> dict:
    """Refuse a forged or drifted echo with the typed ``invalid`` — the
    consent surface may only return what the host derived."""
    if not isinstance(echo, dict) or set(echo) != {"digest", "capabilities", "version"}:
        raise ExtensionError(
            "invalid",
            "the consent echo must carry exactly {digest, capabilities, version}",
        )
    expected = consent_echo(manifest, digest)
    if (
        echo.get("digest") != expected["digest"]
        or set(echo.get("capabilities") or []) != set(expected["capabilities"])
        or echo.get("version") != expected["version"]
    ):
        raise ExtensionError(
            "invalid",
            "the consent echo does not match the candidate (digest/capabilities/version "
            "drift) — re-consent to the current content",
        )
    return expected


async def register_source(
    db: AsyncSession,
    *,
    document: dict,
    trust_tier: str,
    tenant_id: Any | None = None,
) -> ExtensionSource:
    """Validate a source descriptor and upsert its row.

    Production descriptors validate against the schema ROOT (https-only by
    structure); a ``local`` document routes to the distinct
    ``localSourceDescriptor`` def plus the boot gate + root allowlist.
    Unknown shapes fail closed. ``trust_tier`` is health's lane column
    (``T2|T3``) — the descriptor schema deliberately has no tier field.
    """
    schema = _sources_schema()
    if isinstance(document, dict) and "local" in document:
        if "url" in document:
            raise ExtensionError("invalid", "a source is either https or local — never both")
        target = schema["$defs"]["localSourceDescriptor"]
        err = contract.validate(target, document, schema)
        if err is not None:
            raise ExtensionError("invalid", f"local source descriptor invalid: {err}")
        path = _check_local_root(str((document.get("local") or {}).get("path") or ""))
        uri: str = str(path)
        kind = ExtensionSourceKind.FILE
    else:
        err = contract.validate(schema, document, schema)
        if err is not None:
            raise ExtensionError("invalid", f"source descriptor invalid: {err}")
        uri = str(document.get("url") or "")
        kind = ExtensionSourceKind.GIT
    try:
        tier = ExtensionSourceTrustTier(trust_tier)
    except ValueError:
        raise ExtensionError(
            "invalid",
            f"source trust_tier must be one of "
            f"{[member.value for member in ExtensionSourceTrustTier]} — got {trust_tier!r}",
        ) from None
    ref_spec = json.dumps(document.get("ref") or {}, sort_keys=True)

    stmt = select(ExtensionSource).where(
        ExtensionSource.uri == uri,
        ExtensionSource.tenant_id.is_(None)
        if tenant_id is None
        else ExtensionSource.tenant_id == tenant_id,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    source = ExtensionSource(
        tenant_id=tenant_id,
        kind=kind,
        uri=uri[:2048],
        ref_spec=ref_spec[:256],
        auth_secret_ref=None,
        trust_tier=tier,
        enabled=True,
    )
    db.add(source)
    await db.flush()
    return source


def _content_root_for(source: ExtensionSource) -> Path:
    if source.kind == "file":
        return Path(source.uri)
    raise ExtensionError(
        "not-ready",
        f"{source.kind} source transport lands with the import pipeline's git lane — "
        "the source row and its pins persist; sync is honest about the gap",
    )


def _posture_payload(*, requested_tier: str, derived_tier: str) -> dict:
    """The DETECTION evidence row payload — never a constant (isolation.py
    D-39): the graded answer of THIS platform right now."""
    return {
        "schema_version": 1,
        "grade": isolation.posture(),
        "kernel_available": isolation.available(),
        "platform": sys.platform,
        "probed_from": "detection",
        "requested_tier": requested_tier,
        "derived_tier": derived_tier,
    }


async def upsert_posture_row(
    db: AsyncSession,
    version: ExtensionSourceVersion,
    *,
    requested_tier: str,
    derived_tier: str,
    host_profile: str = "health-assistant",
) -> ExtensionIsolationPosture:
    """Record (or refresh) the DETECTION posture evidence for one version
    on one host profile — the row the spawn seam and the host-profile
    surface read; never a constant."""
    payload = _posture_payload(requested_tier=requested_tier, derived_tier=derived_tier)
    stmt = select(ExtensionIsolationPosture).where(
        ExtensionIsolationPosture.version_id == version.id,
        ExtensionIsolationPosture.host_profile == host_profile,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        row = ExtensionIsolationPosture(
            version_id=version.id,
            host_profile=host_profile,
            requested_tier=ExtensionTier(requested_tier),
            derived_tier=ExtensionTier(derived_tier),
            posture=payload,
            probed_at=datetime.now(UTC),
        )
        db.add(row)
    else:
        row.posture = payload
        row.probed_at = datetime.now(UTC)
    return row


async def register_import(
    db: AsyncSession,
    *,
    source: ExtensionSource,
    manifest: dict,
    entry_dir: Path,
    content_digest: str,
    commit_sha: str | None = None,
    host_profile: str = "health-assistant",
) -> ExtensionSourceVersion:
    """The ONE registration decision, persisted (plan 7 W2).

    Runs ``contract.validate_registration`` with the source's trust tier as
    the caller claim, writes the IMMUTABLE version row (``pending``, or
    ``rejected`` with the typed problems in ``reason``) plus the isolation
    posture evidence. Idempotent per ``(source, digest)``: a re-import of
    identical content returns the existing row; a digest change is a NEW
    row — the previous approval never silently carries.
    """
    claimed_tier = (
        source.trust_tier.value
        if isinstance(source.trust_tier, ExtensionSourceTrustTier)
        else str(source.trust_tier)
    )
    problems = contract.validate_registration(
        manifest,
        schema=_manifest_schema(),
        bundled=False,
        profile=_profile(),
        tier=claimed_tier,
        docs_root=entry_dir,
        docs_schema=_docs_schema(),
        charter=_charter(),
    )
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()

    stmt = select(ExtensionSourceVersion).where(
        ExtensionSourceVersion.source_id == source.id,
        ExtensionSourceVersion.digest == content_digest,
    )
    version = (await db.execute(stmt)).scalar_one_or_none()
    derived_tier = contract.derive_tier(manifest, bundled=False)

    if problems:
        reason = "; ".join(problems)
        if version is not None:
            return version
        version = ExtensionSourceVersion(
            source_id=source.id,
            commit_sha=commit_sha,
            digest=content_digest,
            manifest_digest=manifest_digest,
            envelope=manifest,
            status=ExtensionVersionStatus.REJECTED,
            reason=reason,
            contract_version=_CONTRACT_VERSION,
        )
        db.add(version)
        await db.flush()
        return version

    if version is not None:
        await upsert_posture_row(
            db,
            version,
            requested_tier=claimed_tier,
            derived_tier=derived_tier,
            host_profile=host_profile,
        )
        return version
    version = ExtensionSourceVersion(
        source_id=source.id,
        commit_sha=commit_sha,
        digest=content_digest,
        manifest_digest=manifest_digest,
        envelope=manifest,
        status=ExtensionVersionStatus.PENDING,
        reason=None,
        contract_version=_CONTRACT_VERSION,
    )
    db.add(version)
    await db.flush()
    await upsert_posture_row(
        db,
        version,
        requested_tier=claimed_tier,
        derived_tier=derived_tier,
        host_profile=host_profile,
    )
    return version


async def sync_source(
    db: AsyncSession,
    source: ExtensionSource,
    *,
    content_override: Path | None = None,
) -> list[ExtensionSourceVersion]:
    """Resolve a source's content and register every manifest it carries.

    ``content_override`` is the dev/local lane: an already-resolved content
    tree (the local descriptor's path, containment re-checked). Git/registry
    sources refuse with the typed ``not-ready`` — pins stay untouched, the
    failure lands in ``last_error`` (the charter: a failed fetch leaves pins
    untouched and marks the source failing).
    """
    try:
        content_src = Path(content_override) if content_override else _content_root_for(source)
        if source.kind == "file":
            content_src = _check_local_root(str(content_src))
        tree = _materialize_content(content_src, str(source.id), digest_tree(content_src))
    except ExtensionError as exc:
        await record_check(db, source, error=exc.detail)
        raise

    manifests = discover_manifests(tree)
    if not manifests:
        await record_check(db, source, error="no manifest.json found in the resolved content")
        raise ExtensionError("not-found", "no manifest.json found in the resolved content")
    versions: list[ExtensionSourceVersion] = []
    for manifest in manifests:
        versions.append(
            await register_import(
                db,
                source=source,
                manifest=manifest,
                entry_dir=tree,
                content_digest=digest_tree(tree),
            )
        )
    await record_check(db, source)
    return versions


async def record_check(db: AsyncSession, source: ExtensionSource, *, error: str | None = None):
    """Fetch/registration bookkeeping on the source row — the pins and the
    version rows never move on a failure."""
    source.last_check_at = datetime.now(UTC)
    source.last_error = error


async def approve_version(
    db: AsyncSession, version: ExtensionSourceVersion, *, approved_by: Any
) -> ExtensionSourceVersion:
    """``pending → approved`` — the explicit human act. There is no
    auto-apply path at all for code (the charter's update law): approval
    never touches a running instance; the instance picks the digest up only
    through its own explicit apply against the approved row."""
    if version.status == ExtensionVersionStatus.APPROVED:
        return version
    if version.status != ExtensionVersionStatus.PENDING:
        raise ExtensionError(
            "invalid",
            f"a {version.status.value} version cannot be approved — fix the recorded "
            "reason and re-import (a digest change is a new row)",
        )
    version.status = ExtensionVersionStatus.APPROVED
    version.approved_by = approved_by
    return version


async def revoke_version(db: AsyncSession, version: ExtensionSourceVersion):
    """``approved → revoked`` — pulls the digest back from runnable."""
    if version.status != ExtensionVersionStatus.APPROVED:
        raise ExtensionError("invalid", "only an approved version can be revoked")
    version.status = ExtensionVersionStatus.REVOKED


async def active_version(
    db: AsyncSession, source: ExtensionSource
) -> ExtensionSourceVersion | None:
    """The approved digest an instance may run — the tampered/pending
    candidate is never this row (the running instance refuses auto-upgrade)."""
    stmt = (
        select(ExtensionSourceVersion)
        .where(
            ExtensionSourceVersion.source_id == source.id,
            ExtensionSourceVersion.status == ExtensionVersionStatus.APPROVED,
        )
        .order_by(ExtensionSourceVersion.first_seen_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def registration_failures() -> dict[str, str]:
    """The bundled registry's surfaced rejections (plan 37 6.1) — the same
    reasons the registry holds in memory, exposed for the admin surface so
    a rejected registration is a visible row-shaped answer, never only a
    log line."""
    from app.extensions.registry import integration_registry

    return integration_registry.get_rejected()
