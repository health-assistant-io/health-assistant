"""The caller's authorization identity — ``GET /api/v1/me/access``.

The health-owned access surface (ADR 0054, identity contract §12): the
family ``PublicUser`` shape stays untouched, so the permission set, the
scope inventory and the care-team grants ride this endpoint instead. The
frontend derives ``can(permission)`` from it; the server keeps enforcing
(the payload is a mirror, never the gate).

``patient_grants``/``pending_grants`` carry the caller's incoming care-team
grants (accepted / awaiting acceptance, minimal fields, patient label
resolved) — read per request, so a grant created or revoked mid-session is
honored on the next fetch. ``/me/access/grants`` lists both sets for the
self-service page; the accept/decline moves live in the grant service.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.roles import permissions_for
from app.core.database import get_db
from app.core.security import TokenData, get_current_user
from app.models.enums import Role
from app.models.fhir.patient import Patient
from app.models.patient_access_grant import GrantStatus, PatientAccessGrant
from app.schemas.access_elevation import ElevationResponse, elevation_to_response
from app.schemas.patient_access import GrantDecline, MeGrantItem
from app.services.auth.elevations import elevations_for_requester
from app.services.auth.patient_grants import (
    accept_grant,
    decline_grant,
    grants_for_grantee,
    patient_display_name,
)
from app.services.tenancy.tenant_service import get_tenant
from app.services.tenancy.user_service import get_user_by_id

router = APIRouter(prefix="/me", tags=["me"])


async def _project_grants(db: AsyncSession, grants: list[PatientAccessGrant]) -> list[MeGrantItem]:
    """Project grant rows to the minimal wire shape, resolving patient names."""
    if not grants:
        return []
    rows = await db.execute(
        select(Patient.id, Patient.name).where(
            Patient.id.in_({grant.patient_id for grant in grants})
        )
    )
    names = {patient_id: patient_display_name(name) for patient_id, name in rows.all()}
    return [
        MeGrantItem(
            id=grant.id,
            patient_id=grant.patient_id,
            patient_name=names.get(grant.patient_id),
            permission=grant.permission,
            source=grant.source,
            status=grant.status,
            granted_at=grant.granted_at,
            accepted_at=grant.accepted_at,
            expires_at=grant.expires_at,
        )
        for grant in grants
    ]


async def _incoming_grants(
    db: AsyncSession, current_user: TokenData
) -> tuple[list[MeGrantItem], list[MeGrantItem]]:
    """The caller's incoming grants: (active, pending)."""
    active = await grants_for_grantee(
        db, current_user.tenant_id, current_user.user_id, GrantStatus.ACTIVE
    )
    pending = await grants_for_grantee(
        db, current_user.tenant_id, current_user.user_id, GrantStatus.PENDING
    )
    return await _project_grants(db, active), await _project_grants(db, pending)


@router.get("/access")
async def my_access(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The caller's roles, effective permissions, tenant and grants.

    Session auth-only (no permission — self-service identity data, ADR 0054).
    """
    user = await get_user_by_id(current_user.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found in database")
    tenant = await get_tenant(current_user.tenant_id)
    role = current_user.role
    active, pending = await _incoming_grants(db, current_user)
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": getattr(user, "full_name", "") or "",
            "role": role,
        },
        "roles": [role],
        "permissions": sorted(p.value for p in permissions_for(role)),
        "tenant": {"id": str(current_user.tenant_id), "name": tenant.name if tenant else None},
        "patient_grants": [item.model_dump(mode="json") for item in active],
        "pending_grants": [item.model_dump(mode="json") for item in pending],
    }


@router.get("/access/grants")
async def my_grants(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The caller's incoming grants: accepted (``active``) and ``pending``."""
    active, pending = await _incoming_grants(db, current_user)
    return {
        "active": [item.model_dump(mode="json") for item in active],
        "pending": [item.model_dump(mode="json") for item in pending],
    }


@router.post("/access/grants/{grant_id}/accept")
async def accept_my_grant(
    grant_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Accept a pending share (only the granted staff member can)."""
    grant = await accept_grant(db, grant_id, current_user)
    return {"id": str(grant.id), "status": grant.status.value}


@router.post("/access/grants/{grant_id}/decline")
async def decline_my_grant(
    grant_id: UUID,
    payload: GrantDecline | None = None,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Decline a pending share (optionally with a reason)."""
    grant = await decline_grant(
        db, grant_id, current_user, reason=payload.reason if payload else None
    )
    return {"id": str(grant.id), "status": grant.status.value}


@router.get("/elevations", response_model=list[ElevationResponse])
async def my_elevations(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ElevationResponse]:
    """The caller's own break-glass elevations, newest first.

    Session auth-only (self-service identity data): the rows the caller
    requested — pending, live and settled — so they can see their own
    windows without any admin surface.
    """
    rows = await elevations_for_requester(
        db,
        current_user.user_id,
        current_user.tenant_id,
        is_system=current_user.role == Role.SYSTEM_ADMIN.value,
    )
    return [elevation_to_response(elevation) for elevation in rows]
