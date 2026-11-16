"""Self-service scoped API tokens — ``/api/v1/me/api-tokens`` (12C2B).

The owner's credential surface: create (plaintext returned once), list,
inspect, update the permission subset / patient scope / expiry, rotate and
revoke. Every action is audited fail-closed, and every route is
session-only — a PAT may never mint, rotate or widen another PAT (that
would turn one bounded bearer into permanent account control).

The server is the gate: the permission picker payload mirrors the owner's
held catalog, but creation/update re-validate the subset server-side and
each use re-intersects it with the owner's live role.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.policy import RouteClass, policy
from app.core.authz.roles import permissions_for, scope_rule
from app.core.authz.scopes import AccessScope
from app.core.database import get_db
from app.core.security import TokenData, get_current_user
from app.models.api_token import PatientScope
from app.models.enums import Role
from app.schemas.api_token import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenPermissionCatalog,
    ApiTokenPermissionOption,
    ApiTokenResponse,
    ApiTokenUpdate,
)
from app.services.auth import api_tokens
from app.services.tenancy.user_service import get_user_by_id

router = APIRouter(prefix="/me/api-tokens", tags=["me"])


def _reject_pat(current_user: TokenData) -> None:
    """Defense in depth: only a session may manage tokens."""
    if isinstance(getattr(current_user, "pat_id", None), str):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API tokens cannot manage API tokens",
        )


async def _owner(current_user: TokenData):
    user = await get_user_by_id(str(current_user.user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@policy(RouteClass.SESSION)
@router.get("", response_model=list[ApiTokenResponse])
async def list_api_tokens(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Every token the caller owns (revoked and expired rows included)."""
    _reject_pat(current_user)
    rows = await api_tokens.list_tokens(
        db, owner_id=current_user.user_id, tenant_id=current_user.tenant_id
    )
    return [ApiTokenResponse.from_row(row) for row in rows]


@policy(RouteClass.SESSION)
@router.get("/permissions", response_model=ApiTokenPermissionCatalog)
async def get_permission_catalog(
    current_user: TokenData = Depends(get_current_user),
):
    """The caller's assignable permission set — the picker's source of truth."""
    _reject_pat(current_user)
    role = Role(current_user.role)
    options = [
        ApiTokenPermissionOption(
            permission=permission.value,
            scope=(scope_rule(role, permission) or AccessScope.OWN).value,
        )
        for permission in sorted(permissions_for(role), key=lambda item: item.value)
    ]
    return ApiTokenPermissionCatalog(
        permissions=options,
        patient_scopes=[scope.value for scope in PatientScope],
    )


@policy(RouteClass.SESSION)
@router.post("", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_api_token(
    payload: ApiTokenCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a token; the plaintext is returned exactly once."""
    _reject_pat(current_user)
    owner = await _owner(current_user)
    row, plaintext = await api_tokens.create_token(
        db,
        owner=owner,
        name=payload.name,
        permissions=payload.permissions,
        patient_scope=payload.patient_scope.value,
        patient_ids=payload.patient_ids,
        expires_at=payload.expires_at,
        created_by=current_user.user_id,
    )
    await db.commit()
    await db.refresh(row)
    return ApiTokenCreated(token=plaintext, api_token=ApiTokenResponse.from_row(row))


@policy(RouteClass.SESSION)
@router.get("/{token_id}", response_model=ApiTokenResponse)
async def get_api_token(
    token_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One owned token (another owner's id is a hidden 404)."""
    _reject_pat(current_user)
    row = await api_tokens.get_token(
        db,
        owner_id=current_user.user_id,
        tenant_id=current_user.tenant_id,
        token_id=token_id,
    )
    return ApiTokenResponse.from_row(row)


@policy(RouteClass.SESSION)
@router.patch("/{token_id}", response_model=ApiTokenResponse)
async def update_api_token(
    token_id: str,
    payload: ApiTokenUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update name, permission subset, patient scope or expiry."""
    _reject_pat(current_user)
    owner = await _owner(current_user)
    fields = payload.model_fields_set
    row = await api_tokens.update_token(
        db,
        owner=owner,
        token_id=token_id,
        name=payload.name if "name" in fields else api_tokens.UNSET,
        permissions=payload.permissions if "permissions" in fields else api_tokens.UNSET,
        patient_scope=payload.patient_scope if "patient_scope" in fields else api_tokens.UNSET,
        patient_ids=payload.patient_ids if "patient_ids" in fields else api_tokens.UNSET,
        expires_at=payload.expires_at if "expires_at" in fields else api_tokens.UNSET,
        actor_id=current_user.user_id,
    )
    await db.commit()
    await db.refresh(row)
    return ApiTokenResponse.from_row(row)


@policy(RouteClass.SESSION)
@router.post("/{token_id}/rotate", response_model=ApiTokenCreated)
async def rotate_api_token(
    token_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Issue a replacement and revoke the predecessor in one transaction."""
    _reject_pat(current_user)
    owner = await _owner(current_user)
    row, plaintext = await api_tokens.rotate_token(
        db, owner=owner, token_id=token_id, actor_id=current_user.user_id
    )
    await db.commit()
    await db.refresh(row)
    return ApiTokenCreated(token=plaintext, api_token=ApiTokenResponse.from_row(row))


@policy(RouteClass.SESSION)
@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(
    token_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke one token; the bearer stops resolving at once."""
    _reject_pat(current_user)
    await api_tokens.revoke_token(
        db,
        owner_id=current_user.user_id,
        tenant_id=current_user.tenant_id,
        token_id=token_id,
        actor_id=current_user.user_id,
    )
    await db.commit()
    return None
