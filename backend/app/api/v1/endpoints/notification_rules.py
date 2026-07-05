"""Notification rule CRUD endpoints (biomarker limit checks, etc.).

Tenant-scoped. Patient-scoped routes additionally call ``check_patient_access``
so a ``USER``-role caller can only create rules for patients assigned to
them; ``ADMIN``/``MANAGER`` see the tenant-wide view.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.utils import check_patient_access
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.notification import (
    NotificationRuleCreate,
    NotificationRuleListResponse,
    NotificationRuleRead,
    NotificationRuleUpdate,
)
from app.schemas.user import TokenData
from app.services import notification_rule_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notification-rules", tags=["notification-rules"])


@router.post("", response_model=NotificationRuleRead)
async def create_rule(
    payload: NotificationRuleCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a notification rule (e.g. a biomarker threshold check)."""
    if payload.patient_id:
        await check_patient_access(str(payload.patient_id), current_user, db)

    rule = await notification_rule_service.create_rule(
        payload.to_dict(), tenant_id=current_user.tenant_id
    )
    if rule is None:
        raise HTTPException(status_code=500, detail="Failed to create rule")
    return NotificationRuleRead.model_validate(rule.to_dict())


@router.get("", response_model=NotificationRuleListResponse)
async def list_rules(
    patient_id: Optional[str] = Query(None),
    biomarker_id: Optional[str] = Query(None),
    enabled: Optional[bool] = Query(None),
    limit: int = Query(100, le=200),
    offset: int = Query(0, ge=0),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List notification rules for the tenant (optionally filtered)."""
    if patient_id:
        await check_patient_access(patient_id, current_user, db)

    rules = await notification_rule_service.list_rules(
        tenant_id=current_user.tenant_id,
        patient_id=patient_id,
        biomarker_id=biomarker_id,
        enabled=enabled,
        limit=limit,
        offset=offset,
    )
    items = [NotificationRuleRead.model_validate(r.to_dict()) for r in rules]
    return NotificationRuleListResponse(items=items, total=len(items))


@router.get("/{rule_id}", response_model=NotificationRuleRead)
async def get_rule(
    rule_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Get a single rule (tenant-scoped; cross-tenant → 404)."""
    rule = await notification_rule_service.get_rule(rule_id, current_user.tenant_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return NotificationRuleRead.model_validate(rule.to_dict())


@router.put("/{rule_id}", response_model=NotificationRuleRead)
async def update_rule(
    rule_id: str,
    payload: NotificationRuleUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a rule (tenant-scoped)."""
    if payload.patient_id is not None:
        await check_patient_access(str(payload.patient_id), current_user, db)

    rule = await notification_rule_service.update_rule(
        rule_id, payload.to_updates(), current_user.tenant_id
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return NotificationRuleRead.model_validate(rule.to_dict())


@router.delete("/{rule_id}")
async def delete_rule(
    rule_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Delete a rule (tenant-scoped; cross-tenant = no-op → success)."""
    await notification_rule_service.delete_rule(rule_id, current_user.tenant_id)
    return {"status": "success"}


@router.post("/{rule_id}/test")
async def test_rule(
    rule_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Fire a rule immediately for testing (respects current config, not data)."""
    fired = await notification_rule_service.test_fire(rule_id, current_user.tenant_id)
    if not fired:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "success", "message": "Test notification emitted"}
