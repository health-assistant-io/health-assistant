"""Alert service — patient threshold notifications.

Audit item B4: previously no service-layer method filtered by tenant,
so any endpoint that forgot to add the predicate (i.e. all of them)
leaked every tenant's alerts.

Every method now accepts ``tenant_id`` and constrains its SELECT / UPDATE
/ DELETE accordingly. ``get_alert`` / ``update_alert`` / ``delete_alert``
/ ``trigger_alert`` return ``None`` / ``False`` on a cross-tenant call
so the endpoint can surface 404 (no information leak).
"""
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE
from app.models.alert_model import AlertModel

logger = logging.getLogger(__name__)


def _to_uuid(value, field: str) -> UUID:
    """Parse a UUID; raises ValueError on malformed input."""
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


async def create_alert(
    alert_type: str,
    patient_id: str | UUID,
    threshold: float = None,
    enabled: bool = True,
    tenant_id: str | UUID = None,
) -> Optional[AlertModel]:
    """Create a new alert (tenant-scoped via ``tenant_id`` column)."""
    if not DATABASE_AVAILABLE:
        logger.warning("Database not available for create_alert")
        return None

    try:
        patient_id = _to_uuid(patient_id, "patient_id")
        if tenant_id is not None:
            tenant_id = _to_uuid(tenant_id, "tenant_id")
    except ValueError:
        logger.error("Invalid UUID format in create_alert")
        return None

    new_alert = AlertModel(
        type=alert_type,
        patient_id=patient_id,
        threshold=threshold,
        enabled=enabled,
        tenant_id=tenant_id,
    )

    async with AsyncSessionLocal() as session:
        session.add(new_alert)
        await session.commit()
        await session.refresh(new_alert)

    return new_alert


async def get_alert(
    alert_id: str | UUID,
    tenant_id: Optional[str | UUID] = None,
) -> Optional[AlertModel]:
    """Get alert by ID, optionally constrained to ``tenant_id``.

    Returns ``None`` for a cross-tenant call.
    """
    if not DATABASE_AVAILABLE:
        return None

    try:
        alert_id = _to_uuid(alert_id, "alert_id")
    except ValueError:
        return None

    async with AsyncSessionLocal() as session:
        stmt = select(AlertModel).where(AlertModel.id == alert_id)
        if tenant_id is not None:
            stmt = stmt.where(AlertModel.tenant_id == _to_uuid(tenant_id, "tenant_id"))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def list_alerts(
    tenant_id: str | UUID,
    patient_id: str | UUID = None,
    alert_type: str = None,
    enabled: bool = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """List alerts (with filtering and pagination) for a tenant."""
    if not DATABASE_AVAILABLE:
        return {"items": [], "total": 0}

    try:
        tenant_id = _to_uuid(tenant_id, "tenant_id")
        if patient_id is not None:
            patient_id = _to_uuid(patient_id, "patient_id")
    except ValueError:
        return {"items": [], "total": 0}

    async with AsyncSessionLocal() as session:
        query = select(AlertModel).where(AlertModel.tenant_id == tenant_id)

        if patient_id is not None:
            query = query.where(AlertModel.patient_id == patient_id)
        if alert_type:
            query = query.where(AlertModel.type == alert_type)
        if enabled is not None:
            query = query.where(AlertModel.enabled == enabled)

        count_query = select(func.count()).select_from(query.subquery())
        total = await session.execute(count_query)
        total_count = total.scalar() or 0

        query = query.limit(limit).offset(offset)
        result = await session.execute(query)
        items = result.scalars().all()

        return {"items": [item.to_dict() for item in items], "total": total_count}


async def update_alert(
    alert_id: str | UUID,
    threshold: float = None,
    enabled: bool = None,
    tenant_id: Optional[str | UUID] = None,
) -> Optional[AlertModel]:
    """Update alert configuration.

    Returns ``None`` for a cross-tenant call (or when nothing to update
    and the row doesn't exist in the tenant).
    """
    if not DATABASE_AVAILABLE:
        return None

    try:
        alert_id = _to_uuid(alert_id, "alert_id")
    except ValueError:
        return None

    update_data = {}
    if threshold is not None:
        update_data["threshold"] = threshold
    if enabled is not None:
        update_data["enabled"] = enabled

    if not update_data:
        return await get_alert(alert_id, tenant_id=tenant_id)

    async with AsyncSessionLocal() as session:
        stmt = update(AlertModel).where(AlertModel.id == alert_id)
        if tenant_id is not None:
            stmt = stmt.where(AlertModel.tenant_id == _to_uuid(tenant_id, "tenant_id"))
        result = await session.execute(stmt.values(**update_data))
        await session.commit()

        if (result.rowcount or 0) == 0:
            # No row matched — either wrong id or wrong tenant.
            return None

    return await get_alert(alert_id, tenant_id=tenant_id)


async def delete_alert(
    alert_id: str | UUID,
    tenant_id: Optional[str | UUID] = None,
) -> bool:
    """Delete an alert. Returns True iff a row was actually deleted.

    A cross-tenant delete returns False (no row matched).
    """
    if not DATABASE_AVAILABLE:
        return False

    try:
        alert_id = _to_uuid(alert_id, "alert_id")
    except ValueError:
        return False

    async with AsyncSessionLocal() as session:
        stmt = delete(AlertModel).where(AlertModel.id == alert_id)
        if tenant_id is not None:
            stmt = stmt.where(AlertModel.tenant_id == _to_uuid(tenant_id, "tenant_id"))
        result = await session.execute(stmt)
        await session.commit()
        return (result.rowcount or 0) > 0


async def trigger_alert(
    alert_id: str | UUID,
    tenant_id: Optional[str | UUID] = None,
) -> Optional[AlertModel]:
    """Manually trigger an alert and update last_triggered.

    Returns ``None`` for a cross-tenant call.
    """
    if not DATABASE_AVAILABLE:
        return None

    from datetime import datetime, timezone

    try:
        alert_id = _to_uuid(alert_id, "alert_id")
    except ValueError:
        return None

    async with AsyncSessionLocal() as session:
        stmt = update(AlertModel).where(AlertModel.id == alert_id)
        if tenant_id is not None:
            stmt = stmt.where(AlertModel.tenant_id == _to_uuid(tenant_id, "tenant_id"))
        result = await session.execute(
            stmt.values(last_triggered=datetime.now(timezone.utc))
        )
        await session.commit()

        if (result.rowcount or 0) == 0:
            return None

    return await get_alert(alert_id, tenant_id=tenant_id)


async def get_alert_history(
    patient_id: str | UUID = None,
    start_date: str = None,
    end_date: str = None,
    tenant_id: Optional[str | UUID] = None,
) -> List[Dict[str, Any]]:
    """Get alert history (placeholder for actual history log table).

    This would typically query an alert_log table. For now, return an
    empty list. Tenant-scoped signature kept for forward compatibility.
    """
    return []
