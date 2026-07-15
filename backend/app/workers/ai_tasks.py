"""AI-related Celery tasks: OCR, cumulative extraction, anomaly/interaction
checks, and stuck-extraction cleanup.

Extracted from ``app/workers/tasks.py`` (Phase 7). The async-bridge helpers
(``get_async_session`` / ``async_task``) remain in ``tasks.py`` and are
imported here — moving them out would break non-AI task tests that patch
``app.workers.tasks.get_async_session``.

Celery task names now derive from this module (e.g.
``app.workers.ai_tasks.ocr_document``); ``celery_app.py``'s ``include`` and
beat schedule were updated accordingly.
"""

import datetime
import os
from pathlib import Path
from typing import Optional
from uuid import UUID

from celery.utils.log import get_task_logger
from sqlalchemy import select, update

from app.ai.pipeline.service import MedicalProcessingService
from app.ai.providers.service import AIProviderService
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from app.workers.celery_app import celery_app
from app.workers.task_logger import TaskLogger, TaskProgressTracker
from app.workers.tasks import async_task, get_async_session

logger = get_task_logger(__name__)


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
                from app.ai.processors.ocr.utils import convert_to_images

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
        await db.close()


async def _check_trigger_cumulative(db, document_id: UUID):
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
        # All docs finished OCR but none produced any text (blank pages,
        # OCR failures, image-only PDFs that tesseract couldn't read, etc.).
        # Skip the LLM call — it would consume tokens and time to produce
        # an empty / hallucinated impressions string. Mark the exam
        # completed so the UI shows it done. The advisory lock and outer
        # transaction commit this update.
        logger.warning(
            f"Exam {doc.examination_id} finished but no text was extracted "
            f"across {len(docs)} docs; marking completed without invoking LLM."
        )
        from app.models.examination_model import ExaminationModel

        await db.execute(
            update(ExaminationModel)
            .where(ExaminationModel.id == doc.examination_id)
            .values(
                extraction_status="completed",
                impressions="",
            )
        )


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
        await db.close()


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
        await db.close()


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
        await db.close()


# Helper for direct calls from API
def process_document_sync(
    document_id: str, file_path: str, tenant_id: str, user_id: Optional[str] = None
):
    """In-process OCR fallback for when Celery/Redis is unavailable (audit C10).

    The upload endpoint (``documents.trigger_extraction``) normally enqueues
    the OCR pipeline via ``ocr_document.delay(...)``. When Celery can't reach
    the broker, it instead schedules this through FastAPI ``BackgroundTasks``
    so a single-node deployment still extracts without a worker. Calling the
    Celery task object directly runs its body **synchronously** in the current
    process (Celery's ``Task.__call__``), so despite the ``async def`` body the
    caller blocks until extraction completes — the name is intentional.
    """
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
