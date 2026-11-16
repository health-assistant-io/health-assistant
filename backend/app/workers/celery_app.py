import logging
import os

from celery import Celery
from celery.signals import worker_process_init

from app.core.config import settings
from app.core.logging_setup import setup_logging

# Configure logging for worker. HA_LOG_NAME lets each celery process (worker /
# beat / flower) write to its own file via Procfile.dev; falls back to "celery".
setup_logging(log_name=os.getenv("HA_LOG_NAME", "celery"), debug=settings.DEBUG)
logger = logging.getLogger(__name__)


@worker_process_init.connect
def _init_worker_object_storage(**_: object) -> None:
    """Probe and register the object store in every worker process.

    A Celery worker is a separate process/image: there is no ``app.state``.
    The same ``HA_*`` env resolves the same backend, and a missing bucket,
    container or credential fails the worker start loudly instead of failing
    the first OCR/export task. ``current_store`` is a process-local singleton.
    """
    from app.storage.bootstrap import current_store

    current_store(settings)
    logger.info("Worker object storage initialised (backend=%s)", current_store().backend)


celery_app = Celery(
    "health_assistant_workers",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks", "app.workers.ai_tasks", "app.workers.lifecycle"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=900,  # 15 minutes max per task
    task_soft_time_limit=840,  # Warning at 14 minutes
    beat_schedule={
        "cleanup-stuck-extractions-every-5-minutes": {
            "task": "app.workers.ai_tasks.cleanup_stuck_extractions",
            "schedule": 300.0,  # 5 minutes
        },
        "check-notification-triggers-every-minute": {
            "task": "app.workers.tasks.check_notification_triggers",
            "schedule": 60.0,  # 1 minute
        },
        "sync-active-integrations-every-minute": {
            "task": "app.workers.tasks.sync_active_integrations",
            "schedule": 60.0,  # 1 minute
        },
        "expire-proposals-every-15-minutes": {
            "task": "app.workers.tasks.expire_proposals",
            "schedule": 900.0,  # 15 minutes
        },
        "expire-patient-access-grants-every-15-minutes": {
            "task": "app.workers.tasks.expire_patient_access_grants",
            "schedule": 900.0,  # 15 minutes — read paths are safe regardless
        },
        "expire-access-elevations-every-15-minutes": {
            "task": "app.workers.tasks.expire_access_elevations",
            "schedule": 900.0,  # 15 minutes — the consultation predicate is safe regardless
        },
        "prune-checkpoints-daily": {
            "task": "app.workers.ai_tasks.prune_checkpoint_threads",
            "schedule": 86400.0,  # daily — checkpoint rows grow unboundedly
        },
        "apply-retention-policies-daily": {
            "task": "app.workers.lifecycle.apply_retention_policies",
            "schedule": 86400.0,  # daily — policy rows own every cutoff
        },
        "purge-orphaned-lifecycle-rows-daily": {
            "task": "app.workers.lifecycle.purge_orphaned_lifecycle_rows",
            "schedule": 86400.0,  # daily — hypertable/push orphan cleanup
        },
        "sync-seed-sources-every-6h": {
            "task": "app.workers.tasks.sync_seed_sources",
            "schedule": 21600.0,  # 6 hours — remote packs are digest-pinned
            #   and approval-gated; startup never fetches (ADR 0056 Phase 3)
        },
    },
)
