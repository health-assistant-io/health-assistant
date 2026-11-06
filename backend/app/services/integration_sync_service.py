"""The integration sync orchestrator: run_sync, the one pipeline all
connector pulls share (lock -> credential gate -> legs -> push ->
bookkeeping -> commit), plus the telemetry-split helpers that predate
the ingest funnel. Per-source legs live in sync_legs; the notification
surface in sync_notifications; shared constants + SyncResult in
sync_support."""

import contextlib
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.converters import utcnow as _now
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir import Observation
from app.models.telemetry_model import TelemetryDataModel
from app.services.integration import integration_ingest
from app.services.integration.sync_legs import (  # noqa: F401 — _declared re-exported for tests
    _declared,
    _leg_allergies,
    _leg_appointments,
    _leg_catalog_proposals,
    _leg_documents,
    _leg_hitl_proposals,
    _leg_immunizations,
    _leg_labs,
    _leg_medications,
    _leg_pull_and_ingest,
    _leg_push,
)
from app.services.integration.sync_notifications import (  # noqa: F401 — re-exported
    _filter_specs_by_owner_type_prefs,
    _write_failed_log,
    post_sync_notifications,
)
from app.services.integration.sync_support import (  # noqa: F401 — re-exported
    INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC,
    INTEGRATION_MAX_PROPOSALS_PER_SYNC,
    SyncResult,
)
from app.services.telemetry.telemetry_ingest import (
    build_telemetry_row,
    resolve_telemetry_device,
)

logger = logging.getLogger(__name__)

_SYNC_LOCK_TTL = 600  # seconds — matches the worker's original Redis lock expiry

# Document per-sync caps live in the ingest funnel (the one path in); these
# names stay importable here for callers that predate the funnel.
INTEGRATION_MAX_DOCS_PER_SYNC = integration_ingest.INTEGRATION_MAX_DOCS_PER_SYNC
INTEGRATION_MAX_DOC_BYTES_PER_SYNC = integration_ingest.INTEGRATION_MAX_DOC_BYTES_PER_SYNC


def _obs_value(obs: Observation) -> float | None:
    """Best-effort numeric extraction for telemetry column mapping.

    Returns ``None`` for any non-numeric observation, which makes the
    telemetry split safe for STATE biomarkers by construction: a categorical
    observation has ``value_codeable_concept`` set and ``normalized_value`` /
    ``raw_value`` / ``value_quantity`` all NULL, so this helper returns
    ``None`` and ``apply_telemetry_split`` skips it (line marked below).
    Combined with the DB CHECK constraint that forbids
    ``value_type='state' AND is_telemetry=TRUE``, STATE biomarkers are
    double-guarded against ever reaching the Float NOT NULL telemetry
    hypertable.
    """
    val = getattr(obs, "normalized_value", None)
    if val is None:
        val = getattr(obs, "raw_value", None)
    if val is None and obs.value_quantity:
        val = obs.value_quantity.get("value")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


async def fetch_biomarker_definitions(db: AsyncSession, observations: list[Observation]) -> dict:
    """Bulk-load the BiomarkerDefinition rows referenced by ``observations``.

    Returns a dict ``{biomarker_id: BiomarkerDefinition}``.
    """
    b_ids = list({obs.biomarker_id for obs in observations if obs.biomarker_id})
    if not b_ids:
        return {}
    result = await db.execute(select(BiomarkerDefinition).where(BiomarkerDefinition.id.in_(b_ids)))
    return {b.id: b for b in result.scalars().all()}


