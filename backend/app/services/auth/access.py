# ruff: noqa: B904 -- long immutable strings; reflow when touched
"""Tenant/patient access-check helpers (audit C2).

Canonical resource-access checks shared by the router layer AND the service
layer. Each helper: (1) str→UUID coerces, (2) selects by id AND tenant
(cross-tenant → 404), (3) for the ``USER`` role verifies the resource belongs
to a patient owned by ``current_user.user_id`` (403 otherwise), (4) returns
the model instance.

These raise domain exceptions (``NotFoundError`` / ``AuthorizationError`` /
``ValidationError`` from :mod:`app.core.errors`), NOT ``HTTPException``. The
global ``DomainError`` handler in ``main.py`` maps each to its HTTP status,
so the same helper is callable from a FastAPI endpoint, a Celery task, an
importer, or the FHIR facade without coupling to Starlette/HTTPException.

This module lives under ``app/services`` (not ``app/api``) precisely so the
service layer can import it without a layer inversion — the audit (C2) found
``clinical_event_service`` importing these from ``app.api.v1.endpoints.utils``,
which coupled it to the router and made it unusable from Celery/import/facade.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.permissions import Permission
from app.core.authz.roles import is_scoped_principal, principal_has
from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.models.clinical_event import ClinicalEvent
from app.models.enums import Role
from app.models.examination_model import ExaminationModel
from app.models.fhir.allergy import AllergyIntolerance
from app.models.fhir.medication import Medication
from app.models.fhir.patient import Observation, Patient
from app.models.fhir.vaccine import PatientImmunization
from app.models.patient_access_grant import GrantPermission, PatientAccessGrant
from app.schemas.user import TokenData
from app.services.auth.patient_grants import (
    PERMISSION_RANK,
    grantee_clause,
    live_grant_filters,
    tenant_access_settings,
)


def _coerce_uuid(value: str | UUID, label: str = "ID") -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value)
    except ValueError:
        raise ValidationError(f"Invalid {label} format") from None


def require_principal_permission(principal: TokenData, permission: Permission) -> None:
    """Refuse a write whose permission the acting principal does not hold.

    Session principals resolve to their role defaults (every role that can
    reach a write surface already holds the permission, so this is a no-op
    there); a scoped credential answers its own live, intersected permission
    list. Enforcing it here — at the canonical service seam — closes the
    path where a ``patient:read``-only PAT or integration principal reaches
    a service write through an ingest/sync funnel that never runs the route
    gate.

    Raises:
        AuthorizationError: the principal lacks ``permission``.
    """
    if principal_has(principal, permission):
        return
    raise AuthorizationError(f"This credential does not hold {permission.value}")


def is_pat_principal(principal: TokenData) -> bool:
    """Whether the principal is a scoped personal access token."""
    return isinstance(getattr(principal, "pat_id", None), str)


def is_integration_principal(principal: TokenData) -> bool:
    """Whether the principal is a scoped integration/extension identity."""
    return isinstance(getattr(principal, "integration_principal_id", None), str)


def _scoped_scope_and_ids(principal: TokenData) -> tuple[str | None, list[str]]:
    if is_pat_principal(principal):
        return principal.pat_patient_scope, list(principal.pat_patient_ids or [])
    return (
        getattr(principal, "integration_patient_scope", None),
        list(getattr(principal, "integration_patient_ids", None) or []),
    )


async def scoped_patient_reach(
    current_user: TokenData,
    db: AsyncSession,
    *,
    required: GrantPermission | str = GrantPermission.READ,
    include_grants: bool = True,
) -> set[UUID] | None:
    """A scoped credential's effective patient reach as an **intersection**.

    PATs and integration principals share the rule: first the owner's live
    reach (role rules + own patients + live grants at ``required`` level;
    ``None`` = tenant-wide), then the credential's ``patient_scope``:
    ``none`` → empty, ``granted`` → the explicit allowlist, ``all`` → the
    owner's reach unchanged. A revoked grant or a role change therefore
    narrows every outstanding credential, and the explicit allowlist can
    never widen past the owner.
    """
    from app.models.api_token import PatientScope

    scope_value, allowlist_values = _scoped_scope_and_ids(current_user)
    try:
        scope = PatientScope(scope_value)
    except ValueError:
        return set()

    if isinstance(required, str):
        required = GrantPermission(required)
    owner = TokenData(
        user_id=current_user.user_id,
        tenant_id=current_user.tenant_id,
        role=current_user.role,
        sub=current_user.sub,
        email=current_user.email,
    )
    role = Role(owner.role)
    owner_reach: set[UUID] | None
    if role in (Role.SYSTEM_ADMIN, Role.ADMIN):
        owner_reach = None
    elif role is Role.MANAGER:
        tenant_settings = await tenant_access_settings(db, owner.tenant_id)
        owner_reach = (
            None
            if tenant_settings.manager_scope == "tenant"
            else await _own_and_granted(owner, db, required=required, include_grants=include_grants)
        )
    else:
        owner_reach = await _own_and_granted(
            owner, db, required=required, include_grants=include_grants
        )

    if scope is PatientScope.NONE:
        return set()
    if scope is PatientScope.ALL:
        return owner_reach
    allowlist = {UUID(str(value)) for value in allowlist_values}
    if not allowlist:
        return set()
    if owner_reach is None:
        return allowlist
    return owner_reach & allowlist


async def _own_and_granted(
    owner: TokenData,
    db: AsyncSession,
    *,
    required: GrantPermission,
    include_grants: bool,
) -> set[UUID]:
    own = select(Patient.id).where(
        Patient.tenant_id == owner.tenant_id,
        Patient.user_id == owner.user_id,
    )
    if not include_grants:
        result = await db.execute(own)
        return {row[0] for row in result.all()}
    satisfying = tuple(
        permission.value
        for permission in GrantPermission
        if PERMISSION_RANK[permission] >= PERMISSION_RANK[required]
    )
    granted = select(PatientAccessGrant.patient_id).where(
        *live_grant_filters(owner.tenant_id, owner.user_id),
        PatientAccessGrant.permission.in_(satisfying),
    )
    result = await db.execute(own.union(granted))
    return {row[0] for row in result.all()}


async def accessible_patient_ids(current_user: TokenData, db: AsyncSession) -> set[UUID] | None:
    """The patient ids the caller may reach in their tenant, or ``None``.

    ``None`` means "unrestricted within the caller's tenant" (SYSTEM_ADMIN,
    ADMIN, and a MANAGER whose tenant keeps the default
    ``manager_scope=tenant``) — the caller still applies its own tenant
    filter. A set means own + active non-expired grants (USER always; a
    MANAGER under ``manager_scope=assigned``). The set is read per request:
    flipping the tenant setting takes effect on the next call, and grants
    created or revoked mid-session are honored immediately. A scoped
    credential (PAT or integration principal) answers its identity-and-owner
    intersection (:func:`scoped_patient_reach`), never the owner's full reach.
    """
    if is_scoped_principal(current_user):
        return await scoped_patient_reach(current_user, db)
    role = Role(current_user.role)
    if role in (Role.SYSTEM_ADMIN, Role.ADMIN):
        return None
    if role is Role.MANAGER:
        settings = await tenant_access_settings(db, current_user.tenant_id)
        if settings.manager_scope == "tenant":
            return None

    own = select(Patient.id).where(
        Patient.tenant_id == current_user.tenant_id,
        Patient.user_id == current_user.user_id,
    )
    granted = select(PatientAccessGrant.patient_id).where(
        *live_grant_filters(current_user.tenant_id, current_user.user_id)
    )
    result = await db.execute(own.union(granted))
    return {row[0] for row in result.all()}


def patient_scope_condition(column, patient_ids: set[UUID] | None):
    """A list-filter predicate over a ``*.patient_id`` column, or ``None``.

    ``None`` (tenant-wide roles) removes the filter; an empty set yields a
    false() condition so a MANAGER/user with no reachable patients sees no
    rows rather than the world. Pair with the caller's own tenant filter.
    """
    from sqlalchemy import false

    if patient_ids is None:
        return None
    if not patient_ids:
        return false()
    return column.in_(patient_ids)


async def apply_patient_scope(stmt, column, current_user: TokenData, db: AsyncSession):
    """Restrict ``stmt`` (already tenant-filtered) to the caller's patients."""
    condition = patient_scope_condition(column, await accessible_patient_ids(current_user, db))
    return stmt.where(condition) if condition is not None else stmt


