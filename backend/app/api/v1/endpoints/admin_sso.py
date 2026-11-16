"""Tenant-admin SSO configuration + SCIM token surfaces (Phase 12B).

``/admin/tenants/{tenant_id}/sso`` — the per-tenant OIDC bindings
(CRUD + test-connection). ``/admin/tenants/{tenant_id}/scim-tokens`` — the
directory credentials (create with the **one-time plaintext return**,
list, revoke).

Authority: the route gate opens the door (``settings:read``/``settings:write``
for SSO config, ``tenant:manage`` for SCIM tokens, both at TENANT scope);
the service layer re-asserts the tenant boundary — ADMIN acts inside its
own tenant only (a foreign id answers the hidden 404), SYSTEM_ADMIN
anywhere. Every mutation is audited fail-closed; a secret value never
crosses this surface (slot names only).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.permissions import Permission
from app.core.authz.policy import RouteClass, policy
from app.core.authz.scopes import AccessScope
from app.core.database import get_db
from app.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.security import RoleChecker, TokenData
from app.models.enums import Role
from app.schemas.sso import (
    SCIMTokenCreate,
    SCIMTokenCreatedResponse,
    SCIMTokenListResponse,
    SCIMTokenResponse,
    SSOConfigCreate,
    SSOConfigListResponse,
    SSOConfigResponse,
    SSOConfigUpdate,
    SSOTestConnectionResponse,
)
from app.services import scim
from app.services.audit.audit_service import OUTCOME_DENIED, log_audit_action
from app.services.auth import sso

router = APIRouter(prefix="/admin/tenants", tags=["admin"])

_admin_only = RoleChecker([Role.ADMIN])


def _coerce_uuid(value: str, field: str = "tenant_id") -> UUID:
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(  # noqa: B904 -- bare raise; add explicit chaining when touched
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field}: must be a valid UUID.",
        )


async def _require_tenant_access(
    db: AsyncSession, current_user: TokenData, tenant_id: UUID, action: str
) -> None:
    """The tenant boundary: ADMIN own tenant, SYSTEM_ADMIN anywhere.

    A cross-tenant ADMIN refusal is audited (fail-closed) and answered as
    the hidden 404 (the tenant's existence is not disclosed).
    """
    if current_user.role == Role.SYSTEM_ADMIN.value:
        return
    if str(current_user.tenant_id) != str(tenant_id):
        await log_audit_action(
            required=True,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action=action,
            resource_type="tenant",
            resource_id=tenant_id,
            outcome=OUTCOME_DENIED,
            new_value={"detail": "cross_tenant"},
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")


def _map_domain_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, ValidationError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, AuthorizationError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    raise exc


# --- SSO config CRUD ---------------------------------------------------------


@policy(RouteClass.SESSION, permission=Permission.SETTINGS_READ, scope=AccessScope.TENANT)
@router.get("/{tenant_id}/sso", response_model=SSOConfigListResponse)
async def list_sso_configs(
    tenant_id: str,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> SSOConfigListResponse:
    """The tenant's SSO bindings (secrets appear as slot references only)."""
    tid = _coerce_uuid(tenant_id)
    await _require_tenant_access(db, current_user, tid, "sso_config_list_denied")
    configs = await sso.list_sso_configs(db, tenant_id=tid)
    return SSOConfigListResponse(
        items=[SSOConfigResponse.model_validate(c) for c in configs],
        total=len(configs),
    )


@policy(RouteClass.SESSION, permission=Permission.SETTINGS_WRITE, scope=AccessScope.TENANT)
@router.post("/{tenant_id}/sso", response_model=SSOConfigResponse, status_code=201)
async def create_sso_config(
    tenant_id: str,
    payload: SSOConfigCreate,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> SSOConfigResponse:
    """Bind one OIDC provider (issuer unique per tenant; starts disabled)."""
    tid = _coerce_uuid(tenant_id)
    await _require_tenant_access(db, current_user, tid, "sso_config_create_denied")
    try:
        config = await sso.create_sso_config(
            db,
            tenant_id=tid,
            actor_id=current_user.user_id,
            **payload.model_dump(),
        )
        await db.commit()
    except (ValidationError, ConflictError) as exc:
        raise _map_domain_errors(exc) from exc
    return SSOConfigResponse.model_validate(config)


@policy(RouteClass.SESSION, permission=Permission.SETTINGS_WRITE, scope=AccessScope.TENANT)
@router.patch("/{tenant_id}/sso/{config_id}", response_model=SSOConfigResponse)
async def update_sso_config(
    tenant_id: str,
    config_id: str,
    payload: SSOConfigUpdate,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> SSOConfigResponse:
    tid = _coerce_uuid(tenant_id)
    cid = _coerce_uuid(config_id, field="config_id")
    await _require_tenant_access(db, current_user, tid, "sso_config_update_denied")
    try:
        config = await sso.update_sso_config(
            db,
            tenant_id=tid,
            config_id=cid,
            actor_id=current_user.user_id,
            changes=payload.model_dump(exclude_unset=True),
        )
        await db.commit()
    except (ValidationError, ConflictError, NotFoundError) as exc:
        raise _map_domain_errors(exc) from exc
    return SSOConfigResponse.model_validate(config)


@policy(RouteClass.SESSION, permission=Permission.SETTINGS_WRITE, scope=AccessScope.TENANT)
@router.delete("/{tenant_id}/sso/{config_id}", status_code=status.HTTP_200_OK)
async def delete_sso_config(
    tenant_id: str,
    config_id: str,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Unbind the provider (existing links keep resolving; the flow is off)."""
    tid = _coerce_uuid(tenant_id)
    cid = _coerce_uuid(config_id, field="config_id")
    await _require_tenant_access(db, current_user, tid, "sso_config_delete_denied")
    try:
        await sso.delete_sso_config(db, tenant_id=tid, config_id=cid, actor_id=current_user.user_id)
        await db.commit()
    except NotFoundError as exc:
        raise _map_domain_errors(exc) from exc
    return {"message": "SSO configuration deleted."}


@policy(RouteClass.SESSION, permission=Permission.SETTINGS_WRITE, scope=AccessScope.TENANT)
@router.post(
    "/{tenant_id}/sso/{config_id}/test-connection",
    response_model=SSOTestConnectionResponse,
)
async def test_sso_connection(
    tenant_id: str,
    config_id: str,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> SSOTestConnectionResponse:
    """Discovery + secret-slot probe (bounded, redacted — never echoes)."""
    tid = _coerce_uuid(tenant_id)
    cid = _coerce_uuid(config_id, field="config_id")
    await _require_tenant_access(db, current_user, tid, "sso_config_test_denied")
    try:
        config = await sso.get_tenant_config(db, tid, cid)
    except NotFoundError as exc:
        raise _map_domain_errors(exc) from exc
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="SSO configuration not found"
        )
    result = await sso.test_connection(config)
    await log_audit_action(
        required=True,
        tenant_id=tid,
        user_id=current_user.user_id,
        action="sso_config_tested",
        resource_type="tenant_sso_config",
        resource_id=cid,
        new_value={
            "discovery_ok": result.get("discovery_ok", False),
            "secret_resolved": result.get("secret_resolved", False),
        },
    )
    return SSOTestConnectionResponse(**result)


# --- SCIM tokens -------------------------------------------------------------


@policy(RouteClass.SESSION, permission=Permission.TENANT_MANAGE, scope=AccessScope.TENANT)
@router.get("/{tenant_id}/scim-tokens", response_model=SCIMTokenListResponse)
async def list_scim_tokens(
    tenant_id: str,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> SCIMTokenListResponse:
    """The tenant's SCIM credentials (hash-free projections)."""
    tid = _coerce_uuid(tenant_id)
    await _require_tenant_access(db, current_user, tid, "scim_token_list_denied")
    tokens = await scim.list_tokens(db, tenant_id=tid)
    return SCIMTokenListResponse(
        items=[SCIMTokenResponse.model_validate(t) for t in tokens],
        total=len(tokens),
    )


@policy(RouteClass.SESSION, permission=Permission.TENANT_MANAGE, scope=AccessScope.TENANT)
@router.post(
    "/{tenant_id}/scim-tokens",
    response_model=SCIMTokenCreatedResponse,
    status_code=201,
)
async def create_scim_token(
    tenant_id: str,
    payload: SCIMTokenCreate,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> SCIMTokenCreatedResponse:
    """Create one credential — the plaintext rides **this response only**."""
    tid = _coerce_uuid(tenant_id)
    await _require_tenant_access(db, current_user, tid, "scim_token_create_denied")
    try:
        row, plaintext = await scim.create_token(
            db,
            tenant_id=tid,
            name=payload.name,
            created_by=current_user.user_id,
            expires_at=payload.expires_at,
        )
        await db.commit()
    except ValidationError as exc:
        raise _map_domain_errors(exc) from exc
    response = SCIMTokenCreatedResponse(
        id=row.id,
        name=row.name,
        scopes=list(row.scopes or []),
        active=bool(row.active),
        last_used_at=row.last_used_at,
        expires_at=row.expires_at,
        created_at=row.created_at,
        token=plaintext,
    )
    return response


@policy(RouteClass.SESSION, permission=Permission.TENANT_MANAGE, scope=AccessScope.TENANT)
@router.delete("/{tenant_id}/scim-tokens/{token_id}", status_code=status.HTTP_200_OK)
async def revoke_scim_token(
    tenant_id: str,
    token_id: str,
    current_user: TokenData = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Revoke: the bearer stops resolving on the very next request."""
    tid = _coerce_uuid(tenant_id)
    token_uuid = _coerce_uuid(token_id, field="token_id")
    await _require_tenant_access(db, current_user, tid, "scim_token_revoke_denied")
    try:
        await scim.revoke_token(
            db, tenant_id=tid, token_id=token_uuid, actor_id=current_user.user_id
        )
        await db.commit()
    except (NotFoundError, ConflictError) as exc:
        raise _map_domain_errors(exc) from exc
    return {"message": "SCIM token revoked."}
