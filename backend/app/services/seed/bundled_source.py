"""The bundled seed source — the trusted, auto-approved pack (ADR 0056 D9).

``backend/data/seeds`` is wrapped as the ``bundled`` source (priority 0,
system scope): the one origin that needs no admin approval — it ships with
the app and is reviewed in the repo. The boot path:

* ensures the ``seed_sources`` row exists (idempotent);
* digests the bundled pack (``load_bundled_pack`` — canonical, no
  timestamps) and upserts the ``seed_source_versions`` row for that digest
  (``UNIQUE(source_id, digest)`` — a digest change is a NEW version);
* auto-approves the version (trusted-by-definition; ``approved_by`` stays
  NULL — no admin exists on a fresh install and none is needed);
* applies through the applier **only when the digest changed** (or nothing
  has applied it yet), audited like every other apply;
* a fresh DB works before any admin exists (boot ordering unchanged).

The dev loop stays "edit ``data/seeds/*.json`` → restart": editing a seed
file changes the digest, the next boot applies the new version. In
production a failing boot apply keeps ``_abort_or_warn`` semantics (the
caller in ``main.py`` aborts startup); in development it warns and the app
boots.

The admin-facing approval lifecycle for non-bundled sources is the sources
phase (program Phase 10) — this module carries only the trusted lane.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.seed_pack.bundled import load_bundled_pack
from app.models.enums import SeedSourceKind, SeedSourceScope, SeedVersionStatus
from app.models.seed_source_model import (
    SeedImportRun,
    SeedRunMode,
    SeedRunStatus,
    SeedSource,
    SeedSourceVersion,
)
from app.services.seed.applier import PackApplication, SeedPackApplier

logger = logging.getLogger(__name__)

BUNDLED_SOURCE_URI = "app://bundled/seeds"
"""The stable identity of the bundled source (one row per install)."""


async def ensure_bundled_source(session: AsyncSession) -> SeedSource:
    """The bundled ``seed_sources`` row (idempotent; priority 0, system).

    Guarded by a transaction advisory lock so two workers booting at once
    cannot both insert the row (``(kind, uri)`` carries no unique index).
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"),
        {
            "k": int.from_bytes(hashlib.sha256(b"seed-bundled-ensure").digest()[:8], "big")
            & 0x7FFF_FFFF_FFFF_FFFF
        },
    )
    source = (
        await session.execute(
            select(SeedSource).where(
                SeedSource.kind == SeedSourceKind.BUNDLED,
                SeedSource.uri == BUNDLED_SOURCE_URI,
            )
        )
    ).scalar_one_or_none()
    if source is not None:
        return source
    source = SeedSource(
        kind=SeedSourceKind.BUNDLED,
        uri=BUNDLED_SOURCE_URI,
        priority=0,
        scope=SeedSourceScope.SYSTEM,
        enabled=True,
    )
    session.add(source)
    await session.flush()
    return source


async def ensure_approved_version(
    session: AsyncSession, source: SeedSource, digest: str, manifest: dict
) -> tuple[SeedSourceVersion, bool]:
    """The approved version row for a digest (created + auto-approved when new).

    Returns ``(version, created)``. An existing row is approved in place when
    it was still pending (the bundled pack never waits for an admin); an
    already-approved digest re-applies through the digest-changed check at
    the caller.
    """
    version = (
        await session.execute(
            select(SeedSourceVersion).where(
                SeedSourceVersion.source_id == source.id,
                SeedSourceVersion.digest == digest,
            )
        )
    ).scalar_one_or_none()
    if version is not None:
        if version.status is not SeedVersionStatus.APPROVED:
            version.status = SeedVersionStatus.APPROVED
            version.reason = "bundled auto-approve (trusted pack)"
            await session.flush()
        return version, False
    version = SeedSourceVersion(
        source_id=source.id,
        digest=digest,
        manifest=manifest,
        contract_version=str(manifest.get("schema_version", "1.0")),
        status=SeedVersionStatus.APPROVED,
        approved_at=None,  # trusted lane: no admin acted; approved_by stays NULL
        reason="bundled auto-approve (trusted pack)",
    )
    session.add(version)
    await session.flush()
    return version, True


async def bundled_version_applied(session: AsyncSession, version: SeedSourceVersion) -> bool:
    """Whether this exact version already has a successful apply run AND is
    the source's current pointer — the boot fast path (digest unchanged)."""
    await session.refresh(version)
    source = await session.get(SeedSource, version.source_id)
    applied = (
        await session.execute(
            select(SeedImportRun.id).where(
                SeedImportRun.source_version_id == version.id,
                SeedImportRun.mode == SeedRunMode.APPLY,
                SeedImportRun.status.in_((SeedRunStatus.OK, SeedRunStatus.PARTIAL)),
            )
        )
    ).first()
    return applied is not None and source is not None and source.current_version_id == version.id


async def latest_apply_run(session: AsyncSession, version_id) -> SeedImportRun | None:
    """The most recent apply run of a version (the boot's PARTIAL probe)."""
    return (
        await session.execute(
            select(SeedImportRun)
            .where(
                SeedImportRun.source_version_id == version_id,
                SeedImportRun.mode == SeedRunMode.APPLY,
            )
            .order_by(SeedImportRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def apply_bundled_on_boot(seeds_dir=None) -> dict | None:
    """The boot entry: apply the bundled pack when its digest changed.

    Returns the outcome stats (``None`` = digest unchanged, nothing to do).
    Opens its own sessions; the caller (``main.py``) keeps the
    ``_abort_or_warn`` failure policy. Fresh DB: the source + version are
    created here, then the full bundled apply runs — no admin required.
    """
    pack = load_bundled_pack(seeds_dir)
    async with AsyncSessionLocal() as session:
        source = await ensure_bundled_source(session)
        version, created = await ensure_approved_version(
            session, source, pack.digest, pack.manifest
        )
        await session.commit()
        if not created and await bundled_version_applied(session, version):
            # PARTIAL keeps the pointer (re-applying on every boot would turn
            # a permanent item error into a restart loop); the item-error
            # count is surfaced loudly instead. Phase 10 adds the explicit
            # re-apply door (sources admin) for retrying those items.
            run = await latest_apply_run(session, version.id)
            if run is not None and run.status is SeedRunStatus.PARTIAL:
                logger.warning(
                    "Bundled seed pack (digest %s…) last applied PARTIAL with %s item "
                    "error(s) — pointer kept, boot apply skipped; use the sources "
                    "re-apply door (Phase 10) to retry the failed items",
                    pack.digest[:12],
                    (run.stats or {}).get("errors", "unknown"),
                )
            else:
                logger.info(
                    "Bundled seed pack unchanged (digest %s…) — boot apply skipped",
                    pack.digest[:12],
                )
            return None
        logger.info(
            "Bundled seed pack %s (digest %s…) — applying",
            "created" if created else "changed/unapplied",
            pack.digest[:12],
        )
    outcome = await SeedPackApplier().apply(
        [PackApplication(source=source, version=version, pack=pack)]
    )
    stats: dict = {}
    for version_id, run_status in outcome.statuses.items():
        stats[str(version_id)] = run_status.value if run_status else None
    ok_statuses = (SeedRunStatus.OK, SeedRunStatus.PARTIAL)
    failed = [s for s in outcome.statuses.values() if s and s not in ok_statuses]
    if failed:
        raise RuntimeError(
            f"bundled seed apply ended {failed[0].value} — see seed_import_runs for the plan/error"
        )
    return stats
