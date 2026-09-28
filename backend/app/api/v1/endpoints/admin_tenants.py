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
  PATCH  /admin/tenants/{tenant_id}/users/{user_id}/mfa  Force/release MFA
  POST   /admin/tenants/{tenant_id}/invite    Mint tenant-scoped invite token
  GET    /admin/tenants/{tenant_id}/audit     Audit-log viewer (tenant stream,
                                              filterable by action/outcome)

The cross-tenant stream (SYSTEM_ADMIN sees every tenant + system-level
rows) lives at ``GET /admin/audit`` in ``admin.py``.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cookies import set_session_cookies
from app.core.database import get_db
from app.core.security import RoleChecker, TokenData
from app.models.enums import Role
from app.models.user_model import UserModel
from app.schemas.tenant import (
    AuditEntryResponse,
    AuditListResponse,
    CreateInvitePayload,
    HardDeleteConfirm,
    InviteResponse,
    SetTenantUserMFA,
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
from app.services.audit_service import (
    OUTCOME_DENIED,
    OUTCOME_OK,
    log_audit_action,
)
from app.services.tenant_admin_service import TenantAdminService

router = APIRouter(prefix="/admin/tenants", tags=["admin-tenants"])

_admin_only = RoleChecker([Role.SYSTEM_ADMIN])
# MFA policy (plan 16 H5) is institute-facing: tenant ADMINs may enforce it
# inside their own tenant; SYSTEM_ADMIN passes every RoleChecker gate.
_tenant_user_admin_only = RoleChecker([Role.ADMIN])


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
    response: Response,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> SwitchTenantResponse:
    """Mint a scoped session JWT for operating inside another tenant.

    The new token keeps ``role = SYSTEM_ADMIN`` but ``tenant_id`` is the
    target; the admin's real tenant is preserved in ``original_tenant_id``.

    §10 (plan 16 H3): the browser's active credential is the cookie
    triple, so switching re-stamps it with the scoped tokens (the JSON
    body still carries them for §9 clients this pass).
    """
    if getattr(current_user, "switched", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot switch tenants while already in a switched session. Exit first.",
        )
    tid = _coerce_uuid(tenant_id)
    result = await _svc(db).switch_into_tenant(tid, actor=current_user)
    set_session_cookies(
        response,
        access_token=result.access_token,
        refresh_token=result.refresh_token,
    )
    return result


@router.post("/exit-switch", response_model=SwitchTenantResponse)
async def exit_tenant_switch(
    response: Response,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> SwitchTenantResponse:
    """Restore the original SYSTEM_ADMIN session after a switch.

    The restored session is minted from the switched token's
    ``original_user_id`` / ``original_tenant_id`` claims — the frontend no
    longer needs to keep the pre-switch tokens around (they were in
    localStorage pre-H3; cookies make that impossible and unnecessary).
    The cookie triple is re-stamped with the restored tokens.
    """
    result = await _svc(db).switch_back(actor=current_user)
    set_session_cookies(
        response,
        access_token=result.access_token,
        refresh_token=result.refresh_token,
    )
    return result


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


@router.patch(
    "/{tenant_id}/users/{user_id}/mfa",
    response_model=TenantUserResponse,
)
async def set_tenant_user_mfa(
    tenant_id: str,
    user_id: str,
    payload: SetTenantUserMFA,
    current_user: TokenData = Depends(_tenant_user_admin_only),
    db: AsyncSession = Depends(get_db),
) -> TenantUserResponse:
    """Admin-force (or release) TOTP MFA for one member (plan 16 H5).

    ``enforced=true`` — "promoted for institute use": the member's next
    password login returns the ``mfa_required`` challenge with
    ``enrollment_needed: true`` and only completes once they enroll an
    authenticator (the login-time provisioning + confirm flow). Clearing
    the flag never removes an already-active secret; it only stops
    *requiring* one. Existing sessions stay valid — MFA gates login,
    not live sessions (§9 Bearer clients unaffected).

    ADMIN may act inside their own tenant; SYSTEM_ADMIN anywhere (the
    same scoping rule as the role/is_active PATCH next door). Audited
    via ``log_audit_action`` with outcome (H2 chokepoint).
    """
    tid = _coerce_uuid(tenant_id)
    uid = _coerce_uuid(user_id, field="user_id")
    if current_user.role != Role.SYSTEM_ADMIN.value and str(
        current_user.tenant_id
    ) != str(tid):
        await log_audit_action(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="user.mfa_enforce",
            resource_type="user",
            resource_id=uid,
            outcome=OUTCOME_DENIED,
            new_value={"reason": "cross_tenant", "enforced": payload.enforced},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot manage MFA policy for a different tenant.",
        )

    result = await db.execute(
        select(UserModel).where(UserModel.id == uid, UserModel.tenant_id == tid)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in this tenant.",
        )
    old_enforced = bool(user.mfa_enforced)
    if old_enforced != payload.enforced:
        from app.services import mfa_service

        user = await mfa_service.set_enforced(uid, payload.enforced)

    await log_audit_action(
        tenant_id=tid,
        user_id=current_user.user_id,
        action="user.mfa_enforce",
        resource_type="user",
        resource_id=uid,
        outcome=OUTCOME_OK,
        old_value={"mfa_enforced": old_enforced},
        new_value={
            "mfa_enforced": payload.enforced,
            "mfa_enabled": bool(getattr(user, "mfa_secret_enc", None)),
        },
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
        actor_id=current_user.user_id,
    )
    return InviteResponse(**result)


# ----------------------------------------------------------------------
# Audit viewer
# ----------------------------------------------------------------------


@router.get("/{tenant_id}/audit", response_model=AuditListResponse)
async def list_tenant_audit(
    tenant_id: str,
    action: Optional[str] = Query(default=None),
    outcome: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> AuditListResponse:
    """Tenant-scoped audit stream (``audit_events``, §17).

    SYSTEM_ADMIN-only; the cross-tenant viewer is ``GET /admin/audit``.
    """
    tid = _coerce_uuid(tenant_id)
    items, total = await _svc(db).list_audit_entries(
        tid, action=action, outcome=outcome, limit=limit, offset=offset
    )
    return AuditListResponse(
        items=[AuditEntryResponse.model_validate(a) for a in items],
        total=total,
    )
