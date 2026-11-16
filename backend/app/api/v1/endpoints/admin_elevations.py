"""The admin break-glass elevation surface (ADR 0054 D1/D6 — Phase 12A).

``/admin/access-elevations`` — request a time-boxed widening of held
permissions, the ledger (tenant rows for tenant admins, system rows for
SYSTEM_ADMIN), and the second-person decisions: approve / deny / revoke.

The route door is ``break_glass:request`` for every action (the coarse
floor); the per-action authority is re-asserted by the elevation service —
approve/deny need the scope's approver role and a second person (the
requester's own row answers 403), revoke the requester or an eligible
approver. Cross-tenant rows answer hidden-404; both denials are audited
(fail-closed). Every transition writes an ``elevation_*`` audit row and
notifies the affected parties; a single-admin tenant has no approval path
by design (D6 — the host rescue runbook in ``SECURITY.md``, never a
bypass).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.roles import is_unscoped_system_admin
from app.core.database import get_db
from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.core.security import TokenData, get_current_user
from app.models.access_elevation import ElevationScope, ElevationStatus
from app.schemas.access_elevation import (
    ElevationDecisionBody,
    ElevationListResponse,
    ElevationRequest,
    ElevationResponse,
    elevation_to_response,
)
from app.services.audit.audit_service import OUTCOME_DENIED, log_audit_action
from app.services.auth import step_up
from app.services.auth.elevations import (
    approve_elevation,
    deny_elevation,
    list_elevations,
    request_elevation,
    revoke_elevation,
)

router = APIRouter(prefix="/admin/access-elevations", tags=["admin"])


def _response(elevation) -> ElevationResponse:
    return elevation_to_response(elevation)


async def _audit_denied(
    current_user: TokenData,
    action: str,
    detail: str,
    *,
    resource_id=None,
) -> None:
    """Record one fail-closed denial row (§17 — admin-surface refusals)."""
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action=action,
        resource_type="AccessElevation",
        resource_id=resource_id,
        outcome=OUTCOME_DENIED,
        new_value={"detail": detail},
    )


@router.post("", response_model=ElevationResponse, status_code=201)
async def request_access_elevation(
    payload: ElevationRequest,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ElevationResponse:
    """Open a break-glass request (pending until a second person approves).

    Step-up gated (program 12C1): emergency access is exactly the action a
    stolen bearer must not be able to start — the caller records a
    ``break_glass`` proof at ``POST /auth/step-up`` first.
    """
    await step_up.enforce(current_user, step_up.StepUpAction.BREAK_GLASS)
    elevation = await request_elevation(
        db,
        actor=current_user,
        scope=payload.scope,
        permissions=payload.permissions,
        duration_minutes=payload.duration_minutes,
        reason=payload.reason,
        metadata=payload.metadata,
    )
    return _response(elevation)


@router.get("", response_model=ElevationListResponse)
async def list_access_elevations(
    tenant_id: UUID | None = Query(
        default=None,
        description="Filter to one tenant. ADMIN callers are always scoped to "
        "their own tenant; a foreign id answers 404 (hidden).",
    ),
    scope: ElevationScope | None = Query(
        default=None,
        description="``tenant`` or ``system``; omit for everything reachable.",
    ),
    status_filter: ElevationStatus | None = Query(
        default=None,
        alias="status",
        description="Filter by lifecycle status (e.g. ``pending`` — the queue).",
    ),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ElevationListResponse:
    """The elevation ledger (newest first).

    ADMIN: their own tenant only — ``?tenant_id=<foreign>`` answers a
    hidden 404 and ``?scope=system`` a 403, both audited. SYSTEM_ADMIN:
    ``?tenant_id=`` narrows to one tenant, ``?scope=system`` the
    instance-level windows, omitted filters everything.
    """
    # A scoped credential naming SYSTEM_ADMIN reads its own tenant's ledger
    # only — the system rows are the unscoped operator's view (ADR 0054 D8).
    is_system = is_unscoped_system_admin(current_user)
    effective_tenant: UUID | None
    if is_system:
        effective_tenant = tenant_id
    else:
        own = UUID(str(current_user.tenant_id))
        if scope is ElevationScope.SYSTEM:
            await _audit_denied(current_user, "elevation_list_denied", "system_scope_admin_only")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="System-scope elevations require SYSTEM_ADMIN.",
            )
        if tenant_id is not None and tenant_id != own:
            await _audit_denied(current_user, "elevation_list_denied", "cross_tenant")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            )
        effective_tenant = own
    items, total = await list_elevations(
        db,
        tenant_id=effective_tenant,
        scope=scope if is_system else ElevationScope.TENANT,
        status=status_filter,
    )
    return ElevationListResponse(
        items=[_response(elevation) for elevation in items],
        total=total,
    )


async def _decide(
    db: AsyncSession,
    elevation_id: UUID,
    current_user: TokenData,
    move,
    *,
    verb: str,
    **kwargs,
):
    """Run one decision transition, mapping typed errors to HTTP (hidden 404).

    Every refusal writes an ``elevation_{verb}_denied`` row (fail-closed):
    a hidden 404 across tenants, a self-approval, a wrong approver role and
    a settled row are all decisions about a medical-access ledger, so the
    attempt itself must be visible to the audit reader.
    """
    try:
        return await move(db, elevation_id, current_user, **kwargs)
    except NotFoundError:
        await _audit_denied(
            current_user, f"elevation_{verb}_denied", "not_found", resource_id=elevation_id
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Access elevation not found"
        ) from None
    except AuthorizationError as exc:
        await _audit_denied(
            current_user, f"elevation_{verb}_denied", "not_authorized", resource_id=elevation_id
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from None
    except ConflictError as exc:
        await _audit_denied(
            current_user, f"elevation_{verb}_denied", "conflict", resource_id=elevation_id
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None


@router.post("/{elevation_id}/approve", response_model=ElevationResponse)
async def approve_access_elevation(
    elevation_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ElevationResponse:
    """Second-person approval (pending → active; the window opens now).

    Also step-up gated: approving *is* the decision that opens emergency
    access, so the approver proves presence too (deny/revoke do not — they
    only ever narrow access).
    """
    await step_up.enforce(current_user, step_up.StepUpAction.BREAK_GLASS)
    elevation = await _decide(db, elevation_id, current_user, approve_elevation, verb="approve")
    return _response(elevation)


@router.post("/{elevation_id}/deny", response_model=ElevationResponse)
async def deny_access_elevation(
    elevation_id: UUID,
    payload: ElevationDecisionBody | None = None,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ElevationResponse:
    """Second-person refusal (pending → denied)."""
    elevation = await _decide(
        db,
        elevation_id,
        current_user,
        deny_elevation,
        verb="deny",
        reason=payload.reason if payload else None,
    )
    return _response(elevation)


@router.post("/{elevation_id}/revoke", response_model=ElevationResponse)
async def revoke_access_elevation(
    elevation_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ElevationResponse:
    """Close a pending or active elevation early (→ revoked)."""
    elevation = await _decide(db, elevation_id, current_user, revoke_elevation, verb="revoke")
    return _response(elevation)
