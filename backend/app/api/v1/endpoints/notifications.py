"""Notification + notification-trigger endpoints.

Audit item B2/B3: previously, ``mark_as_delivered`` had **no auth at all**
(any anonymous client could mark any notification delivered), and the rest
filtered only by ``patient_id`` — any authenticated user could
list / read / deliver / create / delete / fire any other tenant's
notifications and triggers.

Every endpoint now:
1. Requires ``Depends(get_current_user)``.
2. Threads ``current_user.tenant_id`` into ``NotificationManager.*`` so the
   service-layer update/select is constrained to the caller's tenant.
3. Calls ``check_patient_access`` first so a ``USER``-role caller can only
   touch patients assigned to them (``ADMIN``/``MANAGER`` see the whole
   tenant — same pattern as the rest of the API).
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.security import get_current_user
from app.api.v1.endpoints.utils import check_patient_access
from app.models.notification import (
    NotificationTrigger,
    NotificationType,
    TriggerType,
)
from app.services.notification_manager import NotificationManager
from app.schemas.user import TokenData

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _coerce_uuid(value: str, field: str) -> UUID:
    """Parse a path/query UUID and 400 on malformed input."""
    try:
        return UUID(value)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400, detail=f"Invalid {field} format"
        )


@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Get the VAPID public key for Web Push registration."""
    return {"public_key": getattr(settings, "VAPID_PUBLIC_KEY", None)}


@router.post("/subscribe")
async def subscribe_user(
    subscription: dict = Body(...),
    device_id: Optional[str] = Body(None),
    user_agent: Optional[str] = Body(None),
    current_user: TokenData = Depends(get_current_user),
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
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch notifications for a patient (tenant + patient-access scoped)."""
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")

    # Tenant + (USER-role) patient-assignment check — 404 if not in tenant,
    # 403 if the patient is not assigned to this USER.
    await check_patient_access(patient_id, current_user, db)

    notifications = await NotificationManager.get_active_notifications(
        patient_id=patient_id,
        tenant_id=current_user.tenant_id,
        limit=limit,
        unread_only=unread_only,
    )
    return [n.to_dict() for n in notifications]


@router.patch("/{notification_id}/read")
async def mark_as_read(
    notification_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Mark a specific notification as read.

    Tenant-scoped: a cross-tenant call returns 404 (no information leak).
    """
    _coerce_uuid(notification_id, "notification_id")
    success = await NotificationManager.mark_as_read(
        notification_id, tenant_id=current_user.tenant_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "success"}


@router.patch("/{notification_id}/delivered")
async def mark_as_delivered(
    notification_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Mark a specific notification as delivered.

    Tenant-scoped: a cross-tenant call returns 404. Auth was previously
    missing entirely on this endpoint (audit B2).
    """
    _coerce_uuid(notification_id, "notification_id")
    success = await NotificationManager.mark_as_delivered(
        notification_id, tenant_id=current_user.tenant_id
    )
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
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new manual or scheduled trigger.

    Tenant + patient-access scoped.
    """
    await check_patient_access(patient_id, current_user, db)

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
async def list_triggers(
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all scheduled triggers for a patient.

    Tenant + patient-access scoped.
    """
    await check_patient_access(patient_id, current_user, db)

    patient_uuid = _coerce_uuid(patient_id, "patient_id")

    async with AsyncSessionLocal() as session:
        query = select(NotificationTrigger).where(
            NotificationTrigger.patient_id == patient_uuid,
            NotificationTrigger.tenant_id == current_user.tenant_id,
        )
        result = await session.execute(query)
        triggers = result.scalars().all()
        logger.debug(
            "Found %s triggers for patient %s in tenant %s",
            len(triggers),
            patient_id,
            current_user.tenant_id,
        )
        return [t.to_dict() for t in triggers]


@router.delete("/triggers/{trigger_id}")
async def delete_trigger(
    trigger_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Manually delete a scheduled trigger.

    Tenant-scoped: a cross-tenant delete is a no-op (row not found), but
    we still return success to avoid leaking existence.
    """
    trigger_uuid = _coerce_uuid(trigger_id, "trigger_id")

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(NotificationTrigger).where(
                NotificationTrigger.id == trigger_uuid,
                NotificationTrigger.tenant_id == current_user.tenant_id,
            )
        )
        await session.commit()
        return {"status": "success"}


@router.post("/triggers/{trigger_id}/test")
async def test_trigger(
    trigger_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Immediately fire a trigger for testing.

    Tenant-scoped: cross-tenant fire returns 404.
    """
    trigger_uuid = _coerce_uuid(trigger_id, "trigger_id")

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(NotificationTrigger).where(
                NotificationTrigger.id == trigger_uuid,
                NotificationTrigger.tenant_id == current_user.tenant_id,
            )
        )
        trigger = res.scalar_one_or_none()
        if not trigger:
            raise HTTPException(status_code=404, detail="Trigger not found")

        await NotificationManager.fire_notification(trigger)
        return {"status": "success", "message": "Notification queued for delivery"}
