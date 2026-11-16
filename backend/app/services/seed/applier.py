"""The seed pack applier — locked, staged, provenance-tracked apply (ADR 0056).

The applier turns planned pack versions into rows, under the lane's
concurrency and history laws:

* **Advisory lock** — a Postgres session-level advisory lock held for the
  whole apply: one **global** key for system applies, **per-source** keys for
  tenant applies (ADR 0056 assumption 10), acquired in sorted order so
  concurrent applies cannot deadlock. The lock lives on a dedicated
  connection (the session is bound to it) so the per-stage commits never
  release it; ``pg_advisory_unlock`` runs in ``finally``.
* **Run-status guard** — while the advisory lock is held, any ``running``
  run for the same sources is a crashed process (a live applier would hold
  the lock) and is superseded: marked ``failed`` with the reason. Without
  the lock a fresh ``running`` run refuses with ``ALREADY_RUNNING``; a
  stale one (older than the staleness window) is still superseded.
* **One transaction per pack stage** — a failed stage rolls back completely
  and marks its run ``failed``; the source's ``current_version_id`` is not
  advanced. Other packs in the same apply still run.
* **Priority-ascending, items before refs** — applications are ordered by
  source priority ascending; every pack's item/side stages run first (in
  declared stage order), then every pack's ref/edge stages (assumption 8).
* **Deferred references are first-class** — an unresolved cross-pack
  reference becomes a ``seed_deferred_refs`` row (deduplicated per version)
  and every pending row is retried on every apply; a ref that resolves flips
  to ``landed`` — never silently skipped (D5/D10).
* **History + provenance + audit** — every plan entry becomes a
  ``seed_import_run_items`` row (with the conflict payload of a loser or an
  override); applied items update ``seed_item_provenance`` (``user_modified``
  is never written here — the planner refuses frozen rows); the successful
  pointer advance is audited (fail-closed).
* **Retire / replaces** — retirement flips provenance to ``retired`` (and
  concept rows to ``status=retired``; the row is never deleted); a
  ``replaces`` decision repoints curated edges **in the pack's own lane**
  (system edges for a system pack, the tenant's edges for an overlay) to
  the replacement row and leaves patient ``*_catalog_id`` FKs untouched.
* **Search indexes** — the catalog search indexes are expression GIN indexes
  (trigram + FTS) maintained by Postgres on write; there is no separate
  vector to refresh — the stage commits keep them current by construction.

Retirement restore: re-applying a version whose items were retired flips
them back to ``active`` (concepts back to ``status=active``) — the rollback
path a revocation takes (the previous approved version is re-applied,
restoring what its successor retired).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, ContextAsyncSession, engine
from app.core.seed_pack.items import ParsedPack
from app.models.concept_model import Concept, ConceptEdge
from app.models.enums import (
    ConceptStatus,
    SeedDeferredStatus,
    SeedItemAction,
    SeedItemState,
    SeedRunMode,
    SeedRunStatus,
)
from app.models.seed_source_model import (
    SeedDeferredRef,
    SeedImportRun,
    SeedImportRunItem,
    SeedSource,
    SeedSourceVersion,
)
from app.services.audit.audit_service import log_audit_action
from app.services.seed.planner import (
    _KEY_TYPE_TO_ENTITY,
    REF_STAGES,
    ItemPlan,
    PackPlan,
    SeedPackPlanner,
)
from app.services.seed.provenance import (
    ENTITY_TYPE_MODELS,
    RESTORABLE_STATUS_MODELS,
    get_provenance,
    upsert_provenance,
)
from app.services.seed.service import SeedService

logger = logging.getLogger(__name__)

LOCK_STALENESS = timedelta(hours=6)
"""A ``running`` run older than this is crashed, not concurrent — superseded."""

APPLY_ACTIONS = frozenset({SeedItemAction.CREATE, SeedItemAction.UPDATE, SeedItemAction.OVERRIDE})
"""Actions that (may) write the row and move provenance."""

SIDE_STAGES = frozenset({"retention_policies", "proposal_kinds", "ai_task_defs", "anatomy_figures"})
"""Stages the bundled pack seeds from side inputs (never pack items)."""


class SeedApplyError(RuntimeError):
    """A typed apply refusal (nothing was written; the caller surfaces it)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class PackApplication:
    """One (source, version, parsed pack) triple in an apply."""

    source: SeedSource
    version: SeedSourceVersion
    pack: ParsedPack