async def check_patient_access(
    patient_id: str | UUID,
    current_user: TokenData,
    db: AsyncSession,
    required: GrantPermission | str = GrantPermission.READ,
):
    """Verify the current user may reach this patient at ``required`` level.

    Layered on the tenancy rule (cross-tenant → hidden 404; in-tenant
    foreign patient → 403): SYSTEM_ADMIN passes everywhere; ADMIN passes
    tenant-wide; MANAGER passes tenant-wide under ``manager_scope=tenant``
    (the default) or via own + grants under ``assigned``; USER passes on
    their own patients (ownership carries ``admin``-level authority over
    the record) or through an active non-expired grant whose permission
    satisfies ``required`` — a ``read`` grant never silently becomes a
    write. Expired/revoked grants are ignored.
    """
    if isinstance(required, str):
        required = GrantPermission(required)
    patient_uuid = _coerce_uuid(patient_id, "patient ID")

    result = await db.execute(
        select(Patient).where(
            Patient.id == patient_uuid, Patient.tenant_id == current_user.tenant_id
        )
    )
    patient = result.scalar_one_or_none()

    if not patient:
        raise NotFoundError("Patient not found")

    if is_scoped_principal(current_user):
        reach = await scoped_patient_reach(current_user, db, required=required, include_grants=True)
        if reach is None or patient_uuid in reach:
            return patient
        raise AuthorizationError("Access denied to this patient's data")

    role = Role(current_user.role)
    if role is Role.SYSTEM_ADMIN or role is Role.ADMIN:
        return patient
    if role is Role.MANAGER:
        settings = await tenant_access_settings(db, current_user.tenant_id)
        if settings.manager_scope == "tenant":
            return patient

    is_own = bool(patient.user_id) and str(patient.user_id) == str(current_user.user_id)
    if is_own:
        return patient

    grant_result = await db.execute(
        select(PatientAccessGrant.permission).where(
            PatientAccessGrant.tenant_id == current_user.tenant_id,
            PatientAccessGrant.patient_id == patient_uuid,
            *live_grant_filters(current_user.tenant_id),
            grantee_clause(current_user.user_id),
        )
    )
    best_rank = max(
        (PERMISSION_RANK[GrantPermission(p)] for p in grant_result.scalars().all()),
        default=-1,
    )
    if best_rank >= PERMISSION_RANK[required]:
        return patient

    raise AuthorizationError("Access denied to this patient's data")


