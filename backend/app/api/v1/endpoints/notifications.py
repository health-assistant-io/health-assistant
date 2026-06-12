from fastapi import APIRouter, Depends, HTTPException, Query, Body
from typing import List, Optional
from uuid import UUID
from app.core.security import get_current_user
from app.services.notification_manager import NotificationManager
from app.models.notification import NotificationType, TriggerType
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Get the VAPID public key for Web Push registration."""
    return {"public_key": getattr(settings, "VAPID_PUBLIC_KEY", None)}


@router.post("/subscribe")
async def subscribe_user(
    subscription: dict = Body(...),
    device_id: Optional[str] = Body(None),
    user_agent: Optional[str] = Body(None),
    current_user=Depends(get_current_user),
):
    """Register a Web Push subscription for the current user."""
    sub = await NotificationManager.subscribe_user(
        user_id=current_user.user_id,
        subscription_data=subscription,
        tenant_id=current_user.tenant_id,
        device_id=device_id,
        user_agent=user_agent,
    )
    return {"status": "success", "id": str(sub.id)}


@router.get("")
async def list_notifications(
    patient_id: Optional[str] = Query(None),
    unread_only: bool = Query(False),
    limit: int = Query(20, le=100),
    current_user=Depends(get_current_user),
):
    """Fetch notifications for a patient or the general context."""
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")

    notifications = await NotificationManager.get_active_notifications(
        patient_id=patient_id, limit=limit, unread_only=unread_only
    )
    return [n.to_dict() for n in notifications]


@router.patch("/{notification_id}/read")
async def mark_as_read(notification_id: str, current_user=Depends(get_current_user)):
    """Mark a specific notification as read."""
    success = await NotificationManager.mark_as_read(notification_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "success"}


@router.patch("/{notification_id}/delivered")
async def mark_as_delivered(notification_id: str):
    """
    Mark a specific notification as delivered.
    """
    success = await NotificationManager.mark_as_delivered(notification_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "success"}


@router.post("/triggers")
async def create_trigger(
    patient_id: str,
    title: str,
    body: Optional[str] = None,
    notification_type: str = "medication_reminder",
    trigger_type: str = "time",
    config: dict = Body(...),
    reference_id: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    """Create a new manual or scheduled trigger."""
    trigger = await NotificationManager.create_trigger(
        patient_id=patient_id,
        notification_type=NotificationType(notification_type),
        trigger_type=TriggerType(trigger_type),
        config=config,
        title=title,
        body=body,
        tenant_id=current_user.tenant_id,
        reference_id=reference_id,
    )
    return trigger.to_dict() if trigger else {"status": "error"}


@router.get("/triggers")
async def list_triggers(patient_id: str, current_user=Depends(get_current_user)):
    """List all scheduled triggers for a patient."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.notification import NotificationTrigger

    async with AsyncSessionLocal() as session:
        query = select(NotificationTrigger).where(
            NotificationTrigger.patient_id == UUID(patient_id)
        )
        result = await session.execute(query)
        triggers = result.scalars().all()
        logger.info(f"DEBUG: Found {len(triggers)} triggers for patient {patient_id}")
        return [t.to_dict() for t in triggers]


@router.delete("/triggers/{trigger_id}")
async def delete_trigger(trigger_id: str, current_user=Depends(get_current_user)):
    """Manually delete a scheduled trigger."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import delete
    from app.models.notification import NotificationTrigger

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(NotificationTrigger).where(
                NotificationTrigger.id == UUID(trigger_id)
            )
        )
        await session.commit()
        return {"status": "success"}


@router.post("/triggers/{trigger_id}/test")
async def test_trigger(trigger_id: str, current_user=Depends(get_current_user)):
    """Immediately fire a trigger for testing."""
    from app.core.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.notification import NotificationTrigger
    from app.services.notification_manager import NotificationManager

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(NotificationTrigger).where(
                NotificationTrigger.id == UUID(trigger_id)
            )
        )
        trigger = res.scalar_one_or_none()
        if not trigger:
            raise HTTPException(status_code=404, detail="Trigger not found")

        await NotificationManager.fire_notification(trigger)
        return {"status": "success", "message": "Notification queued for delivery"}