@dataclass
class ApplyOutcome:
    """The multi-pack result: one run id + final status per version."""

    runs: dict[UUID, UUID] = field(default_factory=dict)
    statuses: dict[UUID, SeedRunStatus] = field(default_factory=dict)
    plan_docs: dict[UUID, dict] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.statuses) and all(
            s in (SeedRunStatus.OK, SeedRunStatus.PARTIAL) for s in self.statuses.values()
        )


def _lock_key(scope: str, identifier: str | None = None) -> int:
    """A stable signed-int64 advisory-lock key (sha256 → 63 bits)."""
    digest = hashlib.sha256(f"seed-apply:{scope}:{identifier or ''}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def _lock_keys(applications: Sequence[PackApplication]) -> list[int]:
    """The lock keys an apply holds: one global for system packs, per-source
    for tenant packs — acquired sorted so concurrent applies cannot deadlock."""
    keys: set[int] = set()
    for app in applications:
        if app.source.scope is not None and app.source.scope.value == "tenant":
            keys.add(_lock_key("tenant", str(app.source.id)))
        else:
            keys.add(_lock_key("system"))
    return sorted(keys)


def stage_order(pack: ParsedPack, *, bundled: bool) -> list[str]:
    """The apply's stage list for one pack: the declared SeedService order.

    The bundled pack runs every stage (the app-only vocabularies and the
    binary figures are side inputs only it carries); a non-bundled pack runs
    the stages its files declare, in the same declared order.
    """
    declared = SeedService._SEED_STAGE_NAMES
    if bundled:
        return list(declared)
    present = {parsed.stage for parsed in pack.files.values()}
    return [stage for stage in declared if stage in present]


async def _stage_runner(service: SeedService, session: AsyncSession, stage: str, pack: ParsedPack):
    """Dispatch one stage to its seeder (the ``seed_all`` dispatch, staged)."""
    if stage == "default_catalog":
        return await service.seed_default_catalog(session, pack.stage_data(stage))
    if stage in SIDE_STAGES:
        return await getattr(service, f"seed_{stage}")(session)
    return await getattr(service, f"seed_{stage}")(session, pack.stage_items(stage))


def _stage_item_key(stage: str, item: dict) -> str:
    """The plan-entry key of a stage item (mirrors the planner's derivation)."""
    from app.services.seed.planner import edge_seed_key, panel_seed_key

    if stage == "concept_edges":
        return edge_seed_key(item)
    if stage == "biomarker_panels":
        return panel_seed_key(item)
    return str(item.get("seed_key") or item.get("symbol") or item.get("slug") or "")


async def _run_planned_stage(service: SeedService, session: AsyncSession, ar, stage: str):
    """Run one stage with the plan's filter: only entries whose action writes
    (create/update/override) reach the seeder — SKIP/CONFLICT/NOOP items never
    touch the rows (the freeze, the priority losers and the equal-priority
    conflicts are enforced here, not left to the seeder)."""
    entries = ar.plan.stages.get(stage, [])
    pack = ar.app.pack
    if not entries:
        return await _stage_runner(service, session, stage, pack)
    allowed = {e.seed_key for e in entries if e.action in APPLY_ACTIONS}
    if stage == "default_catalog":
        filtered: dict[str, list[dict]] = {}
        for collection, rows in pack.stage_data(stage).items():
            filtered[collection] = [row for row in rows if _stage_item_key(stage, row) in allowed]
        return await service.seed_default_catalog(session, filtered)
    items = [i for i in pack.stage_items(stage) if _stage_item_key(stage, i) in allowed]
    return await getattr(service, f"seed_{stage}")(session, items)


async def _retry_runner(service: SeedService, session: AsyncSession, stage: str, item: dict):
    """Re-run one deferred item through its stage's seeder (idempotent)."""
    if stage == "default_catalog":
        collection = "units" if "symbol" in item else "biomarkers"
        return await service.seed_default_catalog(session, {collection: [item]})
    return await getattr(service, f"seed_{stage}")(session, [item])


@dataclass
class _AppRun:
    """Per-application bookkeeping across the apply's phases."""

    app: PackApplication
    plan: PackPlan
    run: SeedImportRun
    errors: int = 0
    deferred_landed: int = 0

    @property
    def version_id(self) -> UUID:
        return self.app.version.id

    @property
    def active(self) -> bool:
        return self.run.status in (SeedRunStatus.RUNNING, SeedRunStatus.PARTIAL)

    def fail(self, error: str) -> None:
        self.run.status = SeedRunStatus.FAILED
        self.run.error = error
        self.run.finished_at = datetime.now(UTC)


class SeedPackApplier:
    """Apply planned pack versions under the lane's locks, transactions and
    history contracts."""

    def __init__(self, session_factory=AsyncSessionLocal):
        self._session_factory = session_factory

    async def apply(self, applications: Sequence[PackApplication]) -> ApplyOutcome:
        """Plan + apply the given applications (priority-ascending, items
        before refs, one transaction per stage).

        Every version must already be ``approved`` (the bundled boot path
        auto-approves its version first — trusted-by-definition, ADR 0056 D9).
        """
        if not applications:
            return ApplyOutcome()
        for app in applications:
            if app.version.status is None or app.version.status.value != "approved":
                raise SeedApplyError(
                    "APPLY_NOT_APPROVED",
                    f"version {app.version.id} is not approved "
                    f"(status={getattr(app.version.status, 'value', app.version.status)})",
                )
        if engine is None:
            raise SeedApplyError("DATABASE_UNAVAILABLE", "the database engine is not initialized")
        ordered = sorted(applications, key=lambda a: (a.source.priority or 0, str(a.source.id)))
        outcome = ApplyOutcome()
        async with engine.connect() as conn:
            # ContextAsyncSession (not a bare AsyncSession): the locked
            # apply bypasses async_sessionmaker, so the RLS hook must ride
            # the session class itself — otherwise the apply transaction
            # runs with no planted context and writes nothing under RLS
            # (app.core.boot applies the bundled pack inside the boot
            # system scope; the GUCs themselves are planted here).
            session = ContextAsyncSession(bind=conn, expire_on_commit=False)
            keys = _lock_keys(ordered)
            try:
                for key in keys:
                    await session.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
                try:
                    await self._apply_locked(session, ordered, outcome)
                except Exception:
                    await session.rollback()
                    raise
            finally:
                for key in reversed(keys):
                    try:
                        await session.rollback()
                        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                    except Exception:
                        pass
                await session.close()
        return outcome

    async def plan_only(self, application: PackApplication) -> PackPlan:
        """The read-only plan run (the API preview): stores the plan doc as a
        ``plan``-mode run; writes nothing else."""
        async with self._session_factory() as session:
            planner = SeedPackPlanner(
                session, application.source, application.version, application.pack
            )
            plan = await planner.plan()
            session.add(
                SeedImportRun(
                    source_version_id=application.version.id,
                    mode=SeedRunMode.PLAN,
                    status=SeedRunStatus.CONFLICTED if plan.conflicts else SeedRunStatus.OK,
                    plan=plan.to_doc(),
                    stats=plan.summary(),
                )
            )
            await session.commit()
            return plan

    # -- the locked apply ---------------------------------------------------

    async def _apply_locked(
        self,
        session: AsyncSession,
        ordered: list[PackApplication],
        outcome: ApplyOutcome,
    ) -> None:
        await self._enforce_run_guard(session, ordered)
        runs = await self._open_runs(session, ordered, outcome)
        await session.commit()

        for ar in runs:
            if ar.plan.conflicts:
                logger.warning(
                    "seed apply conflicted for version %s: %d conflicts",
                    ar.version_id,
                    len(ar.plan.conflicts),
                )
                continue
            await self._apply_items(session, ar)
        for ar in runs:
            if ar.active:
                await self._apply_refs(session, ar)
        for ar in runs:
            if ar.active:
                await self._retry_deferred(session, ar)
        for ar in runs:
            if ar.active:
                await self._apply_retires(session, ar)
        for ar in runs:
            if ar.active:
                await self._finalize(session, ar)
        for ar in runs:
            outcome.statuses[ar.version_id] = ar.run.status

    async def _enforce_run_guard(
        self, session: AsyncSession, ordered: list[PackApplication], *, locked: bool = True
    ) -> None:
        """Supersede ``running`` runs for the target sources, or refuse when
        no lock is held.

        Holding the per-source advisory lock proves no live applier — any
        ``running`` run (fresh or stale) is a crashed process and is
        superseded (marked ``failed`` with the reason; without this a crash
        inside the staleness window would wedge every later apply, notably
        the boot apply, until the window expires). Without the lock a fresh
        ``running`` run may be a genuinely concurrent apply →
        ``ALREADY_RUNNING``; a stale one is still superseded (older than the
        window = crashed, never concurrent).
        """
        source_ids = [app.source.id for app in ordered]
        now = datetime.now(UTC)
        cutoff = now - LOCK_STALENESS
        running = (
            (
                await session.execute(
                    select(SeedImportRun)
                    .join(
                        SeedSourceVersion, SeedSourceVersion.id == SeedImportRun.source_version_id
                    )
                    .where(
                        SeedSourceVersion.source_id.in_(source_ids),
                        SeedImportRun.status == SeedRunStatus.RUNNING,
                    )
                )
            )
            .scalars()
            .all()
        )
        for run in running:
            if locked or run.started_at < cutoff:
                run.status = SeedRunStatus.FAILED
                run.error = (
                    "superseded: running apply under the held advisory lock (crashed apply?)"
                    if locked
                    else "superseded: stale running run (crashed apply?)"
                )
                run.finished_at = now
        if not locked and any(run.started_at >= cutoff for run in running):
            raise SeedApplyError("ALREADY_RUNNING", "an apply for this source is already in flight")

    async def _open_runs(
        self,
        session: AsyncSession,
        ordered: list[PackApplication],
        outcome: ApplyOutcome,
    ) -> list[_AppRun]:
        """Plan every application and open its apply run (conflicts pre-marked)."""
        runs: list[_AppRun] = []
        for app in ordered:
            planner = SeedPackPlanner(session, app.source, app.version, app.pack)
            plan = await planner.plan()
            run = SeedImportRun(
                source_version_id=app.version.id,
                mode=SeedRunMode.APPLY,
                status=SeedRunStatus.CONFLICTED if plan.conflicts else SeedRunStatus.RUNNING,
                plan=plan.to_doc(),
                stats=plan.summary(),
            )
            session.add(run)
            runs.append(_AppRun(app=app, plan=plan, run=run))
            outcome.plan_docs[app.version.id] = run.plan
        await session.flush()  # populate run ids
        for ar in runs:
            outcome.runs[ar.version_id] = ar.run.id
            outcome.statuses[ar.version_id] = ar.run.status
        return runs

    async def _apply_items(self, session: AsyncSession, ar: _AppRun) -> None:
        """Every item/side stage of one pack, one transaction per stage."""
        service = SeedService(tenant_id=ar.app.source.tenant_id)
        stages = [s for s in stage_order(ar.app.pack, bundled=True) if s not in REF_STAGES]
        stage_stats: dict[str, dict] = {}
        stage: str | None = None
        try:
            for stage in stages:
                if not self._stage_runs(stage, ar):
                    continue
                service.item_errors.clear()
                service.unresolved_refs.clear()
                stats = await _run_planned_stage(service, session, ar, stage)
                stage_stats[stage] = stats
                await self._record_stage(session, ar, stage, service, stats)
                await session.commit()
        except Exception as exc:
            await session.rollback()
            ar.fail(f"item:{stage}: {exc}")
            await session.commit()
            logger.error("seed apply stage failed (version %s): %s", ar.version_id, exc)
            return
        ar.run.stats = {**ar.run.stats, "stage_stats": stage_stats}

    async def _apply_refs(self, session: AsyncSession, ar: _AppRun) -> None:
        """The ref/edge stages — after EVERY pack's items (assumption 8)."""
        service = SeedService(tenant_id=ar.app.source.tenant_id)
        stages = [s for s in stage_order(ar.app.pack, bundled=True) if s in REF_STAGES]
        stage: str | None = None
        try:
            for stage in stages:
                if not self._stage_runs(stage, ar):
                    continue
                service.item_errors.clear()
                service.unresolved_refs.clear()
                stats = await _run_planned_stage(service, session, ar, stage)
                await self._record_stage(session, ar, stage, service, stats)
                await session.commit()
        except Exception as exc:
            await session.rollback()
            ar.fail(f"refs:{stage}: {exc}")
            await session.commit()
            logger.error("seed apply ref stage failed (version %s): %s", ar.version_id, exc)

    @staticmethod
    def _stage_runs(stage: str, ar: _AppRun) -> bool:
        """Whether one stage runs for this application: the bundled pack runs
        every stage (its side stages seed app-only inputs); a non-bundled
        pack runs only the stages its files declare — side stages are never
        implicitly included (the side inputs are app-owned, not pack data)."""
        bundled = ar.app.source.kind is not None and ar.app.source.kind.value == "bundled"
        present = {p.stage for p in ar.app.pack.files.values()}
        return bundled or stage in present

    async def _record_stage(
        self,
        session: AsyncSession,
        ar: _AppRun,
        stage: str,
        service: SeedService,
        stats: dict,
    ) -> None:
        """Run items + provenance + deferred refs for one finished stage."""
        entries = ar.plan.stages.get(stage, [])
        by_key = {e.seed_key: e for e in entries}
        for entry in entries:
            session.add(
                SeedImportRunItem(
                    run_id=ar.run.id,
                    entity_type=entry.entity_type,
                    entity_id=entry.entity_id,
                    seed_key=entry.seed_key,
                    action=entry.action,
                    content_hash=entry.content_hash,
                    conflict=entry.conflict,
                )
            )
            await self._apply_entry_provenance(session, ar.app, entry)
        for error in service.item_errors:
            entry = by_key.get(error.get("seed_key") or "")
            ar.errors += 1
            session.add(
                SeedImportRunItem(
                    run_id=ar.run.id,
                    entity_type=entry.entity_type if entry else stage,
                    entity_id=entry.entity_id if entry else None,
                    seed_key=error.get("seed_key"),
                    action=SeedItemAction.SKIP,
                    content_hash=entry.content_hash if entry else None,
                    conflict={"reason": "error", "error": error.get("error")},
                )
            )
        for note in service.unresolved_refs:
            await self._defer_ref(session, ar.app, note)
        await session.flush()

    async def _apply_entry_provenance(
        self, session: AsyncSession, app: PackApplication, entry: ItemPlan
    ) -> None:
        """Provenance + retirement-restore for one plan entry.

        ``user_modified`` is never written here: frozen rows plan as SKIP and
        never reach this call (the applier's writes leave the flag false).
        """
        if entry.entity_id is None or entry.action in (
            SeedItemAction.SKIP,
            SeedItemAction.CONFLICT,
        ):
            return
        prov = await get_provenance(session, entry.entity_type, entry.entity_id)
        was_retired = prov is not None and prov.state == SeedItemState.RETIRED
        writes = entry.action in APPLY_ACTIONS
        adopts = entry.action is SeedItemAction.NOOP and (prov is None or was_retired)
        if writes or adopts:
            await upsert_provenance(
                session,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                seed_key=entry.seed_key,
                source_version_id=app.version.id,
                content_hash=entry.content_hash,
                state=SeedItemState.ACTIVE,
            )
        if was_retired:
            await _restore_entity(session, entry.entity_type, entry.entity_id)

    async def _defer_ref(self, session: AsyncSession, app: PackApplication, note: dict) -> None:
        """Persist one unresolved reference (deduplicated per version + key)."""
        payload = {
            "stage": note.get("stage"),
            "seed_key": note.get("seed_key"),
            "item": note.get("item"),
            "ref": note.get("ref"),
        }
        existing = (
            await session.execute(
                select(SeedDeferredRef).where(
                    SeedDeferredRef.source_version_id == app.version.id,
                    SeedDeferredRef.status == SeedDeferredStatus.PENDING,
                    SeedDeferredRef.payload["stage"].astext == str(payload["stage"]),
                    SeedDeferredRef.payload["seed_key"].astext == str(payload["seed_key"]),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.payload = payload
            return
        session.add(SeedDeferredRef(source_version_id=app.version.id, payload=payload))

    async def _retry_deferred(self, session: AsyncSession, ar: _AppRun) -> None:
        """Retry every pending deferred ref **of the apply's own scope** —
        never silently skipped; a ref that now resolves flips to ``landed``.

        Scope-pinned (ADR 0056 tenant packs): a tenant apply retries the
        refs of its tenant's sources, a system apply the system-scope refs —
        retrying another scope's item would seed it into the wrong lane.
        """
        tenant = ar.app.source.tenant_id
        tenant_filter = (
            SeedSource.tenant_id.is_(None) if tenant is None else SeedSource.tenant_id == tenant
        )
        pending = (
            (
                await session.execute(
                    select(SeedDeferredRef)
                    .join(
                        SeedSourceVersion, SeedSourceVersion.id == SeedDeferredRef.source_version_id
                    )
                    .join(SeedSource, SeedSource.id == SeedSourceVersion.source_id)
                    .where(SeedDeferredRef.status == SeedDeferredStatus.PENDING, tenant_filter)
                )
            )
            .scalars()
            .all()
        )
        if not pending:
            return
        service = SeedService(tenant_id=tenant)
        for ref in pending:
            payload = ref.payload or {}
            stage = payload.get("stage")
            item = payload.get("item")
            if not stage or not isinstance(item, dict):
                ref.status = SeedDeferredStatus.ABANDONED
                ref.last_error = "payload has no retryable item"
                continue
            service.unresolved_refs.clear()
            service.item_errors.clear()
            try:
                await _retry_runner(service, session, stage, item)
                still_unresolved = [
                    n
                    for n in service.unresolved_refs
                    if n.get("seed_key") == payload.get("seed_key")
                ]
                await session.commit()
                ref = await session.get(SeedDeferredRef, ref.id)
                ref.attempts += 1
                if still_unresolved:
                    ref.last_error = "; ".join(str(n.get("ref")) for n in still_unresolved)
                else:
                    ref.status = SeedDeferredStatus.LANDED
                    ar.deferred_landed += 1
                await session.commit()
            except Exception as exc:
                await session.rollback()
                ref = await session.get(SeedDeferredRef, ref.id)
                ref.attempts += 1
                ref.last_error = str(exc)
                await session.commit()
        ar.run.stats = {
            **ar.run.stats,
            "deferred_landed": ar.deferred_landed,
            "deferred_pending_left": await self._pending_count(session),
        }

    @staticmethod
    async def _pending_count(session: AsyncSession) -> int:
        return (
            await session.execute(
                select(func.count())
                .select_from(SeedDeferredRef)
                .where(SeedDeferredRef.status == SeedDeferredStatus.PENDING)
            )
        ).scalar_one()

    async def _apply_retires(self, session: AsyncSession, ar: _AppRun) -> None:
        """Retire dropped items and execute ``replaces`` repoints."""
        try:
            for retire in ar.plan.retires:
                if retire.action is not SeedItemAction.RETIRE:
                    session.add(
                        SeedImportRunItem(
                            run_id=ar.run.id,
                            entity_type=retire.entity_type,
                            entity_id=retire.entity_id,
                            seed_key=retire.seed_key,
                            action=retire.action,
                            conflict={"reason": retire.reason},
                        )
                    )
                    continue
                if retire.entity_id is not None:
                    prov = await get_provenance(session, retire.entity_type, retire.entity_id)
                    if prov is not None:
                        prov.state = SeedItemState.RETIRED
                    await _retire_entity(session, retire.entity_type, retire.entity_id)
                session.add(
                    SeedImportRunItem(
                        run_id=ar.run.id,
                        entity_type=retire.entity_type,
                        entity_id=retire.entity_id,
                        seed_key=retire.seed_key,
                        action=SeedItemAction.RETIRE,
                        conflict={"reason": retire.reason},
                    )
                )
                if retire.repoint and retire.replaces_new_key:
                    await self._repoint_edges(session, retire, tenant_id=ar.app.source.tenant_id)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            ar.fail(f"retire: {exc}")
            await session.commit()
            logger.error("seed retire phase failed (version %s): %s", ar.version_id, exc)

    async def _repoint_edges(
        self, session: AsyncSession, retire, *, tenant_id: UUID | None = None
    ) -> None:
        """Repoint curated edges from the replaced row to the new one.

        The edges follow the pack's own lane: a system pack repoints the
        system edges (``tenant_id IS NULL``), a tenant overlay repoints its
        tenant's edges — each lane rewrites only what it seeded, so a tenant
        ``replaces`` never touches the system graph (and vice versa).
        Patient ``*_catalog_id`` FKs are deliberately untouched (ADR 0056 D5).
        """
        new_entity_type = _KEY_TYPE_TO_ENTITY.get(
            retire.replaces_new_key.split(":", 1)[0], retire.entity_type
        )
        new_model = ENTITY_TYPE_MODELS.get(new_entity_type)
        if new_model is None:
            return
        from app.services.seed.identity import find_identity_row

        new_row = await find_identity_row(
            session, new_model, retire.replaces_new_key, tenant_id=tenant_id
        )
        if new_row is None:
            logger.warning(
                "replaces target %s not found in the pack's lane; edges not repointed",
                retire.replaces_new_key,
            )
            return
        edge_tenant = (
            ConceptEdge.tenant_id.is_(None)
            if tenant_id is None
            else ConceptEdge.tenant_id == tenant_id
        )
        await session.execute(
            update(ConceptEdge)
            .where(ConceptEdge.src_id == retire.entity_id, edge_tenant)
            .values(src_id=new_row.id)
        )
        await session.execute(
            update(ConceptEdge)
            .where(ConceptEdge.dst_id == retire.entity_id, edge_tenant)
            .values(dst_id=new_row.id)
        )

    async def _finalize(self, session: AsyncSession, ar: _AppRun) -> None:
        """Advance the approved pointer, close the run, audit (fail-closed)."""
        ar.run.status = SeedRunStatus.PARTIAL if ar.errors else SeedRunStatus.OK
        ar.run.stats = {**ar.run.stats, "summary": ar.plan.summary(), "errors": ar.errors}
        ar.run.finished_at = datetime.now(UTC)
        try:
            await session.execute(
                update(SeedSource)
                .where(SeedSource.id == ar.app.source.id)
                .values(current_version_id=ar.app.version.id)
            )
            await log_audit_action(
                tenant_id=ar.app.source.tenant_id,
                user_id=None,
                action="seed.apply",
                resource_type="seed_source_version",
                resource_id=ar.app.version.id,
                outcome="ok",
                new_value={
                    "pack_id": ar.plan.pack_id,
                    "pack_version": ar.plan.pack_version,
                    "digest": ar.plan.digest,
                    "source_id": str(ar.app.source.id),
                    "priority": ar.plan.priority,
                    "summary": ar.plan.summary(),
                },
                required=True,
                session=session,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            ar.fail(f"finalize: {exc}")
            await session.commit()
            raise SeedApplyError("FINALIZE_FAILED", f"pointer advance/audit failed: {exc}") from exc


async def apply_applications(applications: Sequence[PackApplication]) -> ApplyOutcome:
    """The module-level convenience entry (one applier, one locked apply)."""
    return await SeedPackApplier().apply(applications)


async def _retire_entity(session: AsyncSession, entity_type: str, entity_id: UUID) -> None:
    """Flip a retired row's own lifecycle marker (concept status only; the
    row itself is never deleted — retire, never delete)."""
    if entity_type == "concept":
        await session.execute(
            update(Concept).where(Concept.id == entity_id).values(status=ConceptStatus.RETIRED)
        )


async def _restore_entity(session: AsyncSession, entity_type: str, entity_id: UUID) -> None:
    """Flip a previously-retired row back to active (the rollback path)."""
    if RESTORABLE_STATUS_MODELS.get(entity_type) is not None and entity_type == "concept":
        await session.execute(
            update(Concept).where(Concept.id == entity_id).values(status=ConceptStatus.ACTIVE)
        )
