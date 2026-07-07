"""System-admin tenant management endpoints.

All routes are gated by ``RoleChecker([Role.SYSTEM_ADMIN])`` — only system
admins can list, create, mutate, delete, switch into, or otherwise manage
tenants and their users. Every mutation is audit-logged by the service
layer (``TenantAdminService``).

Tenant-scoped note: ``SYSTEM_ADMIN`` tokens carry their *real* tenant in
``tenant_id``. The ``switch`` / ``exit-switch`` endpoints mint a new
scoped token whose ``tenant_id`` is the target tenant — once switched,
the admin operates inside that tenant until they exit.

Endpoint surface:
  GET    /admin/tenants                       List + search + pagination
  POST   /admin/tenants                       Create
  GET    /admin/tenants/{tenant_id}           Detail + usage stats
  PATCH  /admin/tenants/{tenant_id}           Partial update
  POST   /admin/tenants/{tenant_id}/deactivate  Soft-delete
  POST   /admin/tenants/{tenant_id}/reactivate  Restore
  DELETE /admin/tenants/{tenant_id}           Hard delete (typed-name confirm)
  POST   /admin/tenants/{tenant_id}/switch    Mint scoped session token
  POST   /admin/tenants/exit-switch           Restore original session
  GET    /admin/tenants/{tenant_id}/users     List tenant users
  PATCH  /admin/tenants/{tenant_id}/users/{user_id}  Role + active toggle
  POST   /admin/tenants/{tenant_id}/invite    Mint tenant-scoped invite token
  GET    /admin/tenants/{tenant_id}/audit     Audit-log viewer
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import RoleChecker, TokenData
from app.models.enums import Role
from app.schemas.tenant import (
    AuditEntryResponse,
    AuditListResponse,
    CreateInvitePayload,
    HardDeleteConfirm,
    InviteResponse,
    SwitchTenantResponse,
    TenantCreate,
    TenantDetailResponse,
    TenantListResponse,
    TenantResponse,
    TenantUpdate,
    TenantUserListResponse,
    TenantUserResponse,
    UpdateTenantUser,
)
from app.services.tenant_admin_service import TenantAdminService

router = APIRouter(prefix="/admin/tenants", tags=["admin-tenants"])

_admin_only = RoleChecker([Role.SYSTEM_ADMIN])


def _svc(db: AsyncSession) -> TenantAdminService:
    return TenantAdminService(db)


# ----------------------------------------------------------------------
# List + create
# ----------------------------------------------------------------------


@router.get("", response_model=TenantListResponse)
async def list_tenants(
    search: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> TenantListResponse:
    items, total = await _svc(db).list_tenants(
        search=search, is_active=is_active, limit=limit, offset=offset
    )
    return TenantListResponse(
        items=[TenantResponse.model_validate(t) for t in items],
        total=total,
    )


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreate,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> TenantResponse:
    tenant = await _svc(db).create_tenant(payload, actor_id=current_user.user_id)
    return TenantResponse.model_validate(tenant)


# ----------------------------------------------------------------------
# Single-tenant CRUD
# ----------------------------------------------------------------------


def _coerce_uuid(value: str, field: str = "tenant_id") -> UUID:
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field}: must be a valid UUID.",
        )


@router.get("/{tenant_id}", response_model=TenantDetailResponse)
async def get_tenant_detail(
    tenant_id: str,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> TenantDetailResponse:
    tid = _coerce_uuid(tenant_id)
    return await _svc(db).get_tenant_detail(tid)


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: str,
    payload: TenantUpdate,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> TenantResponse:
    tid = _coerce_uuid(tenant_id)
    tenant = await _svc(db).update_tenant(tid, payload, actor_id=current_user.user_id)
    return TenantResponse.model_validate(tenant)


@router.post("/{tenant_id}/deactivate", response_model=TenantResponse)
async def deactivate_tenant(
    tenant_id: str,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> TenantResponse:
    tid = _coerce_uuid(tenant_id)
    tenant = await _svc(db).set_active(tid, active=False, actor_id=current_user.user_id)
    return TenantResponse.model_validate(tenant)


@router.post("/{tenant_id}/reactivate", response_model=TenantResponse)
async def reactivate_tenant(
    tenant_id: str,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> TenantResponse:
    tid = _coerce_uuid(tenant_id)
    tenant = await _svc(db).set_active(tid, active=True, actor_id=current_user.user_id)
    return TenantResponse.model_validate(tenant)


@router.delete("/{tenant_id}", status_code=status.HTTP_200_OK)
async def hard_delete_tenant(
    tenant_id: str,
    body: HardDeleteConfirm,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> dict:
    tid = _coerce_uuid(tenant_id)
    await _svc(db).hard_delete_tenant(
        tid, confirm_name=body.confirm_name, actor_id=current_user.user_id
    )
    return {"message": "Tenant permanently deleted."}


# ----------------------------------------------------------------------
# Tenant switching
# ----------------------------------------------------------------------


@router.post("/{tenant_id}/switch", response_model=SwitchTenantResponse)
async def switch_into_tenant(
    tenant_id: str,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> SwitchTenantResponse:
    """Mint a scoped session JWT for operating inside another tenant.

    The new token keeps ``role = SYSTEM_ADMIN`` but ``tenant_id`` is the
    target; the admin's real tenant is preserved in ``original_tenant_id``.
    """
    if getattr(current_user, "switched", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot switch tenants while already in a switched session. Exit first.",
        )
    tid = _coerce_uuid(tenant_id)
    return await _svc(db).switch_into_tenant(tid, actor=current_user)


@router.post("/exit-switch", response_model=SwitchTenantResponse)
async def exit_tenant_switch(
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> SwitchTenantResponse:
    """Restore the original SYSTEM_ADMIN session after a switch."""
    return await _svc(db).switch_back(actor=current_user)


# ----------------------------------------------------------------------
# Per-tenant user management
# ----------------------------------------------------------------------


@router.get("/{tenant_id}/users", response_model=TenantUserListResponse)
async def list_tenant_users(
    tenant_id: str,
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> TenantUserListResponse:
    tid = _coerce_uuid(tenant_id)
    items, total = await _svc(db).list_tenant_users(
        tid, search=search, limit=limit, offset=offset
    )
    return TenantUserListResponse(
        items=[TenantUserResponse.model_validate(u) for u in items],
        total=total,
    )


@router.patch(
    "/{tenant_id}/users/{user_id}",
    response_model=TenantUserResponse,
)
async def update_tenant_user(
    tenant_id: str,
    user_id: str,
    payload: UpdateTenantUser,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> TenantUserResponse:
    tid = _coerce_uuid(tenant_id)
    uid = _coerce_uuid(user_id, field="user_id")
    user = await _svc(db).update_tenant_user(
        tid, uid, payload, actor_id=current_user.user_id
    )
    return TenantUserResponse.model_validate(user)


@router.post("/{tenant_id}/invite", response_model=InviteResponse)
async def create_tenant_invite(
    tenant_id: str,
    payload: CreateInvitePayload,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> InviteResponse:
    tid = _coerce_uuid(tenant_id)
    result = await _svc(db).mint_invite(
        tid,
        email=payload.email,
        role=payload.role,
        expires_days=payload.expires_days,
    )
    return InviteResponse(**result)


# ----------------------------------------------------------------------
# Audit viewer
# ----------------------------------------------------------------------


@router.get("/{tenant_id}/audit", response_model=AuditListResponse)
async def list_tenant_audit(
    tenant_id: str,
    action: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> AuditListResponse:
    tid = _coerce_uuid(tenant_id)
    items, total = await _svc(db).list_audit_entries(
        tid, action=action, limit=limit, offset=offset
    )
    return AuditListResponse(
        items=[AuditEntryResponse.model_validate(a) for a in items],
        total=total,
    )
