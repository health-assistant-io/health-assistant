from typing import Optional, List, Dict, Any
from uuid import UUID
import logging
from sqlalchemy import select, update, delete
from app.models.alert_model import AlertModel
from app.core.database import AsyncSessionLocal, DATABASE_AVAILABLE

logger = logging.getLogger(__name__)


async def create_alert(alert_type: str, patient_id: str | UUID, threshold: float = None, enabled: bool = True, tenant_id: str | UUID = None) -> Optional[AlertModel]:
    """Create a new alert"""
    if not DATABASE_AVAILABLE:
        logger.warning("Database not available for create_alert")
        return None

    try:
        if isinstance(patient_id, str):
            patient_id = UUID(patient_id)
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
    except ValueError:
        logger.error("Invalid UUID format in create_alert")
        return None

    new_alert = AlertModel(
        type=alert_type,
        patient_id=patient_id,
        threshold=threshold,
        enabled=enabled,
        tenant_id=tenant_id
    )
    
    async with AsyncSessionLocal() as session:
        session.add(new_alert)
        await session.commit()
        await session.refresh(new_alert)
        
    return new_alert


async def get_alert(alert_id: str | UUID) -> Optional[AlertModel]:
    """Get alert by ID"""
    if not DATABASE_AVAILABLE:
        return None
        
    if isinstance(alert_id, str):
        try:
            alert_id = UUID(alert_id)
        except ValueError:
            return None

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AlertModel).where(AlertModel.id == alert_id)
        )
        return result.scalar_one_or_none()


async def list_alerts(tenant_id: str | UUID, patient_id: str | UUID = None, alert_type: str = None, enabled: bool = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """List alerts (with filtering and pagination)"""
    if not DATABASE_AVAILABLE:
        return {"items": [], "total": 0}

    try:
        if isinstance(tenant_id, str):
            tenant_id = UUID(tenant_id)
        if patient_id and isinstance(patient_id, str):
            patient_id = UUID(patient_id)
    except ValueError:
        return {"items": [], "total": 0}

    async with AsyncSessionLocal() as session:
        query = select(AlertModel).where(AlertModel.tenant_id == tenant_id)
        
        if patient_id:
            query = query.where(AlertModel.patient_id == patient_id)
        if alert_type:
            query = query.where(AlertModel.type == alert_type)
        if enabled is not None:
            query = query.where(AlertModel.enabled == enabled)
            
        # Get total count
        from sqlalchemy import func
        count_query = select(func.count()).select_from(query.subquery())
        total = await session.execute(count_query)
        total_count = total.scalar() or 0
        
        # Apply pagination
        query = query.limit(limit).offset(offset)
        result = await session.execute(query)
        items = result.scalars().all()
        
        return {
            "items": [item.to_dict() for item in items],
            "total": total_count
        }


async def update_alert(alert_id: str | UUID, threshold: float = None, enabled: bool = None) -> Optional[AlertModel]:
    """Update alert configuration"""
    if not DATABASE_AVAILABLE:
        return None

    if isinstance(alert_id, str):
        try:
            alert_id = UUID(alert_id)
        except ValueError:
            return None

    update_data = {}
    if threshold is not None:
        update_data["threshold"] = threshold
    if enabled is not None:
        update_data["enabled"] = enabled

    if not update_data:
        return await get_alert(alert_id)

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(AlertModel).where(AlertModel.id == alert_id).values(**update_data)
        )
        await session.commit()
        
    return await get_alert(alert_id)


async def delete_alert(alert_id: str | UUID) -> bool:
    """Delete an alert"""
    if not DATABASE_AVAILABLE:
        return False

    if isinstance(alert_id, str):
        try:
            alert_id = UUID(alert_id)
        except ValueError:
            return False

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(AlertModel).where(AlertModel.id == alert_id)
        )
        await session.commit()
        return result.rowcount > 0


async def trigger_alert(alert_id: str | UUID) -> Optional[AlertModel]:
    """Manually trigger an alert and update last_triggered"""
    if not DATABASE_AVAILABLE:
        return None
        
    from datetime import datetime, timezone
    
    if isinstance(alert_id, str):
        try:
            alert_id = UUID(alert_id)
        except ValueError:
            return None

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(AlertModel)
            .where(AlertModel.id == alert_id)
            .values(last_triggered=datetime.now(timezone.utc))
        )
        await session.commit()
        
    return await get_alert(alert_id)


async def get_alert_history(patient_id: str | UUID = None, start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
    """Get alert history (placeholder for actual history log table)"""
    # This would typically query an alert_log table
    # For now, we return empty list or maybe filter triggered alerts
    return []
