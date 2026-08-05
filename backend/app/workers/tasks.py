import asyncio
import datetime
import functools
import logging
import threading
from uuid import UUID
from typing import Optional, Tuple

from sqlalchemy import select, delete, and_, update
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.workers.celery_app import celery_app
from app.models.user_integration import UserIntegration
from app.models.enums import IntegrationStatus
from app.core.integration_registry import integration_registry
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


# ---------------------------------------------------------------------------
# Worker-scoped async DB engine.
#
# Each Celery worker process gets exactly one AsyncEngine, created lazily on
# first use and disposed when the worker process shuts down. Two reasons this
# is critical:
#
# 1. Per-task engines (the old pattern) produced asyncpg connections bound to
#    the task's event loop. When that loop closed (end of task) and the next
#    task reused a pooled connection via SQLAlchemy's pool, the connection's
#    asyncpg protocol transport was still tied to the closed loop →
#    ``RuntimeError: Event loop is closed`` / ``Future attached to a different
#    loop``. Celery periodic tasks (check_notification_triggers,
#    sync_active_integrations) crashed intermittently depending on which
#    connection the pool handed out.
#
# 2. ``poolclass=NullPool`` makes each session check out a fresh DB connection
#    and close it on session close, so no connection ever outlives the loop
#    that created it. This trades the small per-task connection-setup cost
#    for correctness. Celery prefork already limits concurrency to one task
#    per child process, so connection pooling inside the worker buys little.
# ---------------------------------------------------------------------------

_worker_engine: Optional[AsyncEngine] = None
_worker_engine_lock = threading.Lock()


def get_async_engine() -> AsyncEngine:
    """Return the worker-scoped ``AsyncEngine`` (lazy singleton).

    Thread-safe; idempotent. The engine lives for the lifetime of the worker
    process and is disposed by ``dispose_worker_engine()`` wired to Celery's
    ``worker_process_shutdown`` signal.
    """
    global _worker_engine
    if _worker_engine is None:
        with _worker_engine_lock:
            if _worker_engine is None:
                _worker_engine = create_async_engine(
                    settings.DATABASE_URL,
                    poolclass=NullPool,
                )
                logger.debug("Created worker-scoped AsyncEngine (NullPool)")
    return _worker_engine


async def dispose_worker_engine() -> None:
    """Dispose the worker-scoped engine. Called on worker process shutdown."""
    global _worker_engine
    if _worker_engine is not None:
        await _worker_engine.dispose()
        _worker_engine = None
        logger.debug("Disposed worker-scoped AsyncEngine")


def get_async_session() -> Tuple[AsyncSession, AsyncEngine]:
    """Return ``(session, engine)`` for a new task.

    The session is fresh per call; the engine is the worker-scoped singleton.
    Callers must ``await db.close()`` in a ``finally`` block but must NOT
    dispose the engine (it is shared across tasks).
    """
    engine = get_async_engine()
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    return session_factory(), engine


