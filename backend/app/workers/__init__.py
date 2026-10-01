from .ai_tasks import check_medication_interactions, detect_anomalies, process_document
from .celery_app import celery_app

__all__ = [
    "celery_app",
    "check_medication_interactions",
    "detect_anomalies",
    "process_document",
]
