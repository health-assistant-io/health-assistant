from .celery_app import celery_app
from .tasks import process_document, check_medication_interactions, detect_anomalies

__all__ = [
    "celery_app",
    "process_document",
    "check_medication_interactions",
    "detect_anomalies",
]
