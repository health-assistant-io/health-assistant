"""Data-lifecycle Celery tasks: retention policies and orphan cleanup.

Two named, scheduled jobs — the only writers of bounded-retention policy:

* :func:`apply_retention_policies` — reads `retention_policies` and enforces
  every enabled window (``delete`` or ``anonymize``). One source: the table,
  never a hard-coded constant.
* :func:`purge_orphaned_lifecycle_rows` — the ``TenantMixin`` docstring's
  promise made code: hypertable rows carry no FK, so a tenant/patient delete
  leaves telemetry rows behind; push registrations and device rows have the
  same restore-time risk. Idempotent.

Checkpoint pruning stays in :mod:`app.workers.ai_tasks` but now reads its
policy row through the same service.
"""

from celery.utils.log import get_task_logger

from app.workers.celery_app import celery_app
from app.workers.tasks import async_task, get_async_session

logger = get_task_logger(__name__)


@celery_app.task
@async_task
async def apply_retention_policies():
    """Enforce every enabled retention policy (daily beat)."""
    from app.services.data_lifecycle.retention_service import run_retention

    db, _engine = get_async_session()
    try:
        result = await run_retention(db)
        logger.info(
            "Retention pass deleted/anonymized %d row(s) across %d table(s)",
            result["total_rows"],
            len(result["tables"]),
        )
        return result
    finally:
        await db.close()


@celery_app.task
@async_task
async def purge_orphaned_lifecycle_rows():
    """Purge telemetry/push rows whose tenant, patient or user is gone."""
    from app.services.data_lifecycle.retention_service import purge_orphaned_lifecycle_rows as purge

    result = await purge()
    if any(result.values()):
        logger.info("Purged orphaned lifecycle rows: %s", result)
    return result