async def apply_telemetry_split(
    db: AsyncSession,
    observations: list[Observation],
    tenant_id: UUID | str | None,
    instance_name: str | None,
    provider_name: str,
    integration_id: UUID | str | None = None,
) -> tuple[list[TelemetryDataModel], list[Observation]]:
    """Apply the FHIR/telemetry split in-memory and queue both row types on ``db``.

    Returns ``(telemetry_records, fhir_records)``. The caller is responsible
    for committing ``db`` once both batches are added.
    """
    if not observations:
        return [], []

    b_defs_map = await fetch_biomarker_definitions(db, observations)

    telemetry_records: list[TelemetryDataModel] = []
    fhir_records: list[Observation] = []

    device_external_ref = instance_name or provider_name
    device_cache: dict[UUID, UUID] = {}

    for obs in observations:
        is_telemetry = False
        if obs.biomarker_id and obs.biomarker_id in b_defs_map:
            is_telemetry = bool(b_defs_map[obs.biomarker_id].is_telemetry)

        if is_telemetry:
            b_def = b_defs_map[obs.biomarker_id]
            slug = (b_def.slug or "").lower()
            value = _obs_value(obs)
            unit = None
            if getattr(obs, "value_quantity", None):
                unit = obs.value_quantity.get("unit")

            # Long-format hypertable: one row per (timestamp, device, slug).
            # ``value`` is NOT NULL on the hypertable, so skip rows we can't
            # extract a numeric value for (logged at debug; the FHIR path
            # is the better home for non-numeric observations anyway).
            if value is None or getattr(obs, "patient_id", None) is None or tenant_id is None:
                logger.debug(
                    "telemetry split: skipping obs %s (slug=%s) — no numeric value, "
                    "tenant or patient attribution",
                    getattr(obs, "id", None),
                    slug,
                )
                continue

            tenant_uuid = UUID(str(tenant_id))
            patient_uuid = UUID(str(obs.patient_id))
            device_uuid = device_cache.get(patient_uuid)
            if device_uuid is None:
                device_uuid = await resolve_telemetry_device(
                    db,
                    tenant_id=tenant_uuid,
                    patient_id=patient_uuid,
                    source=provider_name,
                    external_ref=device_external_ref,
                )
                device_cache[patient_uuid] = device_uuid

            telemetry_records.append(
                TelemetryDataModel(
                    **build_telemetry_row(
                        tenant_id=tenant_uuid,
                        patient_id=patient_uuid,
                        device_id=device_uuid,
                        timestamp=obs.effective_datetime,
                        definition=b_def,
                        value=value,
                        unit=unit,
                    )
                )
            )
        else:
            if not obs.performer:
                reference = f"Integration/{integration_id}" if integration_id else None
                performer = {
                    "type": "Integration",
                    "display": device_external_ref,
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


async def _try_acquire_lock(integration_id: UUID) -> tuple[bool, str]:
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


async def _release_lock(lock_key: str) -> None:
    try:
        from app.core.redis import redis_client

        await redis_client.delete(lock_key)
    except Exception:
        pass  # TTL is the crash-recovery fallback


async def run_sync(
    db: AsyncSession,
    integration: Any,
    provider: Any,
    *,
    source: str = "background",
) -> SyncResult:
    """Run the full sync pipeline for one integration instance.

    Pipeline: acquire lock → hydrate sync state → credential gate → pull →
    ingest funnel (observations/telemetry/documents/appointments) → other
    declared capabilities → push → flush cursors + record stream health →
    write ``IntegrationSyncLog`` → commit.

    The charter-code routing (``auth`` → status ERROR; ``rate-limited`` →
    typed backoff in ``integration_sync_state.next_retry_at``; ``net-denied``
    → non-retryable config class, instance stays ACTIVE; other codes → logged
    failure) is handled uniformly here so the worker and the manual endpoint
    share the exact same behaviour. Backoff is DATA: providers' ``Retry-After``
    and the consecutive-failure streak are written to the stream rows, which
    the beat reads before the next cycle.

    Returns a :class:`SyncResult`. The caller decides how to surface errors.
    """
    from app.extensions.errors import ExtensionError
    from app.models.user_integration import IntegrationStatus, IntegrationSyncLog
    from app.services.integration.integration_credentials import (
        CredentialError,
        require_credentials,
    )
    from app.services.integration.integration_ingest import (
        IngestReport,
    )
    from app.services.integration.integration_sync_state import flush as flush_cursors
    from app.services.integration.integration_sync_state import (
        hydrate,
        record_failure,
        record_success,
        restore_identity,
    )

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
            with contextlib.suppress(Exception):
                await provider.log_debug_payload(integration, title, payload, level=level)

    result: SyncResult | None = None
    ingest_report = IngestReport()
    try:
        # ---- sync state + credential gate ----
        # Cursors live in ``integration_sync_state`` rows; the provider sees
        # them through the hydrated buffer. The credential gate runs before
        # any pull: a revoked/unreadable slot fails closed (typed ``auth``)
        # instead of syncing unauthenticated.
        await hydrate(db, integration)
        await require_credentials(db, integration)
        # ---- pull + the one inbound funnel ----
        (
            observations_data,
            pulled,
            telemetry_count,
            fhir_count,
            dropped_invalid,
            attempts,
        ) = await _leg_pull_and_ingest(db, integration, provider, ingest_report)
        (
            _events_pulled,
            events_written,
            _exams_pulled,
            exams_written,
            exam_by_external_id,
        ) = await _leg_appointments(db, integration, provider, ingest_report, attempts)
        (
            proposals_pulled,
            proposals_applied,
            last_apply_result,
            proposals_ran,
        ) = await _leg_catalog_proposals(db, integration, provider)
        if proposals_ran:
            result = last_apply_result
        hitl_pulled, hitl_inserted = await _leg_hitl_proposals(db, integration, provider)
        docs_pulled, docs_written = await _leg_documents(
            db, integration, provider, ingest_report, attempts, exam_by_external_id
        )
        meds_pulled, meds_written = await _leg_medications(db, integration, provider)
        allergies_pulled, allergies_written = await _leg_allergies(db, integration, provider)
        immuns_pulled, immuns_written = await _leg_immunizations(db, integration, provider)
        labs_pulled, labs_written = await _leg_labs(
            db, integration, provider, ingest_report, attempts
        )
        push_result = await _leg_push(provider, integration, source)

        # ---- bookkeeping ----
        integration.last_synced_at = _now()
        sync_status = ingest_report.status
        error_class = "mapping_gap" if ingest_report.has_mapping_gaps else None
        if ingest_report.mapping_gaps:
            gap_codes = ", ".join(
                sorted({gap.code or gap.display or "unknown" for gap in ingest_report.mapping_gaps})
            )
            error_msg = (
                f"{len(ingest_report.mapping_gaps)} observation(s) stored raw "
                f"(no catalog mapping): {gap_codes}"
            )[:2000]
        elif ingest_report.rejected:
            error_msg = ingest_report.summary()
        else:
            error_msg = None

        # Persist buffered cursors and stamp typed per-stream health in the
        # same transaction as the data. A success resets the failure streak
        # and clears the retry gate on every stream that ran this cycle.
        await flush_cursors(db, integration)
        for stream_name in attempts:
            await record_success(db, integration, stream_name)

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
                + meds_written
                + allergies_written
                + immuns_written
                + labs_written
            ),
            error_class=error_class,
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
            medications_pulled=meds_pulled,
            medications_written=meds_written,
            allergies_pulled=allergies_pulled,
            allergies_written=allergies_written,
            immunizations_pulled=immuns_pulled,
            immunizations_written=immuns_written,
            labs_pulled=labs_pulled,
            labs_written=labs_written,
        )
        return result

    except ExtensionError as e:
        await db.rollback()
        await restore_identity(db, integration)
        # Charter-code routing (the D4 map): ``auth`` flips the instance to
        # ERROR (the UI shows a reconnect prompt); ``rate-limited`` leaves
        # the instance ACTIVE and surfaces the upstream Retry-After hint as
        # a Redis cooldown key; ``net-denied`` is NON-retryable config class
        # (the historical SSRFBlock semantics preserved exactly: logged, a
        # failed sync row, the instance stays ACTIVE — the remedy is config,
        # not retry); every other code is a logged failure with the code on
        # the sync row.
        error_class = "credential" if isinstance(e, CredentialError) else e.code
        if e.code == "auth":
            logger.warning("Auth error for %s: %s", integration.provider, e)
            await _debug(
                "Auth Error", {"code": e.code, "detail": e.detail, "source": source}, level="error"
            )
            integration.status = IntegrationStatus.ERROR
            await record_failure(db, integration, error_class)
            _write_failed_log(
                db,
                integration,
                started,
                e.detail,
                error_code=e.code,
                error_class=error_class,
            )
            await db.commit()
            return SyncResult(
                status="failed",
                error=e.detail,
                error_type=e.code,
                started_at=started,
                completed_at=_now(),
            )
        if e.code == "rate-limited":
            logger.warning("Rate limit for %s: %s", integration.provider, e)
            await _debug(
                "Rate Limit Error",
                {"code": e.code, "detail": e.detail, "source": source},
                level="warning",
            )
            retry_after = e.retry_after_ms / 1000.0 if e.retry_after_ms is not None else None
            # Retry-After is honored as DATA: ``next_retry_at`` on every stream
            # row carries the delay (max of the hint and the exponential
            # backoff), so the beat reads one column instead of a Redis key.
            await record_failure(db, integration, error_class, retry_after_seconds=retry_after)
            _write_failed_log(
                db,
                integration,
                started,
                "Rate Limit Exceeded. Will retry later.",
                error_code=e.code,
                error_class=error_class,
            )
            await db.commit()
            return SyncResult(
                status="failed",
                error=e.detail,
                error_type=e.code,
                started_at=started,
                completed_at=_now(),
                retry_after_seconds=retry_after,
            )
        if e.code == "net-denied":
            logger.warning(
                "Network policy denied a call for %s (non-retryable config class): %s",
                integration.provider,
                e.detail,
            )
            await _debug(
                "Net Denied",
                {"code": e.code, "detail": e.detail, "source": source},
                level="warning",
            )
            await record_failure(db, integration, error_class)
            _write_failed_log(
                db,
                integration,
                started,
                e.detail,
                error_code=e.code,
                error_class=error_class,
            )
            await db.commit()
            return SyncResult(
                status="failed",
                error=e.detail,
                error_type=e.code,
                started_at=started,
                completed_at=_now(),
            )
        logger.error("Sync error for %s (%s): %s", integration.provider, e.code, e.detail)
        await _debug(
            "Sync Error", {"code": e.code, "detail": e.detail, "source": source}, level="error"
        )
        await record_failure(db, integration, error_class)
        _write_failed_log(
            db,
            integration,
            started,
            e.detail,
            error_code=e.code,
            error_class=error_class,
        )
        await db.commit()
        return SyncResult(
            status="failed",
            error=e.detail,
            error_type=e.code,
            started_at=started,
            completed_at=_now(),
        )

    except Exception as e:
        await db.rollback()
        await restore_identity(db, integration)
        logger.error("Sync error for %s: %s", integration.provider, e, exc_info=True)
        await _debug("Sync Error", {"error": str(e), "source": source}, level="error")
        await record_failure(db, integration, "io")
        _write_failed_log(db, integration, started, str(e), error_class="io")
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
                logger.exception("post_sync_notifications raised for %s", integration.id)