async def check_patient_export_access(
    patient_id: str | UUID,
    current_user: TokenData,
    db: AsyncSession,
):
    """Export ride-along check (ADR 0054 D5): grants never widen export.

    Bulk exfiltration is a different decision from reading: a care-team
    ``read``/``write`` grant does NOT satisfy this check. SYSTEM_ADMIN
    passes anywhere; ADMIN/MANAGER pass tenant-wide (MANAGER export stays
    the role-scope default — ``manager_scope=assigned`` narrowing of export
    surfaces is recorded as a follow-up); USER may export only patients
    they own.
    """
    patient_uuid = _coerce_uuid(patient_id, "patient ID")
    result = await db.execute(
        select(Patient).where(
            Patient.id == patient_uuid, Patient.tenant_id == current_user.tenant_id
        )
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise NotFoundError("Patient not found")

    if is_scoped_principal(current_user):
        reach = await scoped_patient_reach(current_user, db, include_grants=False)
        if reach is None or patient_uuid in reach:
            return patient
        raise AuthorizationError("You can only export your own records")

    role = Role(current_user.role)
    if role is Role.SYSTEM_ADMIN or role in (Role.ADMIN, Role.MANAGER):
        return patient
    if patient.user_id and str(patient.user_id) == str(current_user.user_id):
        return patient
    raise AuthorizationError("You can only export your own records")


async def check_examination_access(
    examination_id: str | UUID,
    current_user: TokenData,
    db: AsyncSession,
    required: GrantPermission = GrantPermission.READ,
):
    """Verify the current user has access to the specified examination"""
    if isinstance(examination_id, str):
        try:
            examination_id = UUID(examination_id)
        except ValueError:
            raise ValidationError("Invalid examination ID format")

    result = await db.execute(
        select(ExaminationModel).where(
            ExaminationModel.id == examination_id,
            ExaminationModel.tenant_id == current_user.tenant_id,
        )
    )
    examination = result.scalar_one_or_none()

    if not examination:
        raise NotFoundError("Examination not found")

    await check_patient_access(examination.patient_id, current_user, db, required)

    return examination


async def check_medication_access(
    medication_id: str | UUID,
    current_user: TokenData,
    db: AsyncSession,
    required: GrantPermission = GrantPermission.READ,
):
    """Verify the current user has access to the specified medication record"""
    if isinstance(medication_id, str):
        try:
            medication_id = UUID(medication_id)
        except ValueError:
            raise ValidationError("Invalid medication ID format")

    result = await db.execute(
        select(Medication).where(
            Medication.id == medication_id,
            Medication.tenant_id == current_user.tenant_id,
            Medication.deleted_at.is_(None),
        )
    )
    medication = result.scalar_one_or_none()

    if not medication:
        raise NotFoundError("Medication record not found")

    await check_patient_access(medication.patient_id, current_user, db, required)

    return medication


async def check_immunization_access(
    immunization_id: str | UUID,
    current_user: TokenData,
    db: AsyncSession,
    required: GrantPermission = GrantPermission.READ,
):
    """Verify access to a patient-immunization record (Phase 5).

    Selects by id + tenant (cross-tenant → 404), then delegates to
    ``check_patient_access`` for the USER own-patient gate."""
    if isinstance(immunization_id, str):
        try:
            immunization_id = UUID(immunization_id)
        except ValueError:
            raise ValidationError("Invalid immunization ID format")

    result = await db.execute(
        select(PatientImmunization).where(
            PatientImmunization.id == immunization_id,
            PatientImmunization.tenant_id == current_user.tenant_id,
            PatientImmunization.deleted_at.is_(None),
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise NotFoundError("Immunization record not found")
    await check_patient_access(record.patient_id, current_user, db, required)
    return record


async def check_event_access(
    event_id: str | UUID,
    current_user: TokenData,
    db: AsyncSession,
    required: GrantPermission = GrantPermission.READ,
):
    """Verify the current user has access to the specified clinical event"""
    if isinstance(event_id, str):
        try:
            event_id = UUID(event_id)
        except ValueError:
            raise ValidationError("Invalid event ID format")

    result = await db.execute(
        select(ClinicalEvent).where(
            ClinicalEvent.id == event_id,
            ClinicalEvent.tenant_id == current_user.tenant_id,
            ClinicalEvent.deleted_at.is_(None),
        )
    )
    event = result.scalar_one_or_none()

    if not event:
        raise NotFoundError("Clinical event not found")

    await check_patient_access(event.patient_id, current_user, db, required)

    return event


async def check_allergy_access(
    allergy_id: str | UUID,
    current_user: TokenData,
    db: AsyncSession,
    required: GrantPermission = GrantPermission.READ,
):
    """Verify the current user has access to the specified allergy record"""
    if isinstance(allergy_id, str):
        try:
            allergy_id = UUID(allergy_id)
        except ValueError:
            raise ValidationError("Invalid allergy ID format")

    result = await db.execute(
        select(AllergyIntolerance).where(
            AllergyIntolerance.id == allergy_id,
            AllergyIntolerance.tenant_id == current_user.tenant_id,
            AllergyIntolerance.deleted_at.is_(None),
        )
    )
    allergy = result.scalar_one_or_none()

    if not allergy:
        raise NotFoundError("Allergy record not found")

    await check_patient_access(allergy.patient_id, current_user, db, required)

    return allergy


async def check_observation_access(
    observation_id: str | UUID,
    current_user: TokenData,
    db: AsyncSession,
    required: GrantPermission = GrantPermission.READ,
):
    """Verify the current user has access to the specified observation record"""
    if isinstance(observation_id, str):
        try:
            observation_id = UUID(observation_id)
        except ValueError:
            raise ValidationError("Invalid observation ID format")

    result = await db.execute(
        select(Observation).where(
            Observation.id == observation_id,
            Observation.tenant_id == current_user.tenant_id,
        )
    )
    observation = result.scalar_one_or_none()

    if not observation:
        raise NotFoundError("Observation record not found")

    # Prefer the maintained relational patient_id (audit B3); fall back to the
    # FHIR subject reference for rows that predate the column.
    patient_id = observation.patient_id or (
        (observation.subject or {}).get("reference", "").split("/")[-1] or None
    )
    if not patient_id:
        raise ValidationError("Observation does not have a linked patient")

    await check_patient_access(patient_id, current_user, db, required)

    return observation
