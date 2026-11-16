"""The patient access-grant surface (ADR 0054 Phase 2 — program 7A).

``/patients/{patient_id}/access`` — who can reach this patient's record:
list the ledger, grant (staff: ``admin``/``care_team`` active immediately;
the patient owner: a pending ``patient`` share gated by the tenant's
``allow_patient_share``; ``guardian`` is admin-only for ownerless
patients), update (permission/expiry/reason) and revoke. ``patient:grant``
is the door permission; the row-level answer (reach + level) comes from
``check_patient_access`` — hidden-404 cross-tenant, 403 in-tenant foreign,
409 on lifecycle conflicts, 422 on bad shapes.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import NotFoundError
from app.core.security import TokenData, get_current_user
from app.models.doctor_model import DoctorModel
from app.models.patient_access_grant import GrantPermission
from app.models.user_model import UserModel
from app.schemas.patient_access import (
    GrantCreate,
    GrantListResponse,
    GrantResponse,
    GrantUpdate,
    grant_to_response,
)
from app.services.audit.audit_service import audit_read
from app.services.auth import step_up
from app.services.auth.access import check_patient_access
from app.services.auth.patient_grants import (
    create_grant,
    list_patient_grants,
    revoke_grant,
    update_grant,
)

router = APIRouter(prefix="/patients/{patient_id}/access", tags=["patient-access"])


async def _grantee_labels(db: AsyncSession, grants) -> dict[UUID, str]:
    """Resolve display labels for the ledger's grantee principals."""
    labels: dict[UUID, str] = {}
    user_ids = {g.grantee_user_id for g in grants if g.grantee_user_id is not None}
    doctor_ids = {g.grantee_doctor_id for g in grants if g.grantee_doctor_id is not None}
    if user_ids:
        rows = await db.execute(
            select(UserModel.id, UserModel.full_name, UserModel.email).where(
                UserModel.id.in_(user_ids)
            )
        )
        for user_id, full_name, email in rows.all():
            labels[user_id] = full_name or email
    if doctor_ids:
        rows = await db.execute(
            select(DoctorModel.id, DoctorModel.name).where(DoctorModel.id.in_(doctor_ids))
        )
        for doctor_id, name in rows.all():
            labels[doctor_id] = name
    return labels


@router.get("", response_model=GrantListResponse)
@audit_read("PatientAccessGrant", id_param="patient_id")
async def list_access(
    patient_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GrantListResponse:
    """The patient's grant ledger (active, pending and settled rows)."""
    await check_patient_access(patient_id, current_user, db)
    grants = await list_patient_grants(db, current_user.tenant_id, patient_id)
    labels = await _grantee_labels(db, grants)
    return GrantListResponse(
        items=[
            grant_to_response(
                grant,
                labels.get(grant.grantee_user_id) or labels.get(grant.grantee_doctor_id),
            )
            for grant in grants
        ]
    )


@router.post("", response_model=GrantResponse, status_code=201)
async def grant_access(
    patient_id: UUID,
    payload: GrantCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GrantResponse:
    """Grant access (staff: immediate; patient share: pending acceptance).

    Admin-level reach is step-up gated (program 12C1): a stolen bearer must
    not be able to hand a patient's record to a third party. The proof is
    ``grant_admin`` and is checked here.
    """
    await step_up.enforce(current_user, step_up.StepUpAction.GRANT_ADMIN)
    patient = await check_patient_access(
        patient_id, current_user, db, required=GrantPermission.ADMIN
    )
    grant = await create_grant(
        db,
        patient=patient,
        actor=current_user,
        permission=payload.permission,
        grantee_user_id=payload.grantee_user_id,
        grantee_doctor_id=payload.grantee_doctor_id,
        source=payload.source,
        expires_at=payload.expires_at,
        reason=payload.reason,
    )
    labels = await _grantee_labels(db, [grant])
    return grant_to_response(
        grant, labels.get(grant.grantee_user_id) or labels.get(grant.grantee_doctor_id)
    )


async def _load_managed_grant(
    patient_id: UUID, grant_id: UUID, current_user: TokenData, db: AsyncSession
):
    """The patient (admin-level reach) + one of its grants, or hidden 404."""
    await check_patient_access(patient_id, current_user, db, required=GrantPermission.ADMIN)
    grants = await list_patient_grants(db, current_user.tenant_id, patient_id)
    for grant in grants:
        if grant.id == grant_id:
            return grant
    raise NotFoundError("Access grant not found")


@router.patch("/{grant_id}", response_model=GrantResponse)
async def update_access(
    patient_id: UUID,
    grant_id: UUID,
    payload: GrantUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GrantResponse:
    """Update a grant's permission / expiry / reason (grantor authority).

    Clear semantics: a JSON ``null`` the client sends clears the field
    (``expires_at``/``reason``); an omitted field is left unchanged — so
    only sent fields are forwarded to the service. Raising a grant **to
    admin** is the privileged transition (program 12C1): it needs a fresh
    ``grant_admin`` step-up proof, while narrowing a permission or moving
    an expiry does not.
    """
    await _load_managed_grant(patient_id, grant_id, current_user, db)
    if payload.permission == GrantPermission.ADMIN:
        await step_up.enforce(current_user, step_up.StepUpAction.GRANT_ADMIN)
    grant = await update_grant(
        db,
        grant_id,
        current_user,
        **payload.model_dump(exclude_unset=True),
    )
    labels = await _grantee_labels(db, [grant])
    return grant_to_response(
        grant, labels.get(grant.grantee_user_id) or labels.get(grant.grantee_doctor_id)
    )


@router.delete("/{grant_id}", response_model=GrantResponse)
async def revoke_access(
    patient_id: UUID,
    grant_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GrantResponse:
    """Revoke a grant (the grantee immediately loses access)."""
    await _load_managed_grant(patient_id, grant_id, current_user, db)
    grant = await revoke_grant(db, grant_id, current_user)
    labels = await _grantee_labels(db, [grant])
    return grant_to_response(
        grant, labels.get(grant.grantee_user_id) or labels.get(grant.grantee_doctor_id)
    )
