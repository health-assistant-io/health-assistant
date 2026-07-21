"""Integration sync helper.

Centralizes the FHIR/telemetry split logic that lives at the boundary of:

  - background task ``sync_active_integrations``
  - manual sync endpoint at ``POST /integrations/{id}/sync``
  - webhook delivery endpoint
  - bridge provider

The split is keyed on ``BiomarkerDefinition.is_telemetry``: Observations
linked to a telemetry-flagged biomarker are routed to the TimescaleDB
hypertable (``telemetry_data``); the rest are persisted as FHIR rows.

``run_sync`` consolidates the entire pull → convert → map → split → push →
log pipeline that was previously copy-pasted across the worker, the manual-
sync endpoint, the webhook path, and the bridge. Both the worker and the
manual endpoint now delegate here so the error contract, sync-log shape,
and cursor management stay in lockstep.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir import Observation
from app.models.telemetry_model import TelemetryDataModel
from app.services.fhir_helpers import coerce_patient_id
from app.core.converters import utcnow as _now

logger = logging.getLogger(__name__)

_SYNC_LOCK_TTL = 600  # seconds — matches the worker's original Redis lock expiry

# Cap on how many catalog proposals a single sync will apply. Excess items
# are dropped with a warning so a runaway provider can't spam the catalog.
# Cross-cutting env-var-ification is deferred (see parent plan §D.3).
INTEGRATION_MAX_PROPOSALS_PER_SYNC = 50

# Cap on how many HITL proposals a single sync will queue. Excess items
# are dropped with a warning so a runaway provider can't spam the inbox.
INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC = 20

# Caps on integration-pulled documents (workstream C). Item count protects
# the catalog from a runaway provider; byte cap protects RAM. Both are
# enforced in run_sync before ingest_document_bytes is called.
INTEGRATION_MAX_DOCS_PER_SYNC = 20
INTEGRATION_MAX_DOC_BYTES_PER_SYNC = 50 * 1024 * 1024  # 50 MiB


def _opt_in(provider: Any, hook_name: str) -> bool:
    """Generic capability probe for opt-in provider hooks.

    Returns the boolean result of calling ``provider.<hook_name>()`` if the
    method exists; ``False`` otherwise. Centralizes the ``getattr`` /
    callable-check dance so the engine can grow new opt-in hooks
    (``supports_clinical_events``, future ``supports_examinations``,
    ``supports_documents`` ...) without each call site re-implementing it.
    """
    fn = getattr(provider, hook_name, None)
    if not callable(fn):
        return False
    try:
        return bool(fn())
    except Exception:
        logger.warning(
            "provider %s.%s raised; treating as not-supported",
            type(provider).__name__, hook_name,
            exc_info=True,
        )
        return False


def _obs_value(obs: Observation) -> Optional[float]:
    """Best-effort numeric extraction for telemetry column mapping."""
    val = getattr(obs, "normalized_value", None)
    if val is None:
        val = getattr(obs, "raw_value", None)
    if val is None and obs.value_quantity:
        val = obs.value_quantity.get("value")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


async def fetch_biomarker_definitions(
    db: AsyncSession, observations: List[Observation]
) -> dict:
    """Bulk-load the BiomarkerDefinition rows referenced by ``observations``.

    Returns a dict ``{biomarker_id: BiomarkerDefinition}``.
    """
    b_ids = list({obs.biomarker_id for obs in observations if obs.biomarker_id})
    if not b_ids:
        return {}
    result = await db.execute(
        select(BiomarkerDefinition).where(BiomarkerDefinition.id.in_(b_ids))
    )
    return {b.id: b for b in result.scalars().all()}


async def apply_telemetry_split(
    db: AsyncSession,
    observations: List[Observation],
    tenant_id: UUID | str | None,
    instance_name: Optional[str],
    provider_name: str,
    integration_id: Optional[UUID | str] = None,
) -> Tuple[List[TelemetryDataModel], List[Observation]]:
    """Apply the FHIR/telemetry split in-memory and queue both row types on ``db``.

    Returns ``(telemetry_records, fhir_records)``. The caller is responsible
    for committing ``db`` once both batches are added.
    """
    if not observations:
        return [], []

    b_defs_map = await fetch_biomarker_definitions(db, observations)

    telemetry_records: List[TelemetryDataModel] = []
    fhir_records: List[Observation] = []

    device_id = instance_name or provider_name

    for obs in observations:
        is_telemetry = False
        if obs.biomarker_id and obs.biomarker_id in b_defs_map:
            is_telemetry = bool(b_defs_map[obs.biomarker_id].is_telemetry)

        if is_telemetry:
            b_def = b_defs_map[obs.biomarker_id]
            slug = (b_def.slug or "").lower()
            value = _obs_value(obs)

            hr = steps = cal = None
            data_payload: dict = {}

            if "8867-4" in slug or "heart-rate" in slug or slug == "heart_rate":
                hr = value
            elif "41950-7" in slug or "steps" in slug:
                steps = value
            elif "calories" in slug:
                cal = value
            else:
                data_payload[slug] = value
                if obs.value_quantity:
                    data_payload[f"{slug}_unit"] = obs.value_quantity.get("unit", "")

            telemetry_records.append(
                TelemetryDataModel(
                    tenant_id=tenant_id,
                    device_id=device_id,
                    timestamp=obs.effective_datetime,
                    heart_rate=hr,
                    steps=steps,
                    calories=cal,
                    data=data_payload if data_payload else None,
                )
            )
        else:
            if not obs.performer:
                reference = f"Integration/{integration_id}" if integration_id else None
                performer = {
                    "type": "Integration",
                    "display": device_id,
                }
                if reference:
                    performer["reference"] = reference
                obs.performer = [performer]
            fhir_records.append(obs)

    if telemetry_records:
        db.add_all(telemetry_records)
    if fhir_records:
        db.add_all(fhir_records)

    return telemetry_records, fhir_records


# --------------------------------------------------------------------------- #
#  Consolidated sync pipeline                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class SyncResult:
    """Structured result of one integration sync run.

    The caller (worker or manual-sync endpoint) inspects ``status`` and
    ``error_type`` to decide how to surface the outcome (log + continue for
    the worker; raise an HTTPException for the endpoint).
    """

    pulled: int = 0
    fhir_persisted: int = 0
    telemetry_persisted: int = 0
    dropped_invalid: int = 0
    pushed: Optional[Dict[str, Any]] = None
    status: str = "success"  # "success" | "partial" | "failed" | "skipped"
    error: Optional[str] = None
    error_type: Optional[str] = None  # "auth" | "rate_limit" | "data" | None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    proposals_pulled: int = 0
    proposals_applied: int = 0
    hitl_proposals_pulled: int = 0
    hitl_proposals_inserted: int = 0
    documents_pulled: int = 0
    documents_written: int = 0
    # Carried from ``IntegrationRateLimitError.retry_after_seconds`` when
    # the upstream sent a ``Retry-After`` header. The caller (worker)
    # may use this to write a Redis cooldown key so the next beat skips
    # this integration until the cooldown expires. ``None`` when the
    # upstream sent no hint (caller falls back to ``sync_interval``).
    retry_after_seconds: Optional[float] = None


async def _try_acquire_lock(integration_id: UUID) -> Tuple[bool, str]:
    """Acquire the Redis dedup lock for one integration.

    Returns ``(acquired, lock_key)``. Degrades gracefully when Redis is down
    (returns ``(True, key)`` so the sync proceeds — matching the historical
    worker behaviour) but logs a warning.
    """
    lock_key = f"sync_lock:{integration_id}"
    try:
        from app.core.redis import redis_client

        acquired = await redis_client.set(lock_key, "1", nx=True, ex=_SYNC_LOCK_TTL)
        if acquired is None:
            acquired = False
        return bool(acquired), lock_key
    except Exception:
        logger.warning(
            "Redis unavailable for sync lock on %s — proceeding without dedup "
            "(duplicate writes possible under overlapping beats)",
            integration_id,
        )
        return True, lock_key


# ---------------------------------------------------------------------------
# Rate-limit cooldown (item 1 of the integrations-sdk-improvements plan).
#
# When an upstream returns 429 with a ``Retry-After`` header, the SDK
# surfaces the value on ``IntegrationRateLimitError.retry_after_seconds``.
# ``run_sync`` copies it onto ``SyncResult.retry_after_seconds``; the
# worker then calls ``set_rate_limit_cooldown`` so subsequent beats
# skip the integration until the cooldown expires (instead of
# re-hitting the upstream every 60 s while the window is still closed).
# ---------------------------------------------------------------------------

#: Minimum cooldown we'll honour even if the upstream said less. Protects
#: against an upstream sending ``Retry-After: 0`` (which would effectively
#: disable the cooldown and let the worker re-stampede).
_COOLDOWN_MIN_SECONDS = 60

#: Maximum cooldown we'll honour even if the upstream said more. Protects
#: against an upstream lying (or a misconfigured proxy) and freezing the
#: integration out for hours. The worker's per-instance ``sync_interval``
#: takes over once the cooldown expires.
_COOLDOWN_MAX_SECONDS = 60 * 60


def _cooldown_key(integration_id: UUID) -> str:
    return f"sync_cooldown:{integration_id}"


def _clamp_cooldown(seconds: Optional[float]) -> Optional[int]:
    """Clamp the upstream hint to a sane window. Returns ``None`` for
    empty / non-positive values (no cooldown, fall back to ``sync_interval``).
    """
    if seconds is None:
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return int(max(_COOLDOWN_MIN_SECONDS, min(value, _COOLDOWN_MAX_SECONDS)))


async def set_rate_limit_cooldown(integration_id: UUID, retry_after_seconds: Optional[float]) -> None:
    """Write the rate-limit cooldown key for this integration.

    Degrades gracefully when Redis is unavailable (logs + returns) — the
    worker falls back to the per-instance ``sync_interval`` throttle,
    matching the pre-cooldown behaviour.
    """
    ttl = _clamp_cooldown(retry_after_seconds)
    if ttl is None:
        return
    try:
        from app.core.redis import redis_client

        await redis_client.set(_cooldown_key(integration_id), "1", ex=ttl)
        logger.info(
            "Rate-limit cooldown set for %s: %ds", integration_id, ttl,
        )
    except Exception:
        logger.warning(
            "Redis unavailable for rate-limit cooldown on %s — "
            "falling back to sync_interval throttle",
            integration_id,
        )


async def is_rate_limited(integration_id: UUID) -> bool:
    """Is this integration currently in a rate-limit cooldown?

    Returns ``False`` when Redis is unavailable (degrades to "not
    rate limited" so the sync proceeds; the upstream will re-raise
    ``IntegrationRateLimitError`` if still closed, and the cooldown
    will be re-set).
    """
    try:
        from app.core.redis import redis_client

        return bool(await redis_client.exists(_cooldown_key(integration_id)))
    except Exception:
        return False


async def clear_rate_limit_cooldown(integration_id: UUID) -> None:
    """Clear the cooldown key. Useful when a manual sync should bypass
    the upstream's last ``Retry-After`` hint (e.g. the user clicked
    "Sync now" in the UI)."""
    try:
        from app.core.redis import redis_client

        await redis_client.delete(_cooldown_key(integration_id))
    except Exception:
        pass


async def _release_lock(lock_key: str) -> None:
    try:
        from app.core.redis import redis_client

        await redis_client.delete(lock_key)
    except Exception:
        pass  # TTL is the crash-recovery fallback


async def _notify_sync_outcome(integration: Any, result: SyncResult) -> None:
    """Surface sync results to the integration owner (best-effort).

    * New data arrived  → INTEGRATION_EVENT (info).
    * Auth/data failure → SYNC_FAILURE (warning) to owner + tenant admins.

    Rate-limit and skip outcomes are silent (transient / no action needed).
    """
    try:
        from app.models.enums import (
            NotificationCategory,
            NotificationSeverity,
            NotificationSource,
            NotificationType,
            RecipientKind,
            Role,
        )
        from app.services.notification_service import emit
        from sqlalchemy import select
        from app.models.user_model import UserModel

        tenant_id = integration.tenant_id
        targets: list[dict] = [
            {"kind": RecipientKind.USER.value, "id": str(integration.user_id)}
        ]

        is_failure = result.status == "failed" and result.error_type in ("auth", "data")
        total_new = result.fhir_persisted + result.telemetry_persisted
        if not is_failure and total_new == 0:
            return  # nothing arrived and nothing failed — stay silent

        if is_failure:
            severity = NotificationSeverity.WARNING
            ntype = NotificationType.SYNC_FAILURE
            title = f"{integration.provider} sync failed"
            body = (
                result.error
                or "The integration sync failed and may need re-authorization."
            )
            async with AsyncSessionLocal() as session:
                admin_ids = [
                    row[0]
                    for row in (
                        await session.execute(
                            select(UserModel.id).where(
                                UserModel.tenant_id == tenant_id,
                                UserModel.role.in_(
                                    [
                                        Role.ADMIN.value,
                                        Role.MANAGER.value,
                                        Role.SYSTEM_ADMIN.value,
                                    ]
                                ),
                            )
                        )
                    ).all()
                ]
            for uid in admin_ids:
                targets.append({"kind": RecipientKind.USER.value, "id": str(uid)})
        else:
            severity = NotificationSeverity.INFO
            ntype = NotificationType.INTEGRATION_EVENT
            title = f"{integration.provider} synced {total_new} new record{'s' if total_new != 1 else ''}"
            body = f"{integration.instance_name}: {total_new} measurement(s) imported."

        await emit(
            source=NotificationSource.INTEGRATION,
            type=ntype,
            category=NotificationCategory.INTEGRATION,
            severity=severity,
            title=title,
            body=body,
            tenant_id=tenant_id,
            targets=targets,
            payload={
                "integration_id": str(integration.id),
                "provider": integration.provider,
            },
            source_ref={
                "integration_id": str(integration.id),
                "provider": integration.provider,
                "status": result.status,
            },
        )
    except Exception:
        logger.exception("Integration sync notification failed")


async def run_sync(
    db: AsyncSession,
    integration: Any,
    provider: Any,
    *,
    source: str = "background",
) -> SyncResult:
    """Run the full sync pipeline for one integration instance.

    Pipeline: acquire lock → pull → convert → biomarker-map → telemetry-split
    → push → stamp ``last_synced_at`` → write ``IntegrationSyncLog`` → commit.

    The 3-tier error contract (``IntegrationAuthError`` → status ERROR;
    ``IntegrationRateLimitError`` → retry next cycle; other → log) is handled
    uniformly here so the worker and the manual endpoint share the exact same
    behaviour.

    Returns a :class:`SyncResult`. The caller decides how to surface errors.
    """
    from integrations.sdk.exceptions import (
        IntegrationAuthError,
        IntegrationRateLimitError,
    )
    from app.models.user_integration import IntegrationStatus, IntegrationSyncLog

    started = _now()
    acquired, lock_key = await _try_acquire_lock(integration.id)
    if not acquired:
        return SyncResult(
            status="skipped",
            error="Another sync is already running for this integration.",
            started_at=started,
            completed_at=_now(),
        )

    async def _debug(title: str, payload: dict, level: str = "info") -> None:
        if integration.is_debug_enabled and hasattr(provider, "log_debug_payload"):
            try:
                await provider.log_debug_payload(
                    integration, title, payload, level=level
                )
            except Exception:
                pass

    result: Optional[SyncResult] = None
    try:
        # ---- pull ----
        observations_data = await provider.pull_data(integration)
        pulled = len(observations_data) if observations_data else 0

        observations: List[Observation] = []
        dropped_invalid = 0
        if observations_data:
            for obs_data in observations_data:
                obs_dict = (
                    obs_data.model_dump(exclude_unset=True)
                    if hasattr(obs_data, "model_dump")
                    else obs_data.dict(exclude_unset=True)
                    if hasattr(obs_data, "dict")
                    else obs_data
                )
                observations.append(Observation(**obs_dict))
        # audit B3: keep the relational patient_id in sync with the FHIR subject
        # reference on integration-sourced observations (the SDK builder only
        # sets ``subject``).
        for _obs in observations:
            _obs.patient_id = coerce_patient_id(_obs.patient_id, _obs.subject)

        # Map once for the whole batch (was previously indented inside the
        # per-observation loop above and ran N times — wasted work, since
        # each call is idempotent on the biomarker definitions, but a real
        # regression risk if the call ever grew side effects).
        from app.services.fhir_service import map_observations_to_biomarkers

        map_result = await map_observations_to_biomarkers(db, observations)
        dropped_invalid = (
            map_result.get("dropped_invalid", 0)
            if isinstance(map_result, dict)
            else 0
        )

        # ---- telemetry / FHIR split ----
        telemetry_count = 0
        fhir_count = 0
        if observations:
            telemetry_records, fhir_records = await apply_telemetry_split(
                db,
                observations,
                tenant_id=integration.tenant_id,
                instance_name=integration.instance_name,
                provider_name=integration.provider,
                integration_id=integration.id,
            )
            telemetry_count = len(telemetry_records)
            fhir_count = len(fhir_records)

        # ---- clinical events (opt-in hook, workstream B.2) ----
        # Providers that declare ``supports_clinical_events`` can pull
        # longitudinal events (hospital admissions, chronic conditions,
        # pregnancies, ...) alongside observations. The engine resolves a
        # service-context actor from the integration's owning user and
        # writes each event via ``clinical_event_service.create_event``,
        # which dedups on (tenant, patient, source_integration_id,
        # external_id) when the provider sets ``external_id`` on the
        # payload. Failures are logged and don't abort the sync (mirrors
        # the push hook's resilience).
        events_pulled = 0
        events_written = 0
        supports_events = _opt_in(provider, "supports_clinical_events")
        if supports_events:
            try:
                events_data = await provider.pull_clinical_events(integration)
                events_data = events_data or []
                events_pulled = len(events_data)
                if events_data:
                    from app.services.integration_actor import (
                        resolve_integration_actor,
                    )
                    from app.services.clinical_event_service import create_event

                    actor = await resolve_integration_actor(db, integration)
                    for ev in events_data:
                        try:
                            await create_event(
                                db,
                                actor,
                                ev,
                                source_integration_id=integration.id,
                                external_id=getattr(ev, "external_id", None),
                            )
                            events_written += 1
                        except Exception as ev_err:
                            logger.warning(
                                "create_event failed for integration %s event "
                                "%r: %s",
                                integration.id,
                                getattr(ev, "external_id", None),
                                ev_err,
                            )
            except Exception as ev_pull_err:
                logger.warning(
                    "pull_clinical_events failed for %s: %s",
                    integration.provider, ev_pull_err,
                )
            else:
                if events_pulled and events_written < events_pulled:
                    logger.info(
                        "clinical-events sync: provider %s pulled %d, wrote "
                        "%d (%d failed create_event)",
                        integration.provider, events_pulled,
                        events_written, events_pulled - events_written,
                    )

        # ---- examinations (opt-in hook, workstream E.3) ----
        # Same shape as the clinical-events hook above. Providers that
        # declare ``supports_examinations`` can pull FHIR Encounters (lab
        # visits, hospital appointments, imaging sessions). The engine
        # resolves the actor once (re-uses the one from the events step if
        # that ran) and writes each exam via
        # ``examination_service.create_examination``, which dedups on
        # (tenant, patient, source_integration_id, external_id) when the
        # provider sets ``external_id`` on the payload. The service also
        # runs the category-text → concept-id resolution and patient
        # validation; we don't duplicate that here.
        exams_pulled = 0
        exams_written = 0
        # Hoisted map so the documents block (workstream C) can resolve a
        # ``DocumentPull.examination_external_id`` against the exam that was
        # just pulled+persisted above. Keyed by the upstream external_id
        # the provider set on ``ExaminationCreate``; value is the resulting
        # exam's UUID. Empty when the provider doesn't opt into exams.
        exam_by_external_id: Dict[str, UUID] = {}
        supports_exams = _opt_in(provider, "supports_examinations")
        if supports_exams:
            try:
                exams_data = await provider.pull_examinations(integration)
                exams_data = exams_data or []
                exams_pulled = len(exams_data)
                if exams_data:
                    from app.services.integration_actor import (
                        resolve_integration_actor,
                    )
                    from app.services.examination_service import (
                        create_examination,
                    )

                    actor = await resolve_integration_actor(db, integration)
                    for exam_payload in exams_data:
                        try:
                            created_exam = await create_examination(
                                db,
                                actor,
                                exam_payload,
                                source_integration_id=integration.id,
                                external_id=getattr(
                                    exam_payload, "external_id", None
                                ),
                            )
                            exams_written += 1
                            ext_id = getattr(exam_payload, "external_id", None)
                            if ext_id and created_exam is not None:
                                exam_by_external_id[str(ext_id)] = (
                                    created_exam.id
                                )
                        except Exception as exam_err:
                            logger.warning(
                                "create_examination failed for integration "
                                "%s exam %r: %s",
                                integration.id,
                                getattr(exam_payload, "external_id", None),
                                exam_err,
                            )
            except Exception as exam_pull_err:
                logger.warning(
                    "pull_examinations failed for %s: %s",
                    integration.provider, exam_pull_err,
                )
            else:
                if exams_pulled and exams_written < exams_pulled:
                    logger.info(
                        "examinations sync: provider %s pulled %d, wrote %d "
                        "(%d failed create_examination)",
                        integration.provider, exams_pulled,
                        exams_written, exams_pulled - exams_written,
                    )

        # ---- catalog proposals (opt-in hook, workstream F) ----
        # Providers that declare ``supports_catalog_proposals`` can
        # contribute catalog entries (biomarker classes, medications,
        # concepts, typed concept edges) sourced from upstream — closing
        # the gap left by ``ConceptProvenance.INTEGRATION`` (declared in
        # the enum but unwritten before F). Each returned proposal is
        # applied through ``catalog_proposal_service.apply_proposal``,
        # which routes by ``kind`` to the matching service-layer write
        # path and is idempotent on the natural key per kind — re-syncs
        # don't duplicate. Capped at ``INTEGRATION_MAX_PROPOSALS_PER_SYNC``
        # items; excess dropped with a warning. Failures are logged and
        # don't abort the sync (mirrors the events / exams hooks).
        proposals_pulled = 0
        proposals_applied = 0
        supports_proposals = _opt_in(provider, "supports_catalog_proposals")
        if supports_proposals:
            try:
                proposals_data = await provider.pull_catalog_proposals(
                    integration
                )
                proposals_data = proposals_data or []
                proposals_pulled = len(proposals_data)
                if proposals_data:
                    from app.services.catalog_proposal_service import (
                        apply_proposal,
                    )
                    from app.services.integration_actor import (
                        resolve_integration_actor,
                    )

                    actor = await resolve_integration_actor(
                        db, integration
                    )
                    dropped_by_cap = 0
                    for idx, proposal in enumerate(proposals_data):
                        if idx >= INTEGRATION_MAX_PROPOSALS_PER_SYNC:
                            dropped_by_cap = proposals_pulled - idx
                            logger.warning(
                                "catalog-proposals sync: provider %s returned "
                                "%d proposals — cap is %d; dropping the last "
                                "%d.",
                                integration.provider, proposals_pulled,
                                INTEGRATION_MAX_PROPOSALS_PER_SYNC,
                                dropped_by_cap,
                            )
                            break
                        try:
                            result = await apply_proposal(
                                db, actor, integration, proposal
                            )
                            if result.created:
                                proposals_applied += 1
                        except Exception as proposal_err:
                            logger.warning(
                                "apply_proposal failed for integration %s "
                                "proposal #%d (kind=%s): %s",
                                integration.id, idx,
                                getattr(proposal, "kind", "?"), proposal_err,
                            )
            except Exception as proposals_pull_err:
                logger.warning(
                    "pull_catalog_proposals failed for %s: %s",
                    integration.provider, proposals_pull_err,
                )
            else:
                if proposals_pulled and proposals_applied < proposals_pulled:
                    logger.info(
                        "catalog-proposals sync: provider %s pulled %d, "
                        "applied %d new (%d already existed or failed)",
                        integration.provider, proposals_pulled,
                        proposals_applied, proposals_pulled - proposals_applied,
                    )

        # ---- HITL proposals (opt-in hook, workstream G) ----
        # The HITL layer is the human-in-the-loop counterpart to the
        # catalog-proposals block above. Same payload shapes, but the
        # integration asks the platform to queue each proposal for human
        # review instead of auto-applying. The engine persists each spec
        # as a PROPOSED ``IntegrationProposal`` row + fires an HITL
        # notification. Re-emitting the same spec on the next sync is a
        # no-op (deduped via ``create_proposal``'s dedup_key check). The
        # user resolves via the
        # ``/api/v1/integrations/instance/{id}/proposals/.../resolve``
        # endpoint, which routes the (possibly-edited) payload through
        # ``catalog_proposal_service.apply_proposal`` — the same write
        # path the catalog-proposals block above uses. Capped at
        # ``INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC`` items; excess
        # dropped with a warning. Failures are logged and don't abort
        # the sync (mirrors the events / exams / catalog-proposals hooks).
        hitl_pulled = 0
        hitl_inserted = 0
        supports_hitl = _opt_in(provider, "supports_hitl_proposals")
        if supports_hitl:
            try:
                hitl_specs = await provider.pull_hitl_proposals(integration)
                hitl_specs = hitl_specs or []
                hitl_pulled = len(hitl_specs)
                if hitl_specs:
                    from app.services.integration_proposal_service import (
                        create_proposal as _create_hitl_proposal,
                    )

                    hitl_dropped_by_cap = 0
                    for idx, spec in enumerate(hitl_specs):
                        if idx >= INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC:
                            hitl_dropped_by_cap = hitl_pulled - idx
                            logger.warning(
                                "hitl-proposals sync: provider %s returned "
                                "%d proposals — cap is %d; dropping the "
                                "last %d.",
                                integration.provider, hitl_pulled,
                                INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC,
                                hitl_dropped_by_cap,
                            )
                            break
                        try:
                            proposal_type = getattr(
                                spec, "proposal_type", None
                            )
                            if not proposal_type:
                                logger.warning(
                                    "hitl-proposals sync: provider %s spec "
                                    "#%d missing proposal_type — skipping",
                                    integration.provider, idx,
                                )
                                continue
                            _, created = await _create_hitl_proposal(
                                db,
                                integration_id=integration.id,
                                tenant_id=integration.tenant_id,
                                patient_id=getattr(spec, "patient_id", None),
                                proposal_type=proposal_type,
                                title=getattr(spec, "title", "(untitled)"),
                                proposed_payload=getattr(
                                    spec, "proposed_payload", {}
                                ),
                                context=getattr(spec, "context", {}) or {},
                                created_by=integration.user_id,
                            )
                            if created:
                                hitl_inserted += 1
                                # Fire the HITL notification only for newly-
                                # inserted rows so re-syncs don't spam the
                                # inbox. Best-effort; failures logged.
                                await _emit_hitl_proposal_notification(
                                    integration, spec
                                )
                        except Exception as spec_err:
                            logger.warning(
                                "create_proposal failed for integration %s "
                                "HITL spec #%d (type=%s): %s",
                                integration.id, idx,
                                getattr(spec, "proposal_type", "?"),
                                spec_err,
                            )
            except Exception as hitl_pull_err:
                logger.warning(
                    "pull_hitl_proposals failed for %s: %s",
                    integration.provider, hitl_pull_err,
                )
            else:
                if hitl_pulled and hitl_inserted < hitl_pulled:
                    logger.info(
                        "hitl-proposals sync: provider %s pulled %d, "
                        "inserted %d new (%d already existed or failed)",
                        integration.provider, hitl_pulled,
                        hitl_inserted, hitl_pulled - hitl_inserted,
                    )

        # ---- documents (opt-in hook, workstream C) ----
        # Providers that declare ``supports_documents`` can deliver
        # pre-fetched document bytes (a hospital integration pulling
        # scanned lab reports, a fax-to-email gateway, a wearable
        # companion app syncing ECG printouts). The engine resolves a
        # service-context actor + writes each document via
        # ``document_service.ingest_document_bytes`` — the same path the
        # UI upload endpoint uses. The OCR + LLM extraction Celery task
        # fires automatically when ``include_in_extraction=True`` on the
        # spec (best-effort: broker-down failures are swallowed inside
        # the service). Per-document failures are logged and don't abort
        # the sync.
        #
        # Two caps protect against runaway providers:
        #   - ``INTEGRATION_MAX_DOCS_PER_SYNC`` (item count, default 20)
        #   - ``INTEGRATION_MAX_DOC_BYTES_PER_SYNC`` (total bytes, 50 MiB)
        # Both enforced before ``ingest_document_bytes`` is called.
        docs_pulled = 0
        docs_written = 0
        supports_docs = _opt_in(provider, "supports_documents")
        if supports_docs:
            try:
                docs_data = await provider.pull_documents(integration)
                docs_data = docs_data or []
                docs_pulled = len(docs_data)
                if docs_data:
                    from app.services.document_service import (
                        ingest_document_bytes,
                    )
                    from app.services.integration_actor import (
                        resolve_integration_actor,
                    )
                    from app.services.concept_service import (
                        resolve_concept_by_slug,
                    )

                    actor = await resolve_integration_actor(db, integration)
                    bytes_this_sync = 0
                    dropped_by_count_cap = 0
                    dropped_by_byte_cap = 0
                    for idx, doc_spec in enumerate(docs_data):
                        if idx >= INTEGRATION_MAX_DOCS_PER_SYNC:
                            dropped_by_count_cap += 1
                            continue
                        content = getattr(doc_spec, "content", b"") or b""
                        if (
                            bytes_this_sync + len(content)
                            > INTEGRATION_MAX_DOC_BYTES_PER_SYNC
                        ):
                            dropped_by_byte_cap += 1
                            logger.warning(
                                "documents sync: provider %s doc #%d (%d "
                                "bytes) would exceed the %d-byte per-sync "
                                "cap — dropping",
                                integration.provider, idx, len(content),
                                INTEGRATION_MAX_DOC_BYTES_PER_SYNC,
                            )
                            continue
                        try:
                            exam_ext_id = getattr(
                                doc_spec, "examination_external_id", None
                            )
                            resolved_exam_id: Optional[UUID] = (
                                exam_by_external_id.get(str(exam_ext_id))
                                if exam_ext_id
                                else None
                            )
                            category_slug = getattr(
                                doc_spec, "category_concept_slug", None
                            )
                            resolved_category_id: Optional[UUID] = (
                                await resolve_concept_by_slug(
                                    db,
                                    str(category_slug),
                                    tenant_id=actor.tenant_id,
                                )
                                if category_slug
                                else None
                            )
                            await ingest_document_bytes(
                                filename=getattr(doc_spec, "filename", None)
                                or "unknown",
                                content=content,
                                content_type=getattr(
                                    doc_spec, "content_type", None
                                ),
                                tenant_id=actor.tenant_id,
                                patient_id=integration.patient_id,
                                owner_id=actor.user_id,
                                db=db,
                                examination_id=resolved_exam_id,
                                include_in_extraction=bool(
                                    getattr(
                                        doc_spec,
                                        "include_in_extraction",
                                        True,
                                    )
                                ),
                                category_concept_id=resolved_category_id,
                                # Item 3 of integrations-sdk-improvements:
                                # pass the integration-key pair so the
                                # service dedups at the DB layer. The
                                # engine supplies source_integration_id
                                # from integration.id (the provider
                                # can't fake it); external_id comes
                                # from the DocumentPull spec.
                                source_integration_id=integration.id,
                                external_id=getattr(
                                    doc_spec, "external_id", None
                                ),
                            )
                            docs_written += 1
                            bytes_this_sync += len(content)
                        except Exception as doc_err:
                            logger.warning(
                                "ingest_document_bytes failed for "
                                "integration %s doc #%d (%r): %s",
                                integration.id, idx,
                                getattr(doc_spec, "filename", "?"),
                                doc_err,
                            )
                    if dropped_by_count_cap:
                        logger.warning(
                            "documents sync: provider %s returned %d "
                            "documents — count cap is %d; dropped %d",
                            integration.provider, docs_pulled,
                            INTEGRATION_MAX_DOCS_PER_SYNC,
                            dropped_by_count_cap,
                        )
                    if dropped_by_byte_cap:
                        logger.warning(
                            "documents sync: provider %s dropped %d "
                            "documents that would have exceeded the "
                            "%d-byte per-sync cap",
                            integration.provider, dropped_by_byte_cap,
                            INTEGRATION_MAX_DOC_BYTES_PER_SYNC,
                        )
            except Exception as docs_pull_err:
                logger.warning(
                    "pull_documents failed for %s: %s",
                    integration.provider, docs_pull_err,
                )
            else:
                if docs_pulled and docs_written < docs_pulled:
                    logger.info(
                        "documents sync: provider %s pulled %d, wrote %d "
                        "(%d dropped by cap or failed ingest)",
                        integration.provider, docs_pulled, docs_written,
                        docs_pulled - docs_written,
                    )

        # ---- push ----
        push_result: Optional[Dict[str, Any]] = None
        try:
            push_result = await provider.push_data(
                integration, {"status": f"{source}_sync"}
            )
        except Exception as push_err:
            logger.warning("Push failed for %s: %s", integration.provider, push_err)
            push_result = None

        # ---- bookkeeping ----
        integration.last_synced_at = _now()
        sync_status = "success" if dropped_invalid == 0 else "partial"
        error_msg = (
            f"{dropped_invalid} of {pulled} pulled observations "
            "failed FHIR validation and were dropped"
            if dropped_invalid
            else None
        )
        completed = _now()
        sync_log = IntegrationSyncLog(
            integration_id=integration.id,
            tenant_id=integration.tenant_id,
            status=sync_status,
            records_synced=(
                telemetry_count
                + fhir_count
                + events_written
                + exams_written
                + proposals_applied
                + hitl_inserted
                + docs_written
            ),
            started_at=started,
            completed_at=completed,
            error_message=error_msg,
        )
        db.add(sync_log)
        await db.commit()

        result = SyncResult(
            pulled=pulled,
            fhir_persisted=fhir_count,
            telemetry_persisted=telemetry_count,
            dropped_invalid=dropped_invalid,
            pushed=push_result,
            status=sync_status,
            started_at=started,
            completed_at=completed,
            proposals_pulled=proposals_pulled,
            proposals_applied=proposals_applied,
            hitl_proposals_pulled=hitl_pulled,
            hitl_proposals_inserted=hitl_inserted,
            documents_pulled=docs_pulled,
            documents_written=docs_written,
        )
        return result

    except IntegrationAuthError as e:
        await db.rollback()
        logger.warning("Auth error for %s: %s", integration.provider, e)
        await _debug("Auth Error", {"error": str(e), "source": source}, level="error")
        integration.status = IntegrationStatus.ERROR
        _write_failed_log(db, integration, started, str(e))
        await db.commit()
        result = SyncResult(
            status="failed",
            error=str(e),
            error_type="auth",
            started_at=started,
            completed_at=_now(),
        )
        return result

    except IntegrationRateLimitError as e:
        await db.rollback()
        logger.warning("Rate limit for %s: %s", integration.provider, e)
        await _debug(
            "Rate Limit Error", {"error": str(e), "source": source}, level="warning"
        )
        _write_failed_log(
            db, integration, started, "Rate Limit Exceeded. Will retry later."
        )
        await db.commit()
        # Surface the upstream's Retry-After hint (if any) so the
        # caller can avoid hammering the upstream on every beat. The
        # worker reads ``SyncResult.retry_after_seconds`` and writes a
        # Redis cooldown key. The value is clamped inside the cooldown
        # helper (60s..1h) to defend against an upstream lying.
        retry_after = getattr(e, "retry_after_seconds", None)
        result = SyncResult(
            status="failed",
            error=str(e),
            error_type="rate_limit",
            started_at=started,
            completed_at=_now(),
            retry_after_seconds=retry_after,
        )
        return result

    except Exception as e:
        await db.rollback()
        logger.error("Sync error for %s: %s", integration.provider, e, exc_info=True)
        await _debug("Sync Error", {"error": str(e), "source": source}, level="error")
        _write_failed_log(db, integration, started, str(e))
        await db.commit()
        result = SyncResult(
            status="failed",
            error=str(e),
            error_type="data",
            started_at=started,
            completed_at=_now(),
        )
        return result

    finally:
        await _release_lock(lock_key)
        # Best-effort notification dispatch (baseline + provider-authored).
        # Wrapped in post_sync_notifications so a notification failure can
        # never break the sync result. Same helper is called from the
        # webhook handler so both code paths get identical coverage.
        if result is not None:
            try:
                await post_sync_notifications(
                    provider,
                    integration,
                    pulled=result.pulled,
                    fhir_persisted=result.fhir_persisted,
                    telemetry_persisted=result.telemetry_persisted,
                    status=result.status,
                    started_at=result.started_at or started,
                    completed_at=result.completed_at or _now(),
                    error=result.error,
                    error_type=result.error_type,
                    observations=observations_data or [],
                )
            except Exception:
                logger.exception(
                    "post_sync_notifications raised for %s", integration.id
                )


def _write_failed_log(
    db: AsyncSession, integration: Any, started: datetime, error: str
) -> None:
    """Queue a ``failed`` IntegrationSyncLog row (caller commits)."""
    from app.models.user_integration import IntegrationSyncLog

    db.add(
        IntegrationSyncLog(
            integration_id=integration.id,
            tenant_id=integration.tenant_id,
            status="failed",
            records_synced=0,
            started_at=started,
            completed_at=_now(),
            error_message=error,
        )
    )


async def _filter_specs_by_owner_type_prefs(
    integration: Any, specs: list[Any]
) -> list[Any]:
    """Drop specs whose declared ``type_id`` the integration owner has muted.

    Per-integration-type preferences live at
    ``user.settings["notifications.integration.{domain}.{type_id}"] = False``.
    Specs without a ``type_id`` always pass through. Loads the owner's
    settings once (single query); returns the input list unchanged on any
    error so a settings-lookup failure can never suppress notifications.
    """
    if not specs:
        return specs
    # Fast path: nothing is tagged.
    if not any(getattr(s, "type_id", None) for s in specs):
        return specs

    try:
        from app.models.user_model import UserModel

        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(UserModel.settings).where(
                        UserModel.id == integration.user_id
                    )
                )
            ).scalar_one_or_none()
        user_settings = dict(row or {})
    except Exception:
        logger.exception(
            "Failed to load user settings for type pref filter; passing all specs through (user=%s)",
            integration.user_id,
        )
        return specs

    domain = integration.provider
    out: list[Any] = []
    for spec in specs:
        tid = getattr(spec, "type_id", None)
        if not tid:
            out.append(spec)
            continue
        key = f"notifications.integration.{domain}.{tid}"
        if user_settings.get(key, True) is False:
            logger.debug(
                "Filtering integration notification (user=%s domain=%s type_id=%s — muted)",
                integration.user_id,
                domain,
                tid,
            )
            continue
        out.append(spec)
    return out


async def _emit_hitl_proposal_notification(
    integration: Any, spec: Any
) -> None:
    """Fire the HITL notification for one newly-inserted proposal.

    Mirrors the chat-side ``_notify_hitl_proposal`` shape but keyed on
    ``NotificationSource.INTEGRATION`` so the frontend can render
    integration-sourced proposals separately if it wants. The action
    button links to the integration instance's proposals view
    (``/integrations/{provider}/{integration_id}/proposals/{proposal_id}``);
    the route doesn't exist in the SPA yet (frontend G.5 deferred per
    parent plan §G mitigation), so the link is forward-compatible — when
    G.5 lands it'll Just Work.

    Best-effort: failures are logged + swallowed so a buggy notification
    path can't break the sync.
    """
    try:
        from app.models.enums import (
            NotificationCategory,
            NotificationSeverity,
            NotificationSource,
            NotificationType,
            RecipientKind,
        )
        from app.services.notification_service import emit

        proposal_type = getattr(spec, "proposal_type", "proposal")
        title = getattr(spec, "title", "Integration proposal needs your review")
        await emit(
            source=NotificationSource.INTEGRATION,
            type=NotificationType.HITL_TASK,
            category=NotificationCategory.HITL,
            severity=NotificationSeverity.WARNING,
            title=title,
            body=(
                f"{integration.provider or 'Integration'} proposed a "
                f"{proposal_type.replace('_', ' ')} for your review."
            ),
            patient_id=getattr(spec, "patient_id", None),
            tenant_id=integration.tenant_id,
            targets=[
                {
                    "kind": RecipientKind.USER.value,
                    "id": str(integration.user_id),
                }
            ],
            payload={
                "proposal_type": proposal_type,
                "integration_id": str(integration.id),
                "domain": integration.provider,
                "proposed_payload": getattr(spec, "proposed_payload", {}),
            },
            source_ref={
                "integration_id": str(integration.id),
                "proposal_type": proposal_type,
                "source": "integration_hitl",
            },
        )
    except Exception:
        logger.exception(
            "HITL-proposal notification emit failed for integration=%s",
            getattr(integration, "id", None),
        )


async def _emit_provider_notifications(
    provider: Any,
    integration: Any,
    result: "SyncResult",
    observations: list[Any],
) -> None:
    """Ask the provider for rich, event-driven notifications and emit them.

    Called from ``run_sync``'s ``finally`` block after a successful/partial
    sync, AND from the webhook handler (see :func:`post_sync_notifications`).
    The provider's :meth:`get_notifications` returns a list of
    :class:`~integrations.sdk.notifications.NotificationSpec`. The platform
    emits each with:

    * ``source=NotificationSource.INTEGRATION``
    * ``targets`` default = integration owner (USER); providers can override
      via ``spec.targets_override``
    * ``patient_id`` from the spec (defaults to ``integration.patient_id``)
    * ``tenant_id`` from the integration
    * ``source_ref.integration_id`` automatically added (admin filter/group)

    Failures are logged and swallowed — never propagate to the sync result.
    """
    if (
        provider is None
        or not getattr(provider, "supports_notifications", lambda: False)()
    ):
        return

    context = {
        "sync_result": result,
        "patient_id": str(integration.patient_id) if integration.patient_id else None,
        "integration_id": str(integration.id),
        "domain": integration.provider,
    }
    try:
        specs = await provider.get_notifications(
            integration, observations=observations, context=context
        )
    except Exception:
        logger.exception(
            "Provider %s get_notifications raised; skipping",
            integration.provider,
        )
        return

    if not specs:
        return

    # Filter specs whose declared type_id the integration owner has opted
    # out of. Prefs live at user.settings["notifications.integration.
    # {domain}.{type_id}"] = False. Specs without a type_id always pass
    # through (backwards-compatible). When a spec uses targets_override
    # (broadcast beyond the owner), we still apply the owner's pref — the
    # owner is the one who configured the integration and is the canonical
    # audience; per-recipient type prefs would require pushing this filter
    # down into emit() and coupling it to integration concepts, which we
    # explicitly avoid.
    filtered_specs = await _filter_specs_by_owner_type_prefs(integration, specs)
    if not filtered_specs:
        return

    from app.models.enums import (
        NotificationCategory,
        NotificationSeverity,
        NotificationSource,
        NotificationType,
        RecipientKind,
    )
    from app.services.notification_service import emit

    for spec in filtered_specs:
        try:
            # Category / severity / type — accept both enum and string value.
            category = _coerce_enum(
                spec.category, NotificationCategory, NotificationCategory.INTEGRATION
            )
            severity = _coerce_enum(
                spec.severity, NotificationSeverity, NotificationSeverity.INFO
            )
            ntype = _coerce_enum(
                spec.type, NotificationType, NotificationType.INTEGRATION_EVENT
            )

            patient_id = spec.patient_id or integration.patient_id
            targets = spec.targets_override or [
                {"kind": RecipientKind.USER.value, "id": str(integration.user_id)}
            ]

            source_ref = dict(spec.source_ref)
            source_ref.setdefault("integration_id", str(integration.id))
            source_ref.setdefault("provider", integration.provider)

            await emit(
                source=NotificationSource.INTEGRATION,
                type=ntype,
                category=category,
                severity=severity,
                title=spec.title,
                body=spec.body,
                patient_id=patient_id,
                tenant_id=integration.tenant_id,
                targets=targets,
                payload=spec.to_payload(),
                source_ref=source_ref,
                sender_user_id=None,
                # Item 4 of integrations-sdk-improvements: forward the
                # provider's digest_key so consecutive syncs collapse
                # into one inbox entry inside the TTL window.
                dedup_key=getattr(spec, "digest_key", None),
            )
        except Exception:
            logger.exception(
                "Failed to emit provider notification %r for %s",
                getattr(spec, "title", "?"),
                integration.id,
            )
            continue


async def post_sync_notifications(
    provider: Any,
    integration: Any,
    *,
    pulled: int,
    fhir_persisted: int,
    telemetry_persisted: int,
    status: str,
    started_at: datetime,
    completed_at: datetime,
    error: Optional[str] = None,
    error_type: Optional[str] = None,
    observations: Optional[list[Any]] = None,
) -> None:
    """Best-effort baseline + provider-authored notification dispatch.

    Single entry point called from BOTH ``run_sync``'s finally block AND
    the webhook endpoint so webhook-driven integrations get the same
    notification coverage as pull-driven ones. Constructs a throwaway
    :class:`SyncResult` and invokes both emitters. Never raises — wraps
    every step in try/except so a notification failure can never break
    the parent sync / webhook response.
    """
    result = SyncResult(
        pulled=pulled,
        fhir_persisted=fhir_persisted,
        telemetry_persisted=telemetry_persisted,
        status=status,
        error=error,
        error_type=error_type,
        started_at=started_at,
        completed_at=completed_at,
    )
    try:
        await _notify_sync_outcome(integration, result)
    except Exception:
        logger.exception("Sync-outcome notification failed for %s", integration.id)
    if status in ("success", "partial") and (fhir_persisted + telemetry_persisted) > 0:
        try:
            await _emit_provider_notifications(
                provider, integration, result, observations or []
            )
        except Exception:
            logger.exception(
                "Provider-authored notifications failed for %s", integration.id
            )


def _coerce_enum(value: Any, enum_cls: Any, default: Any) -> Any:
    """Best-effort coerce a string or enum member to the enum class."""
    if value is None:
        return default
    try:
        return enum_cls(value)
    except Exception:
        try:
            return enum_cls[value]
        except Exception:
            return default
