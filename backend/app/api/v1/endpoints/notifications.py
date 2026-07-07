"""Notification inbox + admin endpoints.

Role-aware scoping (the established pattern: ``get_current_user`` +
``check_patient_access``):
* Every authenticated user gets a personal **inbox** (``GET /notifications/inbox``)
  keyed off their ``user_id`` — no patient context required.
* ``ADMIN``/``MANAGER`` see a tenant-wide feed via ``GET /notifications/admin``.
* ``SYSTEM_ADMIN`` sees a cross-tenant feed (and may pass ``tenant_id``).

Tenant isolation: every read/mutation helper in
:mod:`app.services.notification_service` is constrained by ``user_id`` +
``tenant_id``; cross-tenant calls are no-ops surfaced as 404.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.utils import check_patient_access
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enums import (
    NotificationCategory,
    NotificationSource,
    NotificationType,
    RecipientStatus,
    Role,
)
from app.schemas.notification import (
    InboxResponse,
    AdminFeedResponse,
    SubscribeRequest,
    TriggerCreate,
    UnreadCountResponse,
)
from app.schemas.user import TokenData
from app.services import notification_service
from app.services.notification_manager import NotificationManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _coerce_uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {field} format")


# ---------------------------------------------------------------------------
# Web Push subscription (retained from the legacy API)
# ---------------------------------------------------------------------------


@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Get the VAPID public key for Web Push registration."""
    return {"public_key": getattr(settings, "VAPID_PUBLIC_KEY", None)}


