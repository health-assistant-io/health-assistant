"""SCIM 2.0 endpoints — ``/scim/v2/Users`` and ``/scim/v2/Groups`` (Phase 12B).

Authentication is the token_scim principal class: a tenant-bounded SCIM
bearer (sha256-resolved ``scim_tokens`` row — never a session, never a
JWT). The resolved tenant plants the RLS context for the request and every
query filters ``tenant_id`` (the second wall behind the token bound).

Responses follow RFC 7644 shapes (ListResponse envelopes, Error schema in
the ``detail``); the app's typed errors map onto them in one place
(:func:`_scim_error`). Deprovision semantics: SCIM delete/``active=false``
deactivates the account and bumps ``token_version`` — every live session
dies — it never hard-deletes a record.
"""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenant_context
from app.core.authz.policy import RouteClass, policy
from app.core.database import get_db
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.scim_token import ScimToken
from app.services import scim
from app.services.auth.sso import SSOError

router = APIRouter(prefix="/scim/v2", tags=["scim"])

_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


def _scim_error(status_code: int, detail: str) -> HTTPException:
    """One SCIM Error-schema refusal (stable, client-parsable)."""
    return HTTPException(
        status_code=status_code,
        detail={"schemas": [_ERROR_SCHEMA], "status": str(status_code), "detail": detail},
    )


async def get_scim_principal(
    request: Request,
    authorization: str | None = Header(default=None),
) -> ScimToken:
    """The token_scim dependency — resolve the bearer, plant the tenant.

    A missing/unknown/revoked/expired bearer answers 401 (fail-closed,
    audited); the resolved credential plants its tenant on the RLS context
    (actor ``scim:<token id>``) so every request-side query runs inside the
    tenant. There is no system reach from a SCIM token — ever.
    """
    value = None
    if authorization and authorization.startswith("Bearer "):
        value = authorization[7:]
    principal = await scim.resolve_principal(value)
    if principal is None:
        await scim.audit_denied("scim.auth_denied", detail={"reason": "invalid_token"})
        raise _scim_error(status.HTTP_401_UNAUTHORIZED, "Invalid or expired authorization token")
    await scim.touch_last_used(principal)
    tenant_context.set_tenant(principal.tenant_id, actor=f"scim:{principal.id}")
    request.state.scim_principal = principal
    return principal


def _location_base(request: Request) -> str:
    root = str(request.base_url).rstrip("/")
    return f"{root}/api/v1/scim/v2"


def _page_params(
    start_index: int = Query(default=1, ge=1),
    count: int = Query(default=100, ge=0, le=200),
) -> tuple[int, int]:
    return start_index, count


def _domain_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, ValidationError):
        return _scim_error(status.HTTP_400_BAD_REQUEST, str(exc))
    if isinstance(exc, NotFoundError):
        return _scim_error(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, ConflictError):
        return _scim_error(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, SSOError):
        return _scim_error(exc.status_code, exc.message)
    raise exc


# --- Users -------------------------------------------------------------------


@policy(RouteClass.TOKEN_SCIM)
@router.get("/Users")
async def scim_list_users(
    request: Request,
    start_index: int = Query(default=1, ge=1, alias="startIndex"),
    count: int = Query(default=100, ge=0, le=200),
    filter: str | None = Query(default=None),
    principal: ScimToken = Depends(get_scim_principal),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List tenant users (RFC 7644 pagination; ``userName eq`` filter)."""
    try:
        email = scim.parse_user_filter(filter)
        users, total = await scim.list_users(
            db,
            tenant_id=principal.tenant_id,
            start_index=start_index,
            count=count,
            email_equals=email,
        )
    except ValidationError as exc:
        raise _scim_error(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {
        "schemas": [scim.SCIM_SCHEMA_LIST],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(users),
        "Resources": [scim.to_scim_user(u, location_base=_location_base(request)) for u in users],
    }


@policy(RouteClass.TOKEN_SCIM)
@router.get("/Users/{user_id}")
async def scim_get_user(
    request: Request,
    user_id,
    principal: ScimToken = Depends(get_scim_principal),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """One tenant user (a foreign-tenant id answers the same hidden 404)."""
    try:
        user = await scim.get_user(db, tenant_id=principal.tenant_id, user_id=user_id)
    except (ValidationError, NotFoundError) as exc:
        raise _domain_errors(exc) from exc
    return scim.to_scim_user(user, location_base=_location_base(request))


@policy(RouteClass.TOKEN_SCIM)
@router.post("/Users", status_code=status.HTTP_201_CREATED)
async def scim_create_user(
    request: Request,
    payload: dict[str, Any],
    principal: ScimToken = Depends(get_scim_principal),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Directory user creation (role capped to the tenant's SSO default)."""
    user_name = str(payload.get("userName") or "").strip()
    if not user_name:
        raise _scim_error(status.HTTP_400_BAD_REQUEST, "userName is required")
    name = payload.get("name") or {}
    full_name = str(name.get("formatted") if isinstance(name, dict) else "") or str(
        payload.get("displayName") or ""
    )
    active = payload.get("active", True)
    if not isinstance(active, bool):
        raise _scim_error(status.HTTP_400_BAD_REQUEST, "active must be a boolean")
    try:
        user = await scim.create_user(
            db,
            tenant_id=principal.tenant_id,
            user_name=user_name,
            full_name=full_name,
            active=active,
            actor_id=principal.id,
            principal=principal,
        )
        await db.commit()
    except (ValidationError, ConflictError) as exc:
        raise _domain_errors(exc) from exc
    body = scim.to_scim_user(user, location_base=_location_base(request))
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=body,
        headers={"Location": body["meta"]["location"]},
    )