def async_task(func):
    """Decorator to run async functions in a thread and handle DB session cleanup.

    Each task gets its own event loop (closed in ``finally``). Because
    ``get_async_engine()`` uses ``NullPool``, no DB connection is ever reused
    across loops, so the historical "Future attached to a different loop"
    failure mode is impossible.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(func(*args, **kwargs))
        finally:
            loop.close()

    return wrapper


# Dispose the worker-scoped engine when the Celery worker process exits.
try:
    from celery.signals import worker_process_shutdown

    @worker_process_shutdown.connect
    def _on_worker_shutdown(**kwargs):
        """Best-effort engine disposal on worker shutdown.

        Runs in a fresh loop because the task loop has already closed by the
        time the signal fires.
        """
        global _worker_engine
        if _worker_engine is None:
            return
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_worker_engine.dispose())
        except Exception as exc:
            logger.warning("Engine disposal on shutdown failed: %s", exc)
        finally:
            loop.close()
            _worker_engine = None
except ImportError:
    # Celery not installed (e.g. minimal test env) — skip signal wiring.
    pass


@celery_app.task
@async_task
async def deliver_notification(notification_id: str):
    """Deliver a notification via its pending non-IN_APP channels.

    The fan-out model: one ``Notification`` event has N
    ``NotificationDelivery`` rows (per recipient × channel). IN_APP
    deliveries are marked DELIVERED at emit time; this task handles the
    rest (PUSH today, EMAIL/SMS later). Per-delivery status is updated so
    the delivery log answers "was this delivered via push?".
    """
    logger.info(f"Worker picking up delivery for notification {notification_id}")
    from app.models.notification import (
        Notification,
        NotificationDelivery,
        NotificationStatus,
        NotificationChannel,
        NotificationSubscription,
    )

    db, engine = get_async_session()
    try:
        async with db:
            notif_uuid = UUID(notification_id)
            res = await db.execute(
                select(Notification).where(Notification.id == notif_uuid)
            )
            notif = res.scalar_one_or_none()
            if not notif:
                logger.error(f"Notification {notification_id} not found.")
                return

            push_payload = {
                "id": str(notif.id),
                "type": notif.type.value,
                "title": notif.title,
                "body": notif.body,
                "payload": notif.payload,
            }

            # Pending PUSH deliveries only (IN_APP already delivered at emit).
            deliveries_res = await db.execute(
                select(NotificationDelivery).where(
                    and_(
                        NotificationDelivery.notification_id == notif_uuid,
                        NotificationDelivery.channel == NotificationChannel.PUSH,
                        NotificationDelivery.status == NotificationStatus.PENDING,
                    )
                )
            )
            deliveries = deliveries_res.scalars().all()
            if not deliveries:
                logger.info(f"No pending push deliveries for {notification_id}.")
                return

            from app.services.webpush_service import (
                send_web_push,
                SubscriptionExpired,
            )

            now = datetime.datetime.now(datetime.timezone.utc)
            for delivery in deliveries:
                subs_res = await db.execute(
                    select(
                        NotificationSubscription.id,
                        NotificationSubscription.subscription_data,
                    ).where(
                        and_(
                            NotificationSubscription.user_id == delivery.user_id,
                            NotificationSubscription.is_active.is_(True),
                        )
                    )
                )
                subscriptions = subs_res.all()
                if not subscriptions:
                    delivery.status = NotificationStatus.FAILED
                    delivery.error = "no active push subscription"
                    delivery.attempted_at = now
                    continue

                any_success = False
                for sub_id, sub_data in subscriptions:
                    try:
                        if send_web_push(sub_data, push_payload):
                            any_success = True
                            delivery.subscription_id = sub_id
                        else:
                            logger.warning(
                                f"Web Push failed for sub {sub_id}, notification {notification_id}"
                            )
                    except SubscriptionExpired as ex:
                        logger.info(
                            f"Deactivating dead subscription {sub_id} "
                            f"(HTTP {ex.status_code}): {ex}"
                        )
                        await db.execute(
                            update(NotificationSubscription)
                            .where(NotificationSubscription.id == sub_id)
                            .values(is_active=False)
                        )
                    except Exception as e:
                        logger.error(f"Error sending Web Push to sub {sub_id}: {e}")

                delivery.attempted_at = now
                if any_success:
                    delivery.status = NotificationStatus.DELIVERED
                    delivery.delivered_at = now
                    delivery.error = None
                else:
                    delivery.status = NotificationStatus.FAILED
                    delivery.error = "all push attempts failed"

            await db.commit()
            logger.info(f"Delivery pass complete for notification {notification_id}.")
    except Exception as e:
        logger.exception(
            f"Critical failure in deliver_notification for {notification_id}: {e}"
        )
        raise
    finally:
        await db.close()


@celery_app.task
@async_task
async def check_notification_triggers():
    """Periodic task to process scheduled and recurring triggers."""
    from app.services.notification_manager import NotificationManager

    db, engine = get_async_session()
    try:
        async with db:
            # Inject the worker-scoped NullPool session so NotificationManager
            # never reaches for the global pooled engine (whose asyncpg
            # connections are bound to a different/closed event loop in a
            # prefork worker → "Future attached to a different loop").
            await NotificationManager.process_due_triggers(session=db)
    finally:
        await db.close()


@celery_app.task(
    name="app.workers.tasks.sync_active_integrations", bind=True, max_retries=1
)
@async_task
async def sync_active_integrations(self):
    """Periodic task to sync data from all active user integrations.

    Audit item C4 (overlapping sync beats): the 60-second Celery beat
    can overlap when a sync takes >60 s. Two workers both read
    ``last_synced_at``, both pull, both persist Observations + telemetry
    — no dedup anywhere in the sync path → duplicate clinical rows.

    Per-integration guard: each integration's sync is wrapped in a
    Redis-backed lock keyed by ``sync_lock:{integration_id}``. The lock
    is acquired with ``NX`` (only one writer) and a TTL of 600 s (sync
    hard timeout; longer than the per-integration 5-min sync budget
    plus a safety margin). If the lock can't be acquired, this
    integration is SKIPPED for this beat cycle — another worker is
    already syncing it.
    """
    logger.info("Starting integration sync cycle.")

    db, engine = get_async_session()
    try:
        async with db:
            # First ensure registry is initialized if not already (celery workers need this)
            await integration_registry.initialize(db)

            stmt = select(UserIntegration).where(
                UserIntegration.status == IntegrationStatus.ACTIVE
            )
            result = await db.execute(stmt)
            active_integrations = result.scalars().all()

            logger.info(
                f"Found {len(active_integrations)} active integrations to sync."
            )

            for integration in active_integrations:
                start_time = datetime.datetime.now(datetime.timezone.utc)

                # Rate-limit cooldown (item 1 of the integrations-sdk-
                # improvements plan): when an upstream returned 429 with a
                # Retry-After hint, ``run_sync`` wrote a Redis cooldown key
                # via ``set_rate_limit_cooldown``. While it exists, skip
                # this integration entirely — the upstream told us to back
                # off and re-hitting it every 60 s just makes it worse.
                from app.services.integration_sync_service import is_rate_limited

                try:
                    if await is_rate_limited(integration.id):
                        logger.debug(
                            "Skipping sync for %s (user %s): rate-limit cooldown active",
                            integration.provider,
                            integration.user_id,
                        )
                        continue
                except Exception:
                    # Redis down — degrade to "not rate limited" so the
                    # sync proceeds; the upstream will re-raise if it's
                    # still closed and the cooldown will be re-set.
                    pass

                # Check if it's time to sync based on user config interval (default to 15 if missing)
                sync_interval = 15
                if (
                    integration.user_config
                    and "sync_interval" in integration.user_config
                ):
                    sync_interval = int(integration.user_config["sync_interval"])

                if integration.last_synced_at:
                    next_sync = integration.last_synced_at + datetime.timedelta(
                        minutes=sync_interval
                    )
                    # Add a small buffer (e.g. 10 seconds) to avoid missing cycles due to slight execution delays
                    if start_time < (next_sync - datetime.timedelta(seconds=10)):
                        logger.debug(
                            f"Skipping sync for {integration.provider} (user {integration.user_id}). Next sync at {next_sync}"
                        )
                        continue

                provider = integration_registry.get_provider(integration.provider)
                if not provider:
                    logger.warning(
                        f"Provider not found for integration {integration.provider}"
                    )
                    continue

                logger.info(
                    f"Syncing integration {integration.provider} for user {integration.user_id}"
                )

                # Delegate to the shared sync pipeline (acquires the Redis
                # dedup lock internally, handles the 3-tier error contract,
                # writes the IntegrationSyncLog).
                from app.services.integration_sync_service import (
                    run_sync,
                    set_rate_limit_cooldown,
                )

                result = await run_sync(db, integration, provider, source="background")
                if result.status == "skipped":
                    logger.debug(
                        "Sync skipped (lock held) for %s (user %s)",
                        integration.provider,
                        integration.user_id,
                    )
                elif result.status == "failed":
                    logger.warning(
                        "Sync failed for %s (user %s): %s",
                        integration.provider,
                        integration.user_id,
                        result.error,
                    )
                    # If the upstream told us to wait, honour it so the
                    # next beat skips this integration until the cooldown
                    # expires. No-op when ``retry_after_seconds`` is None
                    # (manual raises / unknown upstream behaviour).
                    if result.retry_after_seconds is not None:
                        await set_rate_limit_cooldown(
                            integration.id, result.retry_after_seconds
                        )
                else:
                    logger.info(
                        "Sync %s for %s: pulled=%d fhir=%d telemetry=%d dropped=%d",
                        result.status,
                        integration.provider,
                        result.pulled,
                        result.fhir_persisted,
                        result.telemetry_persisted,
                        result.dropped_invalid,
                    )

    except Exception as e:
        logger.error(f"Critical error during integration sync cycle: {e}")
    finally:
        await db.close()


@celery_app.task(bind=True)
@async_task
async def migrate_biomarker_data(
    self, biomarker_id_str: str, tenant_id_str: str, to_telemetry: bool
):
    from uuid import UUID
    from sqlalchemy import select, func
    from sqlalchemy.orm.attributes import flag_modified
    from app.models.biomarker_model import BiomarkerDefinition, Unit
    from app.models.fhir.patient import Observation, Patient
    from app.models.telemetry_model import TelemetryDataModel

    logger = logging.getLogger(__name__)
    biomarker_id = UUID(biomarker_id_str)
    tenant_id = UUID(tenant_id_str)

    logger.info(
        f"Starting async migration for biomarker {biomarker_id} to_telemetry={to_telemetry}"
    )

    db, engine = get_async_session()
    try:
        async with db:
            # 1. Fetch Biomarker
            res = await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.id == biomarker_id
                )
            )
            db_biomarker = res.scalar_one_or_none()
            if not db_biomarker:
                logger.error(f"Biomarker {biomarker_id} not found during migration.")
                return {"status": "failed", "error": "Biomarker not found"}

            # Hard-block (plan state-biomarkers Step 11): STATE biomarkers
            # cannot migrate to telemetry. ``telemetry_data.value`` is Float
            # NOT NULL — categorical values have nowhere to go. The DB CHECK
            # constraint + the endpoint guard already prevent the toggle from
            # being queued; this defensive backstop keeps the task honest if
            # it's ever invoked directly (e.g. by a future script).
            if getattr(db_biomarker, "value_type", None) == "state" and to_telemetry:
                logger.error(
                    "Refusing to migrate STATE biomarker %s to telemetry — "
                    "telemetry_data.value is Float NOT NULL.",
                    db_biomarker.slug,
                )
                meta = dict(db_biomarker.meta_data or {})
                meta["migration_status"] = "failed"
                meta["migration_error"] = (
                    "STATE biomarkers cannot be telemetry (Float NOT NULL hypertable)"
                )
                db_biomarker.meta_data = meta
                flag_modified(db_biomarker, "meta_data")
                await db.commit()
                return {
                    "status": "failed",
                    "error": "STATE biomarkers cannot be telemetry",
                }

            # Update status to in_progress
            meta = dict(db_biomarker.meta_data or {})
            meta["migration_status"] = "in_progress"
            meta["migration_progress"] = 0
            if "migration_error" in meta:
                del meta["migration_error"]
            db_biomarker.meta_data = meta
            flag_modified(db_biomarker, "meta_data")
            await db.commit()

            slug = db_biomarker.slug.lower() if db_biomarker.slug else ""
            batch_size = 5000

            if to_telemetry:
                # Migrate FHIR -> Telemetry
                count_res = await db.execute(
                    select(func.count(Observation.id)).where(
                        Observation.biomarker_id == biomarker_id
                    )
                )
                total_records = count_res.scalar_one() or 0
                logger.info(
                    f"Total FHIR records to migrate to telemetry: {total_records}"
                )

                if total_records > 0:
                    processed = 0
                    while processed < total_records:
                        obs_res = await db.execute(
                            select(Observation)
                            .where(Observation.biomarker_id == biomarker_id)
                            .limit(batch_size)
                        )
                        observations = obs_res.scalars().all()

                        if not observations:
                            break

                        telemetry_records = []
                        obs_ids_to_delete = []

                        for obs in observations:
                            val = (
                                getattr(obs, "normalized_value", None)
                                or getattr(obs, "raw_value", None)
                                or (
                                    obs.value_quantity.get("value")
                                    if getattr(obs, "value_quantity", None)
                                    else None
                                )
                            )
                            if val is None:
                                # Long-format hypertable requires NOT NULL
                                # value; skip non-numeric observations.
                                continue

                            unit = None
                            if getattr(obs, "value_quantity", None):
                                unit = obs.value_quantity.get("unit")

                            # Long-format: one row per measurement, slug on
                            # the row. ``patient_id`` is carried over from
                            # the source observation so the inverse direction
                            # can attribute rows without device-id resolution.
                            telemetry_records.append(
                                TelemetryDataModel(
                                    tenant_id=obs.tenant_id,
                                    device_id="fhir_migration",
                                    timestamp=obs.effective_datetime,
                                    slug=slug,
                                    value=float(val),
                                    unit=unit,
                                    patient_id=getattr(obs, "patient_id", None),
                                )
                            )
                            obs_ids_to_delete.append(obs.id)

                        db.add_all(telemetry_records)
                        await db.execute(
                            delete(Observation).where(
                                Observation.id.in_(obs_ids_to_delete)
                            )
                        )

                        processed += len(observations)

                        # Update progress
                        progress = int((processed / total_records) * 100)
                        meta = dict(db_biomarker.meta_data or {})
                        meta["migration_status"] = "in_progress"
                        meta["migration_progress"] = progress
                        db_biomarker.meta_data = meta
                        flag_modified(db_biomarker, "meta_data")
                        await db.commit()

            else:
                # Migrate Telemetry -> FHIR.
                # Long-format hypertable: the query is a uniform
                # ``WHERE slug = :slug`` (no per-column branching), and
                # ``patient_id`` is persisted on each row at insert time —
                # so the fragile ``device_id → UserIntegration → user_id →
                # Patient`` attribution chain (audit C1) is no longer needed.
                # Rows lacking ``patient_id`` fall back to the single-patient
                # tenant default; if that's ambiguous they are skipped and
                # counted in ``meta["migration_skipped_no_patient"]``.
                stmt = select(TelemetryDataModel).where(
                    TelemetryDataModel.tenant_id == tenant_id,
                    TelemetryDataModel.slug == slug,
                )

                count_stmt = select(func.count(TelemetryDataModel.id)).where(
                    stmt.whereclause
                )
                count_res = await db.execute(count_stmt)
                total_records = count_res.scalar_one() or 0
                logger.info(
                    f"Total Telemetry records to migrate to FHIR: {total_records}"
                )

                if total_records > 0:
                    u_res = await db.execute(
                        select(Unit.symbol).where(
                            Unit.id == db_biomarker.preferred_unit_id
                        )
                    )
                    symbol = u_res.scalar_one_or_none() or ""

                    # Single-patient-tenant fallback for rows that lack a
                    # persisted patient_id (e.g. legacy mobile uploads that
                    # didn't send one). Only safe when the tenant has exactly
                    # one patient.
                    single_patient_res = await db.execute(
                        select(Patient.id).where(Patient.tenant_id == tenant_id)
                    )
                    all_tenant_patients = single_patient_res.scalars().all()
                    default_patient_id = (
                        all_tenant_patients[0]
                        if len(all_tenant_patients) == 1
                        else None
                    )

                    processed = 0
                    skipped_no_patient = 0
                    while processed < total_records:
                        tel_res = await db.execute(
                            stmt.limit(batch_size).offset(processed)
                        )
                        telemetry_records = tel_res.scalars().all()

                        if not telemetry_records:
                            break

                        fhir_records = []
                        for tr in telemetry_records:
                            # Long-format: value + unit live directly on
                            # the row.
                            val = tr.value

                            resolved_patient_id = tr.patient_id or default_patient_id

                            if resolved_patient_id is None:
                                # Skip — we cannot attribute this row
                                # without risking cross-patient corruption.
                                skipped_no_patient += 1
                                logger.debug(
                                    "Skipping telemetry row %s: no patient_id "
                                    "and tenant %s is multi-patient",
                                    tr.id,
                                    tenant_id,
                                )
                                # Still delete the row so it doesn't
                                # reappear on the next toggle.
                                await db.delete(tr)
                                continue

                            unit = tr.unit or symbol
                            obs = Observation(
                                tenant_id=tr.tenant_id,
                                patient_id=resolved_patient_id,
                                subject={
                                    "reference": f"Patient/{resolved_patient_id}"
                                },
                                status="final",
                                code={
                                    "coding": [
                                        {
                                            "system": db_biomarker.coding_system.fhir_system
                                            if db_biomarker.coding_system
                                            else "http://loinc.org",
                                            "code": db_biomarker.code
                                            or db_biomarker.slug,
                                            "display": db_biomarker.name,
                                        }
                                    ],
                                    "text": db_biomarker.name,
                                },
                                effective_datetime=tr.timestamp,
                                value_quantity={
                                    "value": float(val),
                                    "unit": unit,
                                },
                                raw_value=float(val),
                                normalized_value=float(val),
                                biomarker_id=db_biomarker.id,
                            )
                            fhir_records.append(obs)
                            # Long-format: one slug per row, so the row is
                            # fully consumed — delete it.
                            await db.delete(tr)

                        if fhir_records:
                            db.add_all(fhir_records)

                        processed += len(telemetry_records)

                        # Update progress
                        progress = int((processed / total_records) * 100)
                        meta = dict(db_biomarker.meta_data or {})
                        meta["migration_status"] = "in_progress"
                        meta["migration_progress"] = progress
                        if skipped_no_patient:
                            meta["migration_skipped_no_patient"] = (
                                skipped_no_patient
                            )
                        db_biomarker.meta_data = meta
                        flag_modified(db_biomarker, "meta_data")
                        await db.commit()

                    if skipped_no_patient:
                        logger.warning(
                            "Telemetry->FHIR migration for biomarker %s: "
                            "%d of %d rows skipped (could not attribute to a patient)",
                            biomarker_id,
                            skipped_no_patient,
                            total_records,
                        )

            # Mark as completed
            meta = dict(db_biomarker.meta_data or {})
            meta["migration_status"] = "completed"
            meta["migration_progress"] = 100
            if "migration_error" in meta:
                del meta["migration_error"]
            db_biomarker.meta_data = meta
            flag_modified(db_biomarker, "meta_data")
            await db.commit()
            logger.info(
                f"Migration completed successfully for biomarker {biomarker_id}"
            )

            return {"status": "success", "biomarker_id": str(biomarker_id)}

    except Exception as e:
        logger.exception(
            f"Error during async migration for biomarker {biomarker_id}: {e}"
        )
        async with db:
            # Try to mark as failed
            res = await db.execute(
                select(BiomarkerDefinition).where(
                    BiomarkerDefinition.id == biomarker_id
                )
            )
            b_err = res.scalar_one_or_none()
            if b_err:
                meta = dict(b_err.meta_data or {})
                meta["migration_status"] = "failed"
                meta["migration_error"] = str(e)
                meta["migration_progress"] = 0
                b_err.meta_data = meta
                flag_modified(b_err, "meta_data")
                await db.commit()
        raise
    finally:
        await db.close()


@celery_app.task(bind=True, max_retries=0)
@async_task
async def export_backup(self, job_id_str: str):
    """Run an export/backup job (FHIR-only, full ZIP, or catalog-only)."""
    from uuid import UUID
    from app.services.export_service import ExportService

    job_id = UUID(job_id_str)
    db, engine = get_async_session()
    try:
        async with db:
            svc = ExportService(db)
            await svc.run_export(job_id)
        return {"job_id": job_id_str, "status": "completed"}
    except Exception as e:
        logger.exception(f"export_backup {job_id_str} failed: {e}")
        return {"job_id": job_id_str, "status": "failed", "error": str(e)}
    finally:
        await db.close()


@celery_app.task(bind=True, max_retries=0)
@async_task
async def import_backup(
    self, job_id_str: str, archive_path: str, owner_id_str: str, config_json: str = "{}"
):
    """Run a backup/restore import job from a ZIP or bare JSON file."""
    from uuid import UUID
    from app.services.import_service import ImportService
    from app.schemas.import_data import FHIRImportConfig
    import json

    job_id = UUID(job_id_str)
    owner_id = UUID(owner_id_str)

    config_dict = json.loads(config_json)
    config = FHIRImportConfig(**config_dict) if config_dict else None

    db, engine = get_async_session()
    try:
        async with db:
            svc = ImportService(db)
            result = await svc.run_import(job_id, archive_path, owner_id, config=config)
        return {
            "job_id": job_id_str,
            "status": result.status.value,
            "processed": result.processed_records,
            "failed": result.failed_records,
        }
    except Exception as e:
        logger.exception(f"import_backup {job_id_str} failed: {e}")
        return {"job_id": job_id_str, "status": "failed", "error": str(e)}
    finally:
        await db.close()
        try:
            from pathlib import Path as _Path

            _Path(archive_path).unlink(missing_ok=True)
        except Exception:
            pass
