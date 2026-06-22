import asyncio
import os
import datetime
import functools
import logging
from pathlib import Path
from uuid import UUID
from typing import Optional, Dict, List, Any

from sqlalchemy import update, select, delete, and_
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.core.config import settings
from app.workers.celery_app import celery_app
from app.workers.task_logger import TaskLogger, TaskProgressTracker, TaskTimeoutMonitor
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from app.models.user_integration import UserIntegration
from app.models.enums import IntegrationStatus
from app.core.integration_registry import integration_registry
from app.services.ai_provider_service import AIProviderService
from app.services.medical_processing_service import MedicalProcessingService
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


def get_async_session():
    """Returns a new session with a fresh engine to avoid loop affinity issues"""
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    return session_factory(), engine


def async_task(func):
    """Decorator to run async functions in a thread and handle DB session cleanup"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(func(*args, **kwargs))
        finally:
            loop.close()

    return wrapper


@celery_app.task(bind=True, max_retries=3)
@async_task
async def ocr_document(
    self,
    document_id: str,
    file_path: str,
    tenant_id: str,
    user_id: Optional[str] = None,
):
    logger.info(f"Starting OCR for document {document_id} at {file_path}")
    doc_uuid = UUID(document_id)
    tenant_uuid = UUID(tenant_id)
    user_uuid = UUID(user_id) if user_id else None

    db, engine = get_async_session()
    task_logger = TaskLogger("ocr_document", document_id, tenant_uuid, db=db)
    progress_tracker = TaskProgressTracker(db=db, document_id=doc_uuid)

    try:
        async with db:
            await task_logger.log_start(filename=os.path.basename(file_path))

            # Initialize status
            await db.execute(
                update(DocumentModel)
                .where(DocumentModel.id == doc_uuid)
                .values(status="processing", progress=10)
            )
            await db.commit()

            # 1. Get OCR Processor via Unified Service
            ai_service = AIProviderService(db)
            ocr_processor = await ai_service.get_ocr_processor(tenant_uuid, user_uuid)
            await task_logger.log_progress(
                "config_loaded", 20, processor=ocr_processor.__class__.__name__
            )

            # 2. Verify File
            if not os.path.exists(file_path):
                error_msg = f"File {file_path} not found"
                await progress_tracker.update_document_status("failed", 0, error_msg)
                raise FileNotFoundError(error_msg)

            # 3. Extraction
            await task_logger.log_progress("ocr_start", 50)
            file_path_obj = Path(file_path)

            if file_path_obj.suffix.lower() in [".pdf", ".dcm"]:
                from app.processors.ocr.utils import convert_to_images

                images = await convert_to_images(file_path_obj)
                raw_text = await ocr_processor.extract_text_from_images(images)
            else:
                raw_text = await ocr_processor.extract_text(file_path_obj)

            # 4. Success Persistence
            await db.execute(
                update(DocumentModel)
                .where(DocumentModel.id == doc_uuid)
                .values(status="completed", progress=100, extracted_text=raw_text)
            )
            await db.commit()
            await task_logger.log_success()

            # 5. Check if we should trigger NLP Extraction
            await _check_trigger_cumulative(db, doc_uuid)

        return {"document_id": document_id, "status": "completed"}

    except Exception as e:
        logger.exception(f"OCR failed for doc {document_id}")
        await task_logger.log_error(e, "ocr_processing")
        async with db:
            await db.execute(
                update(DocumentModel)
                .where(DocumentModel.id == doc_uuid)
                .values(status="failed", progress=0, error_message=str(e))
            )
            await db.commit()
        raise
    finally:
        await engine.dispose()


async def _check_trigger_cumulative(db: AsyncSession, document_id: UUID):
    """Check if all relevant documents are processed to trigger NLP.

    Audit item C3 (TOCTOU race): previously this read the pending-doc
    count and fired ``cumulative_extraction.delay()`` only if no docs
    remained. Concurrent OCR completions (typical with multi-doc upload)
    all saw the same pending count → either nobody fired cumulative, or
    everybody fired it (doubles LLM cost + races biomarker auto-create).
    Now we acquire a per-examination Postgres advisory lock
    (``pg_try_advisory_xact_lock``) keyed on the exam id before checking
    the pending count. If the lock can't be acquired, another OCR
    completion is already mid-check — we skip and let that one fire.
    """
    doc_res = await db.execute(
        select(DocumentModel).where(DocumentModel.id == document_id)
    )
    doc = doc_res.scalar_one_or_none()

    if not doc or not doc.examination_id:
        return

    # Per-exam advisory lock. ``pg_try_advisory_xact_lock`` returns True
    # iff the lock was acquired (held until the surrounding transaction
    # commits/aborts). Hash the exam_id to a stable int64 key.
    from sqlalchemy import text

    lock_key = hash(str(doc.examination_id)) & 0x7FFFFFFFFFFFFFFF
    lock_res = await db.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"),
        {"k": lock_key},
    )
    lock_acquired = lock_res.scalar()
    if not lock_acquired:
        logger.info(
            "Another OCR completion is already checking exam %s; skipping "
            "cumulative trigger to avoid the TOCTOU race (audit C3).",
            doc.examination_id,
        )
        return

    # Check for pending/processing documents in this exam
    pending_res = await db.execute(
        select(DocumentModel).where(
            DocumentModel.examination_id == doc.examination_id,
            DocumentModel.include_in_extraction == True,
            DocumentModel.status.in_(["processing", "uploaded"]),
        )
    )
    pending_doc = pending_res.scalars().first()
    if pending_doc:
        logger.info(
            f"Exam {doc.examination_id} still has pending docs (e.g. {pending_doc.id}). Skipping cumulative extraction for now."
        )
        return

    # Trigger if any doc has text
    text_docs_res = await db.execute(
        select(DocumentModel).where(
            DocumentModel.examination_id == doc.examination_id,
            DocumentModel.include_in_extraction == True,
            DocumentModel.status.in_(["completed", "failed"]),
        )
    )
    docs = text_docs_res.scalars().all()

    # Use the owner of the document as the user_id for extraction
    user_id = str(doc.owner_id) if doc.owner_id else None

    if any(d.extracted_text and d.extracted_text.strip() for d in docs):
        logger.info(
            f"All docs for exam {doc.examination_id} finished. Triggering cumulative extraction."
        )
        cumulative_extraction.delay(str(doc.examination_id), user_id)
    else:
        logger.warning(
            f"All docs for exam {doc.examination_id} finished but no text was extracted. Triggering cumulative extraction anyway to handle metadata if needed."
        )
        cumulative_extraction.delay(str(doc.examination_id), user_id)


@celery_app.task(bind=True, max_retries=3)
@async_task
async def cumulative_extraction(
    self, examination_id: str, user_id: Optional[str] = None
):
    logger.info(f"Starting cumulative extraction for examination {examination_id}")
    exam_uuid = UUID(examination_id)
    user_uuid = UUID(user_id) if user_id else None

    db, engine = get_async_session()
    progress_tracker = TaskProgressTracker(db=db, examination_id=exam_uuid)
    task_logger = None

    try:
        async with db:
            # Resolve Tenant
            exam_res = await db.execute(
                select(ExaminationModel.tenant_id).where(
                    ExaminationModel.id == exam_uuid
                )
            )
            tenant_id = exam_res.scalar()
            if not tenant_id:
                logger.error(f"Examination {examination_id} not found or has no tenant")
                return

            task_logger = TaskLogger(
                "cumulative_extraction",
                examination_id,
                tenant_id,
                db=db,
            )
            await task_logger.log_start()

            # Pass user_uuid to processing service if we want it to be configuration aware
            # For now, MedicalProcessingService uses the exam's tenant,
            # but we should pass user_id to its methods that call AI.
            service = MedicalProcessingService(db)

            # We need to update run_extraction_pipeline to accept user_id
            await service.run_extraction_pipeline(
                exam_uuid, task_logger, progress_tracker, user_id=user_uuid
            )
            return {"examination_id": examination_id, "status": "completed"}
    except Exception as e:
        logger.exception(f"Extraction failed for exam {examination_id}")
        # Log to technical task logs too
        if task_logger:
            try:
                await task_logger.log_error(e, "cumulative_extraction")
            except:
                pass
        await progress_tracker.mark_failed(str(e))
        raise
    finally:
        await engine.dispose()


@celery_app.task(bind=True)
@async_task
async def check_medication_interactions(self, medications: list, user_id: str):
    from app.services.medication_interactor import MedicationInteractor

    interactor = MedicationInteractor()
    results = await interactor.check_interactions(medications)
    return {"user_id": user_id, "interactions": results}


@celery_app.task(bind=True)
@async_task
async def detect_anomalies(self, patient_id: str, biomarker_code: str = None):
    """Detect anomalies for a patient's biomarkers via the analytics service."""
    from sqlalchemy import select
    from app.models.fhir.patient import Observation
    from app.services.analytics_service import get_biomarker_anomalies

    db, engine = get_async_session()
    try:
        async with db:
            tenant_row = await db.execute(
                select(Observation.tenant_id)
                .where(
                    Observation.subject["reference"].as_string()
                    == f"Patient/{patient_id}"
                )
                .limit(1)
            )
            tenant_id = tenant_row.scalar_one_or_none()
            if not tenant_id:
                return {"patient_id": patient_id, "anomalies": []}

            result = await get_biomarker_anomalies(
                tenant_id=str(tenant_id),
                biomarker_codes=biomarker_code,
                patient_id=patient_id,
                db=db,
            )
            return {
                "patient_id": patient_id,
                "biomarker": biomarker_code,
                "anomalies": result.get("anomalies", []),
            }
    finally:
        await engine.dispose()


