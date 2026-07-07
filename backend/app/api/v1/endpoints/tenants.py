"""Tenant self-info endpoints.

The administrative surface (list, create, update, delete, switch, user
management) lives at ``/admin/tenants`` and is SYSTEM_ADMIN-only.

This module exposes only the routes a tenant member needs to see their
own tenant:
  * ``GET /tenants``          — the caller's own tenant summary.
  * ``GET /tenants/{id}``     — read a specific tenant (own tenant, or
    any tenant if the caller is SYSTEM_ADMIN).
  * ``PATCH /tenants/{id}``   — tenant admin self-service update
    (name / description / settings only).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, RoleChecker, TokenData
from app.models.enums import Role
from app.schemas.tenant import TenantResponse, TenantUpdate
from app.services.tenant_admin_service import TenantAdminService
from app.services.tenant_service import get_tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


def _coerce_uuid(value: str, field: str = "tenant_id") -> UUID:
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )
    return TenantResponse.model_validate(tenant)


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant_endpoint(
    tenant_id: str,
    current_user: TokenData = Depends(get_current_user),
) -> TenantResponse:
    """Read a tenant by ID.

    A non-SYSTEM_ADMIN caller can only read their own tenant. A switched
    SYSTEM_ADMIN (``switched=True``) reads the tenant their scoped token
    points at.
    """
    tid = _coerce_uuid(tenant_id)
    is_admin = current_user.role == Role.SYSTEM_ADMIN.value
    if not is_admin and str(current_user.tenant_id) != str(tid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this tenant.",
        )
    tenant = await get_tenant(tid)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )
    return TenantResponse.model_validate(tenant)


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_my_tenant_endpoint(
    tenant_id: str,
    payload: TenantUpdate,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
) -> TenantResponse:
    """Tenant-admin self-service update (name / description / settings).

    SYSTEM_ADMIN callers managing a tenant they don't own should use the
    richer surface at ``PATCH /admin/tenants/{id}`` instead.
    """
    tid = _coerce_uuid(tenant_id)
    if current_user.role != Role.SYSTEM_ADMIN.value and str(
        current_user.tenant_id
    ) != str(tid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this tenant.",
        )
    tenant = await TenantAdminService(db).update_tenant(
        tid, payload, actor_id=current_user.user_id
    )
    return TenantResponse.model_validate(tenant)
