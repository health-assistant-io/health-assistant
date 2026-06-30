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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir import Observation
from app.models.telemetry_model import TelemetryDataModel

logger = logging.getLogger(__name__)

_SYNC_LOCK_TTL = 600  # seconds — matches the worker's original Redis lock expiry


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
                reference = (
                    f"Integration/{integration_id}" if integration_id else None
                )
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
                await provider.log_debug_payload(integration, title, payload, level=level)
            except Exception:
                pass

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
            records_synced=telemetry_count + fhir_count,
            started_at=started,
            completed_at=completed,
            error_message=error_msg,
        )
        db.add(sync_log)
        await db.commit()

        return SyncResult(
            pulled=pulled,
            fhir_persisted=fhir_count,
            telemetry_persisted=telemetry_count,
            dropped_invalid=dropped_invalid,
            pushed=push_result,
            status=sync_status,
            started_at=started,
            completed_at=completed,
        )

    except IntegrationAuthError as e:
        await db.rollback()
        logger.warning("Auth error for %s: %s", integration.provider, e)
        await _debug("Auth Error", {"error": str(e), "source": source}, level="error")
        integration.status = IntegrationStatus.ERROR
        _write_failed_log(db, integration, started, str(e))
        await db.commit()
        return SyncResult(
            status="failed", error=str(e), error_type="auth",
            started_at=started, completed_at=_now(),
        )

    except IntegrationRateLimitError as e:
        await db.rollback()
        logger.warning("Rate limit for %s: %s", integration.provider, e)
        await _debug("Rate Limit Error", {"error": str(e), "source": source}, level="warning")
        _write_failed_log(db, integration, started, "Rate Limit Exceeded. Will retry later.")
        await db.commit()
        return SyncResult(
            status="failed", error=str(e), error_type="rate_limit",
            started_at=started, completed_at=_now(),
        )

    except Exception as e:
        await db.rollback()
        logger.error("Sync error for %s: %s", integration.provider, e, exc_info=True)
        await _debug("Sync Error", {"error": str(e), "source": source}, level="error")
        _write_failed_log(db, integration, started, str(e))
        await db.commit()
        return SyncResult(
            status="failed", error=str(e), error_type="data",
            started_at=started, completed_at=_now(),
        )

    finally:
        await _release_lock(lock_key)


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