@router.post("/subscribe")
async def subscribe_user(
    payload: SubscribeRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """Register a Web Push subscription for the current user."""
    sub = await NotificationManager.subscribe_user(
        user_id=current_user.user_id,
        subscription_data=payload.subscription,
        tenant_id=current_user.tenant_id,
        device_id=payload.device_id,
        user_agent=payload.user_agent,
    )
    return {"status": "success", "id": str(sub.id)}


# ---------------------------------------------------------------------------
# Inbox (personal, user-scoped — works without a patient context)
# ---------------------------------------------------------------------------


@router.get("/inbox", response_model=InboxResponse)
async def get_inbox(
    status: Optional[str] = Query(None, description="unread|read|dismissed"),
    category: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    patient_id: Optional[str] = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch the current user's notification inbox."""
    if patient_id:
        await check_patient_access(patient_id, current_user, db)

    items, total = await notification_service.get_inbox(
        user_id=current_user.user_id,
        tenant_id=current_user.tenant_id,
        status=_parse_status(status),
        category=_parse_category(category),
        source=_parse_source(source),
        patient_id=patient_id,
        limit=limit,
        offset=offset,
    )
    return InboxResponse(items=items, total=total)


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(current_user: TokenData = Depends(get_current_user)):
    """Badge count for the notification bell."""
    count = await notification_service.get_unread_count(
        user_id=current_user.user_id, tenant_id=current_user.tenant_id
    )
    return UnreadCountResponse(count=count)


@router.patch("/{recipient_id}/read")
async def mark_read(
    recipient_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Mark a recipient inbox row as read."""
    _coerce_uuid(recipient_id, "recipient_id")
    ok = await notification_service.mark_read(
        recipient_id, current_user.user_id, current_user.tenant_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "success"}


@router.patch("/{recipient_id}/dismiss")
async def mark_dismissed(
    recipient_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Dismiss a recipient inbox row."""
    _coerce_uuid(recipient_id, "recipient_id")
    ok = await notification_service.mark_dismissed(
        recipient_id, current_user.user_id, current_user.tenant_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "success"}


@router.post("/read-all")
async def mark_all_read(current_user: TokenData = Depends(get_current_user)):
    """Mark every unread inbox row for the current user as read."""
    count = await notification_service.mark_all_read(
        user_id=current_user.user_id, tenant_id=current_user.tenant_id
    )
    return {"status": "success", "marked_read": count}


# ---------------------------------------------------------------------------
# Admin / tenant-wide feed
# ---------------------------------------------------------------------------


@router.get("/admin", response_model=AdminFeedResponse)
async def get_admin_feed(
    tenant_id: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    current_user: TokenData = Depends(get_current_user),
):
    """Tenant-wide (ADMIN/MANAGER) or cross-tenant (SYSTEM_ADMIN) feed."""
    is_system_admin = current_user.role == Role.SYSTEM_ADMIN.value
    is_admin = current_user.role in (
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    )
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    # SYSTEM_ADMIN may target any tenant; others are pinned to their own.
    target_tenant = (
        tenant_id if is_system_admin and tenant_id else current_user.tenant_id
    )

    items, total = await notification_service.get_admin_feed(
        tenant_id=target_tenant,
        is_system_admin=is_system_admin,
        type=_parse_type(type),
        source=_parse_source(source),
        category=_parse_category(category),
        limit=limit,
        offset=offset,
    )
    return AdminFeedResponse(items=items, total=total)


@router.get("/admin/stats")
async def get_admin_stats(
    tenant_id: Optional[str] = Query(None),
    current_user: TokenData = Depends(get_current_user),
):
    """Aggregated notification delivery stats for the admin dashboard."""
    is_system_admin = current_user.role == Role.SYSTEM_ADMIN.value
    is_admin = current_user.role in (
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    )
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    target_tenant = (
        tenant_id if is_system_admin and tenant_id else current_user.tenant_id
    )
    return await notification_service.get_admin_stats(
        tenant_id=target_tenant,
        is_system_admin=is_system_admin,
    )


@router.get("/admin/{notification_id}/delivery")
async def get_admin_delivery_detail(
    notification_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Per-recipient delivery breakdown for a single notification.

    Returns the notification, the sender (email resolved), and one entry
    per recipient with their inbox status + per-channel delivery state
    (IN_APP / PUSH, status, error). Used by the Admin Center's "who got
    this and how was it delivered?" detail view.
    """
    is_system_admin = current_user.role == Role.SYSTEM_ADMIN.value
    is_admin = current_user.role in (
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    )
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    detail = await notification_service.get_notification_delivery_detail(
        notification_id=notification_id,
        tenant_id=current_user.tenant_id,
        is_system_admin=is_system_admin,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return detail


# ---------------------------------------------------------------------------
# Triggers (scheduled reminders — retained, simplified)
# ---------------------------------------------------------------------------


@router.post("/triggers")
async def create_trigger(
    payload: TriggerCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a scheduled/recurring reminder trigger."""
    if payload.patient_id:
        await check_patient_access(str(payload.patient_id), current_user, db)

    from app.models.enums import NotificationType as NT, TriggerType as TT

    trigger = await NotificationManager.create_trigger(
        patient_id=payload.patient_id,
        notification_type=NT(payload.notification_type),
        trigger_type=TT(payload.trigger_type),
        config=payload.config,
        title=payload.title,
        body=payload.body,
        tenant_id=current_user.tenant_id,
        reference_id=payload.reference_id,
        enabled=payload.enabled,
    )
    return trigger.to_dict() if trigger else {"status": "error"}


@router.get("/triggers")
async def list_triggers(
    patient_id: Optional[str] = Query(None),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List scheduled triggers.

    With ``patient_id``: scoped to that patient (access-checked). Without:
    tenant-wide (ADMIN/MANAGER/SYSTEM_ADMIN) or just the caller's own
    patient-linked triggers (USER). Used by the Notification Center
    "Reminders" tab, which is global, not patient-scoped.
    """
    if patient_id:
        await check_patient_access(patient_id, current_user, db)
        return await NotificationManager.list_triggers_for_patient(
            patient_id=patient_id, tenant_id=current_user.tenant_id
        )
    return await NotificationManager.list_triggers_for_tenant(
        tenant_id=current_user.tenant_id
    )


@router.delete("/triggers/{trigger_id}")
async def delete_trigger(
    trigger_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Delete a scheduled trigger (tenant-scoped; cross-tenant = no-op)."""
    trigger_uuid = _coerce_uuid(trigger_id, "trigger_id")
    return await NotificationManager.delete_trigger(
        trigger_id=trigger_uuid, tenant_id=current_user.tenant_id
    )


@router.post("/triggers/{trigger_id}/test")
async def test_trigger(
    trigger_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Immediately fire a trigger for testing."""
    trigger_uuid = _coerce_uuid(trigger_id, "trigger_id")
    ok = await NotificationManager.fire_trigger_by_id(
        trigger_id=trigger_uuid, tenant_id=current_user.tenant_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return {"status": "success", "message": "Notification queued for delivery"}


# ---------------------------------------------------------------------------
# Enum parsers (lenient — bad values 400)
# ---------------------------------------------------------------------------


def _parse_status(value: Optional[str]) -> Optional[RecipientStatus]:
    if value is None:
        return None
    try:
        return RecipientStatus(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {value}")


def _parse_category(value: Optional[str]) -> Optional[NotificationCategory]:
    if value is None:
        return None
    try:
        return NotificationCategory(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid category: {value}")


def _parse_source(value: Optional[str]) -> Optional[NotificationSource]:
    if value is None:
        return None
    try:
        return NotificationSource(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid source: {value}")


def _parse_type(value: Optional[str]) -> Optional[NotificationType]:
    if value is None:
        return None
    try:
        return NotificationType(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid type: {value}")
