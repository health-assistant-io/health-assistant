"""Audit logging service — writes provenance records for clinical writes.

Audit B12: the ``AuditLog`` table existed but was never written. ``created_by``
and ``updated_by`` on FHIR services were always NULL — no provenance trail for
who created/modified/deleted a clinical resource.

This module provides a single helper, ``log_audit_action``, that the FHIR and
clinical endpoints call after a successful write. The helper:

- Opens its own short-lived session (so the audit row is committed even if the
  caller later rolls back — the fact that the write *happened* is itself the
  audit fact of interest).
- Never raises — an audit-logging failure must not break the user's request.
- Accepts ``old_value`` / ``new_value`` as dicts and stores them as JSONB for
  full diff capability.
- Uses ``tenant_id = None`` for system-level actions so global catalog writes
  still get a trail.
"""
import logging
from typing import Any, Dict, Optional
from uuid import UUID

from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal
from app.models.audit_model import AuditLog

logger = logging.getLogger(__name__)


async def log_audit_action(
    *,
    tenant_id: Optional[UUID],
    user_id: Optional[UUID],
    action: str,
    resource_type: str,
    resource_id: Optional[UUID] = None,
    old_value: Optional[Dict[str, Any]] = None,
    new_value: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist an ``AuditLog`` row.

    Parameters mirror the ``AuditLog`` columns. ``old_value``/``new_value``
    are stored as JSONB; pass ``to_dict()`` snapshots for full diffs. The
    function is best-effort: any failure is logged at WARNING and swallowed
    so it can never break the calling request.
    """
    if not DATABASE_AVAILABLE:
        return

    try:
        entry = AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action[:100],
            resource_type=resource_type[:100],
            resource_id=resource_id,
            old_value=old_value,
            new_value=new_value,
        )
        async with AsyncSessionLocal() as session:
            session.add(entry)
            await session.commit()
    except Exception as e:
        logger.warning(
            "Failed to write AuditLog (action=%s resource=%s/%s): %s",
            action,
            resource_type,
            resource_id,
            e,
            exc_info=True,
        )