@celery_app.task
@async_task
async def cleanup_stuck_extractions():
    """Periodic task to mark long-stuck extractions as failed.

    Audit item A5: the threshold here must be **greater** than the Celery
    hard ``task_time_limit`` (900s = 15 min in ``celery_app.py``) so a
    task killed at exactly 15 min doesn't race with this cleanup. We
    use 20 min — a 5-minute safety margin beyond the hard kill.
    """
    db, engine = get_async_session()
    try:
        async with db:
            threshold = datetime.datetime.now(
                datetime.timezone.utc
            ) - datetime.timedelta(minutes=20)
            await db.execute(
                update(ExaminationModel)
                .where(
                    ExaminationModel.extraction_status.in_(
                        [
                            "aggregating",
                            "analyzing_text",
                            "clinical_analysis",
                            "defining_ontology",
                            "persisting_results",
                        ]
                    )
                )
                .where(ExaminationModel.updated_at < threshold)
                .values(
                    extraction_status="failed",
                    extraction_progress=0,
                    error_message="Task timeout",
                )
            )
            await db.commit()
    finally:
        await engine.dispose()


@celery_app.task
@async_task
async def deliver_notification(notification_id: str):
    """
    Final delivery attempt for a notification instance.
    Handles PUSH, EMAIL, and potentially SMS channels.
    """
    logger.info(f"Worker picking up delivery for notification {notification_id}")
    from app.services.notification_manager import NotificationManager
    from app.services.notification_service import NotificationService
    from app.services.webpush_service import send_web_push
    from app.models.notification import (
        Notification,
        NotificationStatus,
        NotificationChannel,
        NotificationSubscription,
    )

    db, engine = get_async_session()
    try:
        async with db:
            notif_uuid = UUID(notification_id)
            # 1. Fetch notification
            res = await db.execute(
                select(Notification).where(Notification.id == notif_uuid)
            )
            notif = res.scalar_one_or_none()
            if not notif:
                logger.error(
                    f"Notification {notification_id} not found in delivery worker."
                )
                return

            logger.info(
                f"Processing notification {notification_id} for patient {notif.patient_id} via {notif.channel}"
            )

            # Skip if already delivered/dismissed
            if notif.status != NotificationStatus.PENDING:
                logger.info(
                    f"Notification {notification_id} is already in state {notif.status}. Skipping."
                )
                return

            # Prepare payload
            payload = {
                "id": str(notif.id),
                "type": notif.type.value,
                "title": notif.title,
                "body": notif.body,
                "payload": notif.payload,
            }

            success = False
            # 2. Channel logic
            # For IN_APP, it's already "stored" in DB for polling or WebSocket
            # For PUSH, we need to find user subscriptions

            if notif.channel == NotificationChannel.PUSH:
                # Find users in the tenant
                from app.models.user_model import UserModel

                users_res = await db.execute(
                    select(UserModel.id).where(UserModel.tenant_id == notif.tenant_id)
                )
                user_ids = [u[0] for u in users_res.all()]
                logger.info(
                    f"Targeting {len(user_ids)} users in tenant {notif.tenant_id} for notification {notification_id}"
                )

                # 3. Web Push Delivery
                subs_res = await db.execute(
                    select(NotificationSubscription.subscription_data).where(
                        and_(
                            NotificationSubscription.user_id.in_(user_ids),
                            NotificationSubscription.is_active == True,
                        )
                    )
                )
                subscriptions = subs_res.scalars().all()
                logger.info(
                    f"Found {len(subscriptions)} active Web Push subscriptions for notification {notification_id}"
                )

                for i, sub_data in enumerate(subscriptions):
                    try:
                        push_ok = send_web_push(sub_data, payload)
                        if push_ok:
                            success = True
                            logger.info(
                                f"Successfully sent Web Push #{i + 1} for notification {notification_id}"
                            )
                        else:
                            logger.warning(
                                f"Failed to send Web Push #{i + 1} for notification {notification_id}"
                            )
                    except Exception as e:
                        logger.error(f"Error sending Web Push #{i + 1}: {e}")
            else:
                # For non-push channels (like IN_APP), we consider it delivered to the DB
                success = True

            # 4. Email Delivery (optional/fallback)
            # if notif.type in [NotificationType.BIOMARKER_ALERT]:
            #     # ... logic to send email ...
            #     pass

            # Update status
            notif.status = (
                NotificationStatus.DELIVERED if success else NotificationStatus.FAILED
            )
            notif.sent_at = datetime.datetime.now(datetime.timezone.utc)
            await db.commit()
            logger.info(
                f"Notification {notification_id} status updated to {notif.status}"
            )
    except Exception as e:
        logger.exception(
            f"Critical failure in deliver_notification for {notification_id}: {e}"
        )
        raise
    finally:
        await engine.dispose()


