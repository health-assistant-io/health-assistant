"""Tenant self-info endpoints.

The administrative surface (list, create, update, delete, switch, user
management) lives at ``/admin/tenants`` and is SYSTEM_ADMIN-only.

This module exposes the routes a tenant member needs for their own
tenant:
  * ``GET /tenants``                        — the caller's own tenant summary.
  * ``GET /tenants/{id}``                   — read a specific tenant (own
    tenant, or any tenant if the caller is an unscoped SYSTEM_ADMIN; a
    scoped credential's role name never widens it past its own tenant).
  * ``PATCH /tenants/{id}``                 — tenant admin self-service update
    (name / description / settings only).
  * ``GET/PATCH /tenants/{id}/security-policy`` — the tenant security policy
    (ADR 0054 Phase 3): read for staff, write for tenant admins; every
    change audited; cross-tenant access is a hidden 404.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.roles import is_unscoped_system_admin
from app.core.database import get_db
from app.core.security import RoleChecker, TokenData, get_current_user
from app.models.enums import Role
from app.schemas.tenant import (
    TenantResponse,
    TenantSecurityPolicyPatch,
    TenantSecurityPolicyResponse,
    TenantUpdate,
)
from app.services.audit.audit_service import log_audit_action
from app.services.auth import security_policy
from app.services.tenancy.tenant_admin_service import TenantAdminService
from app.services.tenancy.tenant_service import get_tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


def _coerce_uuid(value: str, field: str = "tenant_id") -> UUID:
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(  # noqa: B904 -- bare raise; add explicit chaining when touched
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field}: must be a valid UUID.",
        )


@router.get("", response_model=TenantResponse)
async def get_my_tenant(
    current_user: TokenData = Depends(get_current_user),
) -> TenantResponse:
    """Return the caller's own tenant."""
    tenant = await get_tenant(current_user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return TenantResponse.model_validate(tenant)


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant_endpoint(
    tenant_id: str,
    current_user: TokenData = Depends(get_current_user),
) -> TenantResponse:
    """Read a tenant by ID.

    A non-SYSTEM_ADMIN caller can only read their own tenant. A switched
    SYSTEM_ADMIN (``switched=True``) reads the tenant their scoped token
    points at. A scoped credential (PAT / integration principal) keeps its
    own tenant even though it carries its owner's ``SYSTEM_ADMIN`` role name.
    """
    tid = _coerce_uuid(tenant_id)
    is_admin = is_unscoped_system_admin(current_user)
    if not is_admin and str(current_user.tenant_id) != str(tid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this tenant.",
        )
    tenant = await get_tenant(tid)
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return TenantResponse.model_validate(tenant)


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_my_tenant_endpoint(
    tenant_id: str,
    payload: TenantUpdate,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008 -- framework default idiom (FastAPI/Pydantic)
    db: AsyncSession = Depends(get_db),
) -> TenantResponse:
    """Tenant-admin self-service update (name / description / settings).

    SYSTEM_ADMIN callers managing a tenant they don't own should use the
    richer surface at ``PATCH /admin/tenants/{id}`` instead.
    """
    tid = _coerce_uuid(tenant_id)
    if not is_unscoped_system_admin(current_user) and str(current_user.tenant_id) != str(tid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this tenant.",
        )
    tenant = await TenantAdminService(db).update_tenant(tid, payload, actor_id=current_user.user_id)
    return TenantResponse.model_validate(tenant)


# ----------------------------------------------------------------------
# Tenant security policy (ADR 0054 Phase 3)
# ----------------------------------------------------------------------


def _policy_response(tenant) -> TenantSecurityPolicyResponse:
    """The normalized policy of a loaded tenant row as the API shape."""
    policy = security_policy.normalize_security_policy(tenant.settings, tenant_id=tenant.id)
    return TenantSecurityPolicyResponse(
        tenant_id=tenant.id,
        updated_at=tenant.updated_at,
        **policy.to_dict(),
    )


async def _own_tenant_or_hidden_404(tenant_id: str, current_user: TokenData, db: AsyncSession):
    """The tenant row for the policy surface — loaded on the request session
    (the writer mutates and commits it); cross-tenant is a hidden 404 (§7:
    the API hides cross-tenant existence; the audit stream keeps the denial
    answer operational). Only an **unscoped** SYSTEM_ADMIN reads across
    tenants: a scoped credential stays in its own tenant."""

    tid = _coerce_uuid(tenant_id)
    if not is_unscoped_system_admin(current_user) and str(current_user.tenant_id) != str(tid):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )
    from sqlalchemy import select

    from app.models.tenant_model import TenantModel

    tenant = (
        await db.execute(select(TenantModel).where(TenantModel.id == tid))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


@router.get("/{tenant_id}/security-policy", response_model=TenantSecurityPolicyResponse)
async def get_tenant_security_policy(
    tenant_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantSecurityPolicyResponse:
    """The tenant's normalized security policy (gate: ``settings:read`` @
    tenant — staff reads, members are 403 at the central gate).

    The shape is the normalized policy, not the raw settings JSONB: unset
    knobs answer their defaults and unknown stored values never surface.
    The policy is read per request — a flip applies immediately (nothing
    is cached into tokens)."""
    tenant = await _own_tenant_or_hidden_404(tenant_id, current_user, db)
    return _policy_response(tenant)


@router.patch("/{tenant_id}/security-policy", response_model=TenantSecurityPolicyResponse)
async def update_tenant_security_policy(
    tenant_id: str,
    payload: TenantSecurityPolicyPatch,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN])),  # noqa: B008 -- framework default idiom (FastAPI/Pydantic)
    db: AsyncSession = Depends(get_db),
) -> TenantSecurityPolicyResponse:
    """Change the tenant security policy (gate + door: ``settings:write``
    @ tenant and the ADMIN role pin — SYSTEM_ADMIN passes every gate and
    may manage any tenant; tenant admins only their own, hidden 404
    otherwise).

    Partial patch: an omitted field is untouched, an explicit ``null``
    clears the nullable knob. Every change is audited (``required=True``,
    fail-closed) with a before/after diff of exactly the changed keys; the
    validation errors are 422 with the field path. Enforcement is
    immediate — the login/verification/refresh paths read the policy per
    request, never from the token."""
    tenant = await _own_tenant_or_hidden_404(tenant_id, current_user, db)
    changes = payload.model_dump(exclude_unset=True)
    if changes:
        before, after = await security_policy.update_security_policy(db, tenant, changes)
        # Same-transaction audit (S1): the audit row rides the request
        # session, so a policy change cannot land unrecorded — an audit
        # append failure rolls the change back and answers 500.
        await log_audit_action(
            required=True,
            session=db,
            tenant_id=tenant.id,
            user_id=current_user.user_id,
            action="tenant.security_policy",
            resource_type="tenant",
            resource_id=tenant.id,
            old_value=before,  # gate-allow: *, audit diff
            new_value=after,
        )
        await db.commit()
        await db.refresh(tenant)
    return _policy_response(tenant)