@policy(RouteClass.TOKEN_SCIM)
@router.patch("/Users/{user_id}")
async def scim_patch_user(
    request: Request,
    user_id,
    payload: dict[str, Any],
    principal: ScimToken = Depends(get_scim_principal),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """RFC 7644 PatchOp (``active`` replace is the deprovision/restore door)."""
    operations = payload.get("operations")
    if not isinstance(operations, list):
        raise _scim_error(status.HTTP_400_BAD_REQUEST, "operations must be a list")
    try:
        user = await scim.patch_user(
            db,
            tenant_id=principal.tenant_id,
            user_id=user_id,
            operations=operations,
            actor_id=principal.id,
        )
        await db.commit()
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _domain_errors(exc) from exc
    return scim.to_scim_user(user, location_base=_location_base(request))


@policy(RouteClass.TOKEN_SCIM)
@router.delete("/Users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def scim_delete_user(
    user_id,
    principal: ScimToken = Depends(get_scim_principal),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """SCIM delete = deprovision: deactivate + ``token_version`` bump."""
    try:
        await scim.delete_user(
            db, tenant_id=principal.tenant_id, user_id=user_id, actor_id=principal.id
        )
        await db.commit()
    except (ValidationError, NotFoundError) as exc:
        raise _domain_errors(exc) from exc
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)


# --- Groups ------------------------------------------------------------------


@policy(RouteClass.TOKEN_SCIM)
@router.get("/Groups")
async def scim_list_groups(
    request: Request,
    start_index: int = Query(default=1, ge=1, alias="startIndex"),
    count: int = Query(default=100, ge=0, le=200),
    filter: str | None = Query(default=None),
    principal: ScimToken = Depends(get_scim_principal),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List tenant groups (``displayName eq`` filter)."""
    display_name = None
    if filter:
        stripped = filter.strip()
        parts = stripped.split('"')
        if len(parts) == 3 and parts[0].strip().lower() == "displayname eq":
            display_name = parts[1]
        else:
            raise _scim_error(
                status.HTTP_400_BAD_REQUEST,
                'Unsupported filter; only displayName eq "..." is implemented',
            )
    groups, total = await scim.list_groups(
        db,
        tenant_id=principal.tenant_id,
        start_index=start_index,
        count=count,
        display_name_equals=display_name,
    )
    resources = []
    for group in groups:
        members = await scim.group_members(db, group.id)
        resources.append(scim.to_scim_group(group, members, location_base=_location_base(request)))
    return {
        "schemas": [scim.SCIM_SCHEMA_LIST],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(groups),
        "Resources": resources,
    }


@policy(RouteClass.TOKEN_SCIM)
@router.get("/Groups/{group_id}")
async def scim_get_group(
    request: Request,
    group_id,
    principal: ScimToken = Depends(get_scim_principal),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        group = await scim.get_group(db, tenant_id=principal.tenant_id, group_id=group_id)
    except (ValidationError, NotFoundError) as exc:
        raise _domain_errors(exc) from exc
    members = await scim.group_members(db, group.id)
    return scim.to_scim_group(group, members, location_base=_location_base(request))


@policy(RouteClass.TOKEN_SCIM)
@router.post("/Groups", status_code=status.HTTP_201_CREATED)
async def scim_create_group(
    request: Request,
    payload: dict[str, Any],
    principal: ScimToken = Depends(get_scim_principal),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Directory group creation (members optional; role map applies)."""
    display_name = str(payload.get("displayName") or "").strip()
    member_ids = [m.get("value") for m in payload.get("members", []) if isinstance(m, dict)]
    try:
        group = await scim.create_group(
            db,
            tenant_id=principal.tenant_id,
            display_name=display_name,
            external_id=payload.get("externalId"),
            member_ids=member_ids,
            actor_id=principal.id,
        )
        await db.commit()
    except (ValidationError, ConflictError, NotFoundError) as exc:
        raise _domain_errors(exc) from exc
    members = await scim.group_members(db, group.id)
    body = scim.to_scim_group(group, members, location_base=_location_base(request))
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=body,
        headers={"Location": body["meta"]["location"]},
    )


@policy(RouteClass.TOKEN_SCIM)
@router.patch("/Groups/{group_id}")
async def scim_patch_group(
    request: Request,
    group_id,
    payload: dict[str, Any],
    principal: ScimToken = Depends(get_scim_principal),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Membership moves (audited; the group→role map re-roles members)."""
    operations = payload.get("operations")
    if not isinstance(operations, list):
        raise _scim_error(status.HTTP_400_BAD_REQUEST, "operations must be a list")
    try:
        group = await scim.patch_group(
            db,
            tenant_id=principal.tenant_id,
            group_id=group_id,
            operations=operations,
            actor_id=principal.id,
        )
        await db.commit()
    except (ValidationError, NotFoundError, ConflictError) as exc:
        raise _domain_errors(exc) from exc
    members = await scim.group_members(db, group.id)
    return scim.to_scim_group(group, members, location_base=_location_base(request))


@policy(RouteClass.TOKEN_SCIM)
@router.delete("/Groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def scim_delete_group(
    group_id,
    principal: ScimToken = Depends(get_scim_principal),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    try:
        await scim.delete_group(
            db, tenant_id=principal.tenant_id, group_id=group_id, actor_id=principal.id
        )
        await db.commit()
    except (ValidationError, NotFoundError) as exc:
        raise _domain_errors(exc) from exc
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