@celery_app.task
@async_task
async def check_notification_triggers():
    """Periodic task to process scheduled and recurring triggers."""
    from app.services.notification_manager import NotificationManager

    await NotificationManager.process_due_triggers()


# Helper for direct calls from API
def process_document_sync(
    document_id: str, file_path: str, tenant_id: str, user_id: Optional[str] = None
):
    return ocr_document(document_id, file_path, tenant_id, user_id)


@celery_app.task(bind=True, max_retries=3)
def process_document(
    self,
    document_id: str,
    file_path: str,
    tenant_id: str,
    user_id: Optional[str] = None,
):
    return ocr_document.delay(document_id, file_path, tenant_id, user_id)

@celery_app.task(name="app.workers.tasks.sync_active_integrations", bind=True, max_retries=1)
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

            stmt = select(UserIntegration).where(UserIntegration.status == IntegrationStatus.ACTIVE)
            result = await db.execute(stmt)
            active_integrations = result.scalars().all()

            logger.info(f"Found {len(active_integrations)} active integrations to sync.")

            for integration in active_integrations:
                # Per-integration lock (audit C4). ``NX`` = set-if-not-exists,
                # ``EX`` = expire-after. ``redis_client.set`` returns True iff
                # the lock was acquired.
                lock_key = f"sync_lock:{integration.id}"
                try:
                    from app.core.redis import redis_client

                    acquired = await redis_client.set(lock_key, "1", nx=True, ex=600)
                except Exception as lock_err:
                    # Redis unavailable — degrade to "always sync" (legacy
                    # behaviour) but log the gap loudly.
                    logger.warning(
                        "Could not acquire Redis lock for integration %s (Redis down? %s); "
                        "proceeding without dedup guard. This may cause duplicate writes under "
                        "overlapping beats (audit C4).",
                        integration.id,
                        lock_err,
                    )
                    acquired = True  # legacy mode

                if not acquired:
                    logger.info(
                        "Skipping sync for integration %s (provider=%s) — another worker "
                        "holds the sync_lock (audit C4).",
                        integration.id,
                        integration.provider,
                    )
                    continue

                try:
                    start_time = datetime.datetime.now(datetime.timezone.utc)

                    # Check if it's time to sync based on user config interval (default to 15 if missing)
                    sync_interval = 15
                    if integration.user_config and "sync_interval" in integration.user_config:
                        sync_interval = int(integration.user_config["sync_interval"])

                    if integration.last_synced_at:
                        next_sync = integration.last_synced_at + datetime.timedelta(minutes=sync_interval)
                        # Add a small buffer (e.g. 10 seconds) to avoid missing cycles due to slight execution delays
                        if start_time < (next_sync - datetime.timedelta(seconds=10)):
                            logger.debug(f"Skipping sync for {integration.provider} (user {integration.user_id}). Next sync at {next_sync}")
                            continue

                    provider = integration_registry.get_provider(integration.provider)
                    if not provider:
                        logger.warning(f"Provider not found for integration {integration.provider}")
                        continue

                    logger.info(f"Syncing integration {integration.provider} for user {integration.user_id}")

                    from integrations.sdk.exceptions import IntegrationAuthError, IntegrationRateLimitError

                    # Pull data
                    observations_data = await provider.pull_data(integration)

                    observations = []
                    dropped_invalid = 0
                    if observations_data:
                        logger.info(f"Pulled {len(observations_data)} observations from {integration.provider}")

                        from app.models.fhir import Observation

                        # Convert to ORM models BEFORE passing to mapping
                        for obs_data in observations_data:
                            obs_dict = obs_data.model_dump(exclude_unset=True) if hasattr(obs_data, "model_dump") else obs_data.dict(exclude_unset=True) if hasattr(obs_data, "dict") else obs_data
                            obs = Observation(**obs_dict)
                            observations.append(obs)

                        from app.services.fhir_service import map_observations_to_biomarkers
                        map_result = await map_observations_to_biomarkers(db, observations)
                        dropped_invalid = (
                            map_result.get("dropped_invalid", 0)
                            if isinstance(map_result, dict)
                            else 0
                        )

                    # Route telemetry-class observations to the TimescaleDB
                    # hypertable. The split is shared with manual sync, webhook,
                    # and the bridge provider via ``apply_telemetry_split``.
                    telemetry_count = 0
                    fhir_count = 0
                    if observations:
                        from app.services.integration_sync_service import (
                            apply_telemetry_split,
                        )
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

                    # Push data (for dev dummy)
                    await provider.push_data(integration, {"status": "sync_started"})

                    # Update sync time
                    integration.last_synced_at = datetime.datetime.now(datetime.timezone.utc)

                    # If validation dropped observations, surface it in the
                    # sync log so the UI/admin can see partial-success.
                    sync_status = "success" if dropped_invalid == 0 else "partial"
                    error_msg = (
                        f"{dropped_invalid} of {len(observations_data) if observations_data else 0} "
                        "pulled observations failed FHIR validation and were dropped"
                        if dropped_invalid
                        else None
                    )

                    # Log sync
                    from app.models.user_integration import IntegrationSyncLog
                    sync_log = IntegrationSyncLog(
                        integration_id=integration.id,
                        tenant_id=integration.tenant_id,
                        status=sync_status,
                        records_synced=telemetry_count + fhir_count,
                        started_at=start_time,
                        completed_at=integration.last_synced_at,
                        error_message=error_msg,
                    )
                    db.add(sync_log)

                    await db.commit()

                except IntegrationAuthError as e:
                    logger.warning(f"Auth error for integration {integration.provider} (user {integration.user_id}): {e}")

                    if integration.is_debug_enabled and hasattr(provider, "log_debug_payload"):
                        try:
                            await provider.log_debug_payload(integration, "Auth Error (Background)", {"error": str(e)}, level="error")
                        except Exception:
                            pass

                    # Update integration status to ERROR so we stop hammering it until user fixes it
                    integration.status = IntegrationStatus.ERROR

                    from app.models.user_integration import IntegrationSyncLog
                    sync_log = IntegrationSyncLog(
                        integration_id=integration.id,
                        tenant_id=integration.tenant_id,
                        status="failed",
                        records_synced=0,
                        started_at=start_time,
                        completed_at=datetime.datetime.now(datetime.timezone.utc),
                        error_message=str(e)
                    )
                    db.add(sync_log)
                    await db.commit()

                except IntegrationRateLimitError as e:
                    logger.warning(f"Rate limit hit for {integration.provider} (user {integration.user_id}): {e}")

                    if integration.is_debug_enabled and hasattr(provider, "log_debug_payload"):
                        try:
                            await provider.log_debug_payload(integration, "Rate Limit Error (Background)", {"error": str(e)}, level="warning")
                        except Exception:
                            pass

                    # Log the delay but don't mark as error so we try again later
                    from app.models.user_integration import IntegrationSyncLog
                    sync_log = IntegrationSyncLog(
                        integration_id=integration.id,
                        tenant_id=integration.tenant_id,
                        status="failed",
                        records_synced=0,
                        started_at=start_time,
                        completed_at=datetime.datetime.now(datetime.timezone.utc),
                        error_message="Rate Limit Exceeded. Will retry later."
                    )
                    db.add(sync_log)
                    await db.commit()

                except Exception as e:
                    logger.error(f"Error syncing integration {integration.provider} for user {integration.user_id}: {e}")

                    if integration.is_debug_enabled and hasattr(provider, "log_debug_payload"):
                        try:
                            await provider.log_debug_payload(integration, "Sync Error (Background)", {"error": str(e)}, level="error")
                        except Exception:
                            pass

                    # Log failure
                    from app.models.user_integration import IntegrationSyncLog
                    sync_log = IntegrationSyncLog(
                        integration_id=integration.id,
                        tenant_id=integration.tenant_id,
                        status="failed",
                        records_synced=0,
                        started_at=datetime.datetime.now(datetime.timezone.utc),
                        completed_at=datetime.datetime.now(datetime.timezone.utc),
                        error_message=str(e)
                    )
                    db.add(sync_log)
                    await db.commit()

                finally:
                    # Release the lock. We use ``delete`` (not a Lua compare-
                    # and-delete) for simplicity; the EX=600 fallback handles
                    # the case where this finally block never runs (worker
                    # crash mid-sync).
                    if acquired:
                        try:
                            from app.core.redis import redis_client

                            await redis_client.delete(lock_key)
                        except Exception:
                            # Lock will expire via TTL — safe to ignore.
                            pass

    except Exception as e:
        logger.error(f"Critical error during integration sync cycle: {e}")
    finally:
        await engine.dispose()
