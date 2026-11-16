"""Scoped integration & extension principals — ``/api/v1/integration-principals``.

Tenant admins (``integration:manage`` / ``tenant:manage``) manage every
principal in the tenant; an integration owner manages the principal bound to
their own integration. The route gate proves a session principal; the
ownership/tenant decision is handler-level because the rule is
"admin **or** owner" — a single permission cannot express it. A scoped
credential can never manage principals (no escalation).

The secret is returned exactly once (create/rotate); everything else is the
secret-free projection. Every action is audited fail-closed.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.permissions import Permission
from app.core.authz.policy import RouteClass, policy
from app.core.authz.roles import is_scoped_principal, principal_scope
from app.core.authz.scopes import AccessScope
from app.core.database import get_db
from app.core.security import TokenData, get_current_user
from app.models.user_model import UserModel
from app.schemas.integration_principal import (
    IntegrationPrincipalCreate,
    IntegrationPrincipalCreated,
    IntegrationPrincipalPermissionCatalog,
    IntegrationPrincipalPermissionOption,
    IntegrationPrincipalResponse,
    PrincipalPatientScope,
)
from app.services.integration import integration_principals
from app.services.tenancy.user_service import get_user_by_id

router = APIRouter(prefix="/integration-principals", tags=["integration-principals"])


def _reject_scoped(current_user: TokenData) -> None:
    """Only a session may manage principals (defense in depth)."""
    if is_scoped_principal(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Scoped credentials cannot manage integration principals",
        )


def _is_tenant_admin(current_user: TokenData) -> bool:
    """Whether the caller holds a tenant-wide principal-management permission."""
    for permission in (Permission.INTEGRATION_MANAGE, Permission.TENANT_MANAGE):
        scope = principal_scope(current_user, permission)
        if scope is not None and scope in (AccessScope.TENANT, AccessScope.SYSTEM):
            return True
    return False


def _require_owner_or_admin(current_user: TokenData, owner_id: UUID) -> None:
    if _is_tenant_admin(current_user):
        return
    if current_user.user_id == owner_id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


async def _owner(owner_id: UUID | None) -> UserModel:
    user = await get_user_by_id(str(owner_id)) if owner_id else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@policy(RouteClass.SESSION)
@router.get("", response_model=list[IntegrationPrincipalResponse])
async def list_integration_principals(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Principals the caller may see (tenant admins: all, owners: their own)."""
    _reject_scoped(current_user)
    owner_filter = None if _is_tenant_admin(current_user) else current_user.user_id
    rows = await integration_principals.list_principals(
        db, tenant_id=current_user.tenant_id, owner_id=owner_filter
    )
    return [IntegrationPrincipalResponse.from_row(row) for row in rows]


@policy(RouteClass.SESSION)
@router.get("/permissions", response_model=IntegrationPrincipalPermissionCatalog)
async def get_assignable_permissions(
    integration_id: UUID | None = None,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The assignable permissions for an integration (or the owner's catalog)."""
    _reject_scoped(current_user)
    domain: str | None = None
    if integration_id is not None:
        integration = (
            await db.execute(
                integration_principals.select_integration(
                    integration_id, tenant_id=current_user.tenant_id
                )
            )
        ).scalar_one_or_none()
        if integration is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Integration not found"
            )
        _require_owner_or_admin(current_user, integration.user_id)
        domain = integration.provider
    allowed = integration_principals.assignable_permissions(current_user, domain)
    return IntegrationPrincipalPermissionCatalog(
        permissions=[
            IntegrationPrincipalPermissionOption(
                permission=permission.value,
                scope=(principal_scope(current_user, permission) or AccessScope.OWN).value,
            )
            for permission in sorted(allowed, key=lambda item: item.value)
        ],
        patient_scopes=[scope.value for scope in PrincipalPatientScope],
    )


@policy(RouteClass.SESSION)
@router.post("", response_model=IntegrationPrincipalCreated, status_code=status.HTTP_201_CREATED)
async def create_integration_principal(
    payload: IntegrationPrincipalCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a principal; the secret is returned exactly once."""
    _reject_scoped(current_user)
    target_owner_id = payload.owner_user_id or current_user.user_id
    if target_owner_id != current_user.user_id and not _is_tenant_admin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    owner = await _owner(target_owner_id)
    if owner.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if payload.integration_id is not None:
        integration = (
            await db.execute(
                integration_principals.select_integration(
                    payload.integration_id, tenant_id=current_user.tenant_id
                )
            )
        ).scalar_one_or_none()
        if integration is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Integration not found"
            )
        _require_owner_or_admin(current_user, integration.user_id)
    row, secret = await integration_principals.create_principal(
        db,
        tenant_id=current_user.tenant_id,
        owner=owner,
        name=payload.name,
        kind=payload.kind.value,
        integration_id=payload.integration_id,
        scopes=payload.scopes,
        patient_scope=payload.patient_scope.value,
        patient_ids=payload.patient_ids,
        expires_at=payload.expires_at,
        created_by=current_user.user_id,
    )
    await db.commit()
    await db.refresh(row)
    return IntegrationPrincipalCreated(
        secret=secret, integration_principal=IntegrationPrincipalResponse.from_row(row)
    )


@policy(RouteClass.SESSION)
@router.get("/{principal_id}", response_model=IntegrationPrincipalResponse)
async def get_integration_principal(
    principal_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One principal (another owner's id is a hidden 404)."""
    _reject_scoped(current_user)
    owner_filter = None if _is_tenant_admin(current_user) else current_user.user_id
    row = await integration_principals.get_principal(
        db,
        tenant_id=current_user.tenant_id,
        principal_id=principal_id,
        owner_id=owner_filter,
    )
    return IntegrationPrincipalResponse.from_row(row)


@policy(RouteClass.SESSION)
@router.post("/{principal_id}/rotate", response_model=IntegrationPrincipalCreated)
async def rotate_integration_principal(
    principal_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Issue a replacement secret and revoke the predecessor in one transaction."""
    _reject_scoped(current_user)
    owner_filter = None if _is_tenant_admin(current_user) else current_user.user_id
    row, secret = await integration_principals.rotate_principal(
        db,
        tenant_id=current_user.tenant_id,
        principal_id=principal_id,
        actor_id=current_user.user_id,
        owner_id=owner_filter,
    )
    await db.commit()
    await db.refresh(row)
    return IntegrationPrincipalCreated(
        secret=secret, integration_principal=IntegrationPrincipalResponse.from_row(row)
    )


@policy(RouteClass.SESSION)
@router.delete("/{principal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_integration_principal(
    principal_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke one principal; the secret stops resolving at once."""
    _reject_scoped(current_user)
    owner_filter = None if _is_tenant_admin(current_user) else current_user.user_id
    await integration_principals.revoke_principal(
        db,
        tenant_id=current_user.tenant_id,
        principal_id=principal_id,
        actor_id=current_user.user_id,
        owner_id=owner_filter,
    )
    await db.commit()
    return None


__all__ = ["router"]
