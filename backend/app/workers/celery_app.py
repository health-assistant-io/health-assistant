from celery import Celery
from app.core.config import settings
from app.core.logging_setup import setup_logging

# Configure logging for worker
setup_logging(log_name="celery", debug=settings.DEBUG)

celery_app = Celery(
    "health_assistant_workers",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
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
            "task": "app.workers.tasks.cleanup_stuck_extractions",
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
    },
)
