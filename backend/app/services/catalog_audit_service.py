"""Catalog audit logging (Phase B).

Appends a :class:`~app.models.catalog_audit_log.CatalogAuditLog` row for every
catalog create / update / delete / promote / demote. Recording is **best-effort**:
a failure is logged and never aborts the parent catalog write (the catalog row
has already been committed by the time ``record`` is called).

Item identity (``item_name``) is denormalized so the trail survives deletion of
the catalog row; ``user_email`` is denormalized so it survives user deletion.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog_audit_model import CatalogAuditLog

logger = logging.getLogger(__name__)


def _label(obj: Any) -> str:
    """Best-effort human label for the item snapshot."""
    for attr in ("name", "slug", "code"):
        val = getattr(obj, attr, None)
        if val:
            return str(val)
    return ""


async def record(
    db: AsyncSession,
    *,
    actor: Any,
    catalog_type: str,
    item_id: UUID,
    item_name: str = "",
    operation: str,
    from_scope: Optional[str] = None,
    to_scope: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """Append one audit row. Best-effort — swallows + logs exceptions."""
    try:
        entry = CatalogAuditLog(
            tenant_id=getattr(actor, "tenant_id", None),
            user_id=getattr(actor, "user_id", None),
            user_email=getattr(actor, "sub", None) or "",
            catalog_type=catalog_type,
            item_id=item_id,
            item_name=item_name,
            operation=operation,
            from_scope=from_scope,
            to_scope=to_scope,
            details=details,
        )
        db.add(entry)
        await db.commit()
    except Exception:
        # Never abort the parent operation because the audit trail could not be
        # written. The catalog change is already durable.
        logger.warning(
            "catalog audit record failed (type=%s item=%s op=%s)",
            catalog_type,
            item_id,
            operation,
            exc_info=True,
        )


async def record_from_obj(
    db: AsyncSession,
    *,
    actor: Any,
    catalog_type: str,
    obj: Any,
    operation: str,
    from_scope: Optional[str] = None,
    to_scope: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """Convenience: derive ``item_id`` + ``item_name`` from the ORM object."""
    to = to_scope
    if to is None and operation == "create":
        scope = getattr(obj, "scope", None)
        to = scope.value if scope is not None else None
    await record(
        db,
        actor=actor,
        catalog_type=catalog_type,
        item_id=obj.id,
        item_name=_label(obj),
        operation=operation,
        from_scope=from_scope,
        to_scope=to,
        details=details,
    )


async def list_history(
    db: AsyncSession,
    *,
    tenant_id: Optional[UUID],
    catalog_type: str,
    item_id: UUID,
    limit: int = 100,
) -> list[CatalogAuditLog]:
    """Return the audit trail for one item, newest-first, tenant-scoped.

    System-scope items (``tenant_id IS NULL`` on the catalog row) are visible to
    every tenant, so their audit trail is too. Tenant / user items are scoped to
    the caller's tenant.
    """
    stmt = (
        select(CatalogAuditLog)
        .where(
            CatalogAuditLog.catalog_type == catalog_type,
            CatalogAuditLog.item_id == item_id,
        )
        .order_by(desc(CatalogAuditLog.created_at))
        .limit(limit)
    )
    if tenant_id is not None:
        # Include rows whose actor was in this tenant OR system-level rows
        # (tenant_id NULL — system-scope operations).
        stmt = stmt.where(
            (CatalogAuditLog.tenant_id == tenant_id)
            | (CatalogAuditLog.tenant_id.is_(None))
        )
    res = await db.execute(stmt)
    return list(res.scalars().all())
