"""Unified settings endpoints.

Three tiers: system (SYSTEM_ADMIN), tenant (ADMIN+), user (self).
Resolution (USER > TENANT > SYSTEM > default) is exposed via /settings/effective.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import RoleChecker, TokenData, get_current_user
from app.models.enums import Role
from app.schemas.settings import SettingLevel
from app.services.settings_service import SettingsService, can_manage_level

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/definitions")
async def get_definitions(
    current_user: TokenData = Depends(get_current_user),
):
    defs = [d.model_dump(mode="json") for d in SettingsService.get_definitions()]
    cats = [c.model_dump(mode="json") for c in SettingsService.get_categories()]
    return {"definitions": defs, "categories": cats}


@router.get("/effective")
async def get_effective_settings(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = SettingsService(db)
    values, sources = await service.resolve_effective(
        current_user.user_id, current_user.tenant_id
    )
    return {"settings": values, "sources": sources}


@router.get("/system")
async def get_system_overrides(
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    service = SettingsService(db)
    overrides = await service.get_level_overrides(
        SettingLevel.SYSTEM, current_user.user_id, current_user.tenant_id
    )
    return {"level": SettingLevel.SYSTEM.value, "settings": overrides}


@router.put("/system")
async def update_system_override(
    payload: Dict[str, Any],
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    key = payload.get("key")
    value = payload.get("value")
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    service = SettingsService(db)
    try:
        await service.update_override(
            SettingLevel.SYSTEM,
            key,
            value,
            current_user.user_id,
            current_user.tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": "System setting updated"}


@router.get("/tenant")
async def get_tenant_overrides(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not can_manage_level(
        current_user.role,
        SettingLevel.TENANT,
        current_user.tenant_id,
        current_user.tenant_id,
    ):
        raise HTTPException(status_code=403, detail="Not authorized")
    service = SettingsService(db)
    overrides = await service.get_level_overrides(
        SettingLevel.TENANT, current_user.user_id, current_user.tenant_id
    )
    return {"level": SettingLevel.TENANT.value, "settings": overrides}


@router.put("/tenant")
async def update_tenant_override(
    payload: Dict[str, Any],
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not can_manage_level(
        current_user.role,
        SettingLevel.TENANT,
        current_user.tenant_id,
        current_user.tenant_id,
    ):
        raise HTTPException(status_code=403, detail="Not authorized")
    key = payload.get("key")
    value = payload.get("value")
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    service = SettingsService(db)
    try:
        await service.update_override(
            SettingLevel.TENANT,
            key,
            value,
            current_user.user_id,
            current_user.tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": "Tenant setting updated"}


@router.get("/user")
async def get_user_overrides(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = SettingsService(db)
    overrides = await service.get_level_overrides(
        SettingLevel.USER, current_user.user_id, current_user.tenant_id
    )
    return {"level": SettingLevel.USER.value, "settings": overrides}


@router.put("/user")
async def update_user_override(
    payload: Dict[str, Any],
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    key = payload.get("key")
    value = payload.get("value")
    if not key:
        raise HTTPException(status_code=400, detail="key is required")
    service = SettingsService(db)
    try:
        await service.update_override(
            SettingLevel.USER, key, value, current_user.user_id, current_user.tenant_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": "User setting updated"}