@celery_app.task(bind=True)
@async_task
async def migrate_biomarker_data(
    self, biomarker_id_str: str, tenant_id_str: str, to_telemetry: bool
):
    import logging
    from uuid import UUID
    from sqlalchemy import select, delete, func
    from sqlalchemy.orm.attributes import flag_modified
    from app.models.biomarker_model import BiomarkerDefinition, Unit
    from app.models.fhir.patient import Observation, Patient
    from app.models.telemetry_model import TelemetryDataModel

    logger = logging.getLogger(__name__)
    biomarker_id = UUID(biomarker_id_str)
    tenant_id = UUID(tenant_id_str)

    logger.info(f"Starting async migration for biomarker {biomarker_id} to_telemetry={to_telemetry}")

    db, engine = get_async_session()
    try:
        async with db:
            # 1. Fetch Biomarker
            res = await db.execute(
                select(BiomarkerDefinition).where(BiomarkerDefinition.id == biomarker_id)
            )
            db_biomarker = res.scalar_one_or_none()
            if not db_biomarker:
                logger.error(f"Biomarker {biomarker_id} not found during migration.")
                return {"status": "failed", "error": "Biomarker not found"}

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
                    select(func.count(Observation.id)).where(Observation.biomarker_id == biomarker_id)
                )
                total_records = count_res.scalar_one() or 0
                logger.info(f"Total FHIR records to migrate to telemetry: {total_records}")

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
                            val = getattr(obs, "normalized_value", None) or getattr(obs, "raw_value", None) or (obs.value_quantity.get("value") if getattr(obs, "value_quantity", None) else None)
                            
                            hr = val if slug == "8867-4" or "heart-rate" in slug else None
                            steps = val if slug == "41950-7" or "steps" in slug else None
                            cal = val if "calories" in slug else None
                            
                            data_payload = {}
                            if not hr and not steps and not cal:
                                data_payload[slug] = val
                                data_payload[f"{slug}_unit"] = obs.value_quantity.get("unit", "") if getattr(obs, "value_quantity", None) else ""

                            telemetry_records.append(TelemetryDataModel(
                                tenant_id=obs.tenant_id,
                                device_id="fhir_migration",
                                timestamp=obs.effective_datetime,
                                heart_rate=hr,
                                steps=steps,
                                calories=cal,
                                data=data_payload if data_payload else None
                            ))
                            obs_ids_to_delete.append(obs.id)
                        
                        db.add_all(telemetry_records)
                        await db.execute(delete(Observation).where(Observation.id.in_(obs_ids_to_delete)))
                        
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
                # Audit item C1: the original implementation picked the
                # tenant's first patient and assigned ALL migrated
                # observations to them — silently wrong in any multi-
                # patient tenant (cross-patient data corruption).
                #
                # We now resolve the patient per telemetry row via
                # ``TelemetryDataModel.device_id`` → ``UserIntegration``
                # (instance_name match) → ``user_id`` → ``Patient`` where
                # ``user_id == that AND tenant_id == tenant``. Rows that
                # can't be attributed to exactly one patient are skipped
                # and counted in ``meta["migration_skipped_no_patient"]``
                # so the UI/admin can see the partial-success.
                stmt = select(TelemetryDataModel).where(TelemetryDataModel.tenant_id == tenant_id)
                if slug == "8867-4" or "heart-rate" in slug:
                    stmt = stmt.where(TelemetryDataModel.heart_rate.is_not(None))
                elif slug == "41950-7" or "steps" in slug:
                    stmt = stmt.where(TelemetryDataModel.steps.is_not(None))
                elif "calories" in slug:
                    stmt = stmt.where(TelemetryDataModel.calories.is_not(None))
                else:
                    stmt = stmt.where(TelemetryDataModel.data.has_key(slug))

                # Unfortunately, counting JSONB keys is complex across rows, but we can count total matches
                count_stmt = select(func.count(TelemetryDataModel.id)).where(stmt.whereclause)
                count_res = await db.execute(count_stmt)
                total_records = count_res.scalar_one() or 0
                logger.info(f"Total Telemetry records to migrate to FHIR: {total_records}")

                if total_records > 0:
                    u_res = await db.execute(select(Unit.symbol).where(Unit.id == db_biomarker.preferred_unit_id))
                    symbol = u_res.scalar_one_or_none() or ""

                    # Pre-build a device_id -> patient_id resolver. We
                    # load all tenant UserIntegrations once (typically a
                    # small number) and all tenant Patients linked via
                    # user_id, then join them in-memory.
                    from app.models.user_integration import UserIntegration as _UInt
                    from app.models.user_model import UserModel as _UserM

                    uint_res = await db.execute(
                        select(_UInt.id, _UInt.instance_name, _UInt.provider, _UInt.user_id).where(
                            _UInt.tenant_id == tenant_id
                        )
                    )
                    uint_rows = uint_res.all()
                    # Map device_id (instance_name OR provider) -> user_id
                    device_to_user: dict[str, Any] = {}
                    for _id, instance_name, provider_name, user_id in uint_rows:
                        if instance_name:
                            device_to_user.setdefault(instance_name, user_id)
                        if provider_name:
                            device_to_user.setdefault(provider_name, user_id)
                    # "fhir_migration" was the historical device_id; treat
                    # it as "no attribution possible" (we can't know who
                    # the row belonged to).
                    device_to_user.pop("fhir_migration", None)

                    # Load all tenant patients that have user_id set.
                    pat_res = await db.execute(
                        select(Patient.id, Patient.user_id).where(
                            Patient.tenant_id == tenant_id,
                            Patient.user_id.is_not(None),
                        )
                    )
                    user_to_patient: dict[Any, Any] = {
                        user_id: patient_id
                        for patient_id, user_id in pat_res.all()
                        if user_id is not None
                    }

                    # Resolve a default patient for the "no device_id on
                    # telemetry row" case: only safe if the tenant has
                    # exactly ONE patient. Otherwise we MUST skip to avoid
                    # the cross-patient attribution bug.
                    single_patient_res = await db.execute(
                        select(Patient.id).where(Patient.tenant_id == tenant_id)
                    )
                    all_tenant_patients = single_patient_res.scalars().all()
                    default_patient_id = (
                        all_tenant_patients[0]
                        if len(all_tenant_patients) == 1
                        else None
                    )

                    # If we have NO way to attribute any row, abort early
                    # with a clear error in meta_data.
                    if not device_to_user and not default_patient_id:
                        meta = dict(db_biomarker.meta_data or {})
                        meta["migration_status"] = "failed"
                        meta["migration_error"] = (
                            "Cannot attribute telemetry rows to a patient: no "
                            "UserIntegrations in tenant and tenant has != 1 patient. "
                            "Telemetry rows require a device_id that maps to a "
                            "UserIntegration, OR a single-patient tenant."
                        )
                        meta["migration_progress"] = 0
                        db_biomarker.meta_data = meta
                        flag_modified(db_biomarker, "meta_data")
                        await db.commit()
                        logger.error(
                            f"Aborting telemetry->FHIR migration for biomarker {biomarker_id}: "
                            f"{meta['migration_error']}"
                        )
                    else:
                        processed = 0
                        skipped_no_patient = 0
                        while processed < total_records:
                            tel_res = await db.execute(stmt.limit(batch_size).offset(processed))
                            telemetry_records = tel_res.scalars().all()

                            if not telemetry_records:
                                break

                            fhir_records = []
                            for tr in telemetry_records:
                                if slug == "8867-4" or "heart-rate" in slug:
                                    val = tr.heart_rate
                                    tr.heart_rate = None
                                elif slug == "41950-7" or "steps" in slug:
                                    val = tr.steps
                                    tr.steps = None
                                elif "calories" in slug:
                                    val = tr.calories
                                    tr.calories = None
                                else:
                                    val = tr.data.get(slug) if tr.data else None
                                    if tr.data and slug in tr.data:
                                        del tr.data[slug]
                                        flag_modified(tr, "data")

                                # Resolve patient for this row.
                                # Priority: device_id → user_id → patient_id;
                                # fallback: single-patient tenant default.
                                resolved_patient_id = None
                                if tr.device_id and tr.device_id in device_to_user:
                                    uid = device_to_user[tr.device_id]
                                    resolved_patient_id = user_to_patient.get(uid)
                                if resolved_patient_id is None:
                                    resolved_patient_id = default_patient_id

                                if resolved_patient_id is None:
                                    # Skip — we cannot attribute this row
                                    # without risking cross-patient
                                    # corruption (audit C1).
                                    skipped_no_patient += 1
                                    logger.debug(
                                        "Skipping telemetry row %s: device_id=%s "
                                        "does not map to a patient in tenant %s",
                                        tr.id,
                                        tr.device_id,
                                        tenant_id,
                                    )
                                    # Still clear out the value so the row
                                    # can be pruned if empty.
                                elif val is not None:
                                    obs = Observation(
                                        tenant_id=tr.tenant_id,
                                        subject={"reference": f"Patient/{resolved_patient_id}"},
                                        status="final",
                                        code={
                                            "coding": [{
                                                "system": db_biomarker.coding_system.fhir_system if db_biomarker.coding_system else "http://loinc.org",
                                                "code": db_biomarker.code or db_biomarker.slug,
                                                "display": db_biomarker.name
                                            }],
                                            "text": db_biomarker.name
                                        },
                                        effective_datetime=tr.timestamp,
                                        value_quantity={
                                            "value": float(val) if val is not None else None,
                                            "unit": symbol
                                        },
                                        raw_value=float(val) if val is not None else None,
                                        normalized_value=float(val) if val is not None else None,
                                        biomarker_id=db_biomarker.id
                                    )
                                    fhir_records.append(obs)

                                is_empty = (
                                    tr.heart_rate is None and
                                    tr.steps is None and
                                    tr.calories is None and
                                    (tr.data is None or len(tr.data) == 0)
                                )
                                if is_empty:
                                    await db.delete(tr)

                            if fhir_records:
                                db.add_all(fhir_records)

                            processed += len(telemetry_records)

                            progress = int((processed / total_records) * 100)
                            meta = dict(db_biomarker.meta_data or {})
                            meta["migration_status"] = "in_progress"
                            meta["migration_progress"] = progress
                            if skipped_no_patient:
                                meta["migration_skipped_no_patient"] = skipped_no_patient
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
            logger.info(f"Migration completed successfully for biomarker {biomarker_id}")

            return {"status": "success", "biomarker_id": str(biomarker_id)}
            
    except Exception as e:
        logger.exception(f"Error during async migration for biomarker {biomarker_id}: {e}")
        async with db:
            # Try to mark as failed
            res = await db.execute(select(BiomarkerDefinition).where(BiomarkerDefinition.id == biomarker_id))
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
        await engine.dispose()


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
        await engine.dispose()


@celery_app.task(bind=True, max_retries=0)
@async_task
async def import_backup(self, job_id_str: str, archive_path: str, owner_id_str: str, config_json: str = "{}"):
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
        await engine.dispose()
        try:
            from pathlib import Path as _Path

            _Path(archive_path).unlink(missing_ok=True)
        except Exception:
            pass
