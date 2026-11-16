"""The patient-access grant state machine (ADR 0054 Phase 2 / program 7A).

One module owns the grant lifecycle — creation rules per source, the legal
status transitions, concurrency (atomic status-guarded updates + the partial
unique indexes), expiry and revoke — so callers (the API layer, future
workers) cannot improvise a state change. Every transition is audited via
:func:`log_audit_action` and the user-facing ones notify the affected party
through the notification service.

Sources (provenance of the decision):

- ``admin``      — granted from the admin surface; active immediately.
- ``care_team``  — granted from the patient's Access tab by staff; active
  immediately.
- ``patient``    — self-service share by the patient's owner; **pending**
  until the grantee staff accepts (or declines). Gated by the tenant
  ``allow_patient_share`` setting.
- ``guardian``   — admin-granted access to a patient that has **no** owner
  account (the guardian logs in with their own user). Active immediately.

``break_glass`` is deliberately not a source (ADR 0054 D1) — elevations live
in ``access_elevations``.

Tenant settings (``tenants.settings`` JSONB, read per request — never
cached into tokens):

- ``manager_scope``       — ``tenant`` (default) | ``assigned``. ``assigned``
  narrows a MANAGER's patient reach to own + grants.
- ``allow_patient_share`` — default on. Off refuses ``source=patient``.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.security import TokenData
from app.models.doctor_model import DoctorModel
from app.models.enums import (
    NotificationCategory,
    NotificationSeverity,
    NotificationSource,
    NotificationType,
    Role,
)
from app.models.fhir.patient import Patient
from app.models.patient_access_grant import (
    GrantPermission,
    GrantSource,
    GrantStatus,
    PatientAccessGrant,
)
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.audit.audit_service import log_audit_action
from app.services.auth import security_policy
from app.services.notifications.notification_service import emit

logger = logging.getLogger(__name__)


def patient_display_name(name: object) -> str | None:
    """The patient's name as a display string (``Given Family``), or ``None``."""
    if isinstance(name, list):
        name = name[0] if name else None
    if isinstance(name, dict):
        given = " ".join(name.get("given") or [])
        family = name.get("family") or ""
        text = name.get("text")
        display = f"{given} {family}".strip() or (str(text) if text else "")
        return display or None
    if isinstance(name, str):
        return name or None
    return None


#: A read grant never satisfies a write check; ranks make that a comparison.
PERMISSION_RANK: Final[dict[GrantPermission, int]] = {
    GrantPermission.READ: 0,
    GrantPermission.WRITE: 1,
    GrantPermission.ADMIN: 2,
}

#: The legal transitions (constitution rule 7a: one module owns the moves).
TRANSITIONS: Final[dict[GrantStatus, frozenset[GrantStatus]]] = {
    GrantStatus.PENDING: frozenset({GrantStatus.ACTIVE, GrantStatus.DECLINED, GrantStatus.REVOKED}),
    GrantStatus.ACTIVE: frozenset({GrantStatus.REVOKED, GrantStatus.EXPIRED}),
    GrantStatus.REVOKED: frozenset(),
    GrantStatus.EXPIRED: frozenset(),
    GrantStatus.DECLINED: frozenset(),
}

DEFAULT_MANAGER_SCOPE: Final = security_policy.DEFAULT_MANAGER_SCOPE
MANAGER_SCOPES: Final = security_policy.MANAGER_SCOPES

#: Staff roles a patient-initiated share may target (the accepting party).
STAFF_ROLES: Final = (Role.ADMIN, Role.MANAGER)


# ---------------------------------------------------------------------------
# Tenant access settings (tenants.settings JSONB — normalize on read)
# ---------------------------------------------------------------------------
# The knob normalizers live in ``app.services.auth.security_policy`` (the
# tenant security policy module, ADR 0054 Phase 3) and are re-exported here
# under the Phase-2 names — one implementation, two consumers.


def normalize_manager_scope(value: object) -> str:
    """Normalize the ``manager_scope`` knob (anything else → ``tenant``)."""
    return security_policy.normalize_manager_scope(value)


def normalize_allow_patient_share(value: object) -> bool:
    """Normalize ``allow_patient_share`` (default on; only falsey turns it off)."""
    return security_policy.normalize_patient_sharing(value)


@dataclass(frozen=True)
class TenantAccessSettings:
    """The two Phase-2 knobs, normalized (unknown values → defaults)."""

    manager_scope: str = DEFAULT_MANAGER_SCOPE
    allow_patient_share: bool = True


async def tenant_access_settings(db: AsyncSession, tenant_id: UUID | str) -> TenantAccessSettings:
    """Read the two access knobs for one tenant, normalized, per request."""
    result = await db.execute(select(TenantModel.settings).where(TenantModel.id == tenant_id))
    raw = result.scalar_one_or_none() or {}
    if not isinstance(raw, dict):
        raw = {}
    return TenantAccessSettings(
        manager_scope=normalize_manager_scope(raw.get("manager_scope")),
        allow_patient_share=normalize_allow_patient_share(raw.get("allow_patient_share")),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_expiry(expires_at: datetime | None) -> None:
    if expires_at is not None and expires_at <= _now():
        raise ValidationError("expires_at must be in the future")


def _actor_is_staff(actor: TokenData) -> bool:
    return actor.role in (Role.ADMIN.value, Role.MANAGER.value, Role.SYSTEM_ADMIN.value)


async def _get_grant(db: AsyncSession, tenant_id: UUID, grant_id: UUID) -> PatientAccessGrant:
    """One grant by id + tenant (cross-tenant → hidden 404)."""
    result = await db.execute(
        select(PatientAccessGrant).where(
            PatientAccessGrant.id == grant_id,
            PatientAccessGrant.tenant_id == tenant_id,
        )
    )
    grant = result.scalar_one_or_none()
    if grant is None:
        raise NotFoundError("Access grant not found")
    return grant


async def _grantee_user_id(db: AsyncSession, grant: PatientAccessGrant) -> UUID | None:
    """The login the grant confers access to (direct, or via the doctor row)."""
    if grant.grantee_user_id is not None:
        return grant.grantee_user_id
    if grant.grantee_doctor_id is None:
        return None
    result = await db.execute(
        select(DoctorModel.user_id).where(DoctorModel.id == grant.grantee_doctor_id)
    )
    return result.scalar_one_or_none()


async def _patient_label(db: AsyncSession, patient_id: UUID) -> str:
    result = await db.execute(select(Patient.name).where(Patient.id == patient_id))
    name = result.scalar_one_or_none()
    return patient_display_name(name) or "a patient record"


async def _notify(
    *,
    tenant_id: UUID,
    patient_id: UUID,
    user_ids: list[UUID | None],
    title: str,
    body: str,
    grant: PatientAccessGrant,
) -> None:
    """Best-effort fan-out to the given users; failures never break the flow."""
    targets = [{"kind": "USER", "id": str(user_id)} for user_id in user_ids if user_id is not None]
    if not targets:
        return
    try:
        await emit(
            source=NotificationSource.SYSTEM,
            type=NotificationType.SYSTEM_UPDATE,
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.INFO,
            title=title,
            body=body,
            tenant_id=tenant_id,
            patient_id=patient_id,
            targets=targets,
            payload={"grant_id": str(grant.id), "grant_status": grant.status.value},
            source_ref={"grant_id": str(grant.id)},
        )
    except Exception:
        logger.exception("Grant notification failed (grant=%s)", grant.id)


async def _audit(
    *,
    action: str,
    grant: PatientAccessGrant,
    actor_user_id: UUID | None,
    detail: dict | None = None,
) -> None:
    """Audit one transition (id-shaped payload — no PHI beyond the patient id)."""
    payload = grant.to_dict()
    if detail:
        payload.update(detail)
    payload.pop("tenant_id", None)
    await log_audit_action(
        required=True,
        tenant_id=UUID(str(grant.tenant_id)),
        user_id=actor_user_id,
        action=action,
        resource_type="PatientAccessGrant",
        resource_id=grant.id,
        new_value=payload,
    )


# ---------------------------------------------------------------------------
# Creation (per-source validation)
# ---------------------------------------------------------------------------


async def create_grant(
    db: AsyncSession,
    *,
    patient: Patient,
    actor: TokenData,
    permission: GrantPermission | str,
    grantee_user_id: UUID | str | None = None,
    grantee_doctor_id: UUID | str | None = None,
    source: GrantSource | str | None = None,
    expires_at: datetime | None = None,
    reason: str | None = None,
) -> PatientAccessGrant:
    """Create one grant under the per-source rules; audit + notify.

    Typed errors: 404 unknown grantee in this tenant, 400 a doctor grantee
    without a linked user / both-or-neither grantee given, 403 a source the
    actor may not use (or patient-share disabled, or a guardian grant on an
    owned patient), 409 a live/pending grant for the same identity exists.
    """
    permission = GrantPermission(permission)
    if source is None:
        source = GrantSource.PATIENT if not _actor_is_staff(actor) else GrantSource.CARE_TEAM
    source = GrantSource(source)
    _validate_expiry(expires_at)

    grantee_user_id = UUID(str(grantee_user_id)) if grantee_user_id else None
    grantee_doctor_id = UUID(str(grantee_doctor_id)) if grantee_doctor_id else None
    if (grantee_user_id is None) == (grantee_doctor_id is None):
        raise ValidationError("Exactly one of grantee_user_id / grantee_doctor_id is required")

    grantee_user: UserModel | None = None
    resolved_user: UserModel | None = None
    doctor: DoctorModel | None = None
    if grantee_user_id is not None:
        result = await db.execute(
            select(UserModel).where(
                UserModel.id == grantee_user_id,
                UserModel.tenant_id == actor.tenant_id,
                UserModel.is_active.is_(True),
            )
        )
        grantee_user = result.scalar_one_or_none()
        if grantee_user is None:
            raise NotFoundError("Grantee user not found in this tenant")
        resolved_user = grantee_user
    else:
        result = await db.execute(
            select(DoctorModel).where(
                DoctorModel.id == grantee_doctor_id,
                DoctorModel.tenant_id.is_(None) | (DoctorModel.tenant_id == actor.tenant_id),
            )
        )
        doctor = result.scalar_one_or_none()
        if doctor is None:
            raise NotFoundError("Grantee doctor not found in this tenant")
        if doctor.user_id is None:
            raise ValidationError(
                "This doctor entry has no linked user account and cannot hold access"
            )
        linked = (
            await db.execute(
                select(UserModel).where(
                    UserModel.id == doctor.user_id,
                    UserModel.tenant_id == actor.tenant_id,
                    UserModel.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if linked is None:
            raise NotFoundError("The doctor's linked user is not an active member of this tenant")
        resolved_user = linked

    actor_role = Role(actor.role)
    if source in (GrantSource.ADMIN, GrantSource.CARE_TEAM):
        if not _actor_is_staff(actor):
            raise AuthorizationError("Only staff can grant care-team access directly")
        if resolved_user is not None and resolved_user.role not in STAFF_ROLES:
            raise ValidationError(
                "Only staff (ADMIN/MANAGER) can hold a care-team grant; "
                "use the guardian source for an account without a staff role"
            )
    elif source is GrantSource.PATIENT:
        if (
            actor_role != Role.USER
            or not patient.user_id
            or str(patient.user_id) != str(actor.user_id)
        ):
            raise AuthorizationError("Only the patient's own account can share this record")
        settings = await tenant_access_settings(db, actor.tenant_id)
        if not settings.allow_patient_share:
            raise AuthorizationError("Patient-initiated sharing is disabled for this tenant")
        if resolved_user is not None and resolved_user.role not in STAFF_ROLES:
            raise ValidationError("Records can only be shared with staff members")
    elif source is GrantSource.GUARDIAN:
        if actor_role not in (Role.ADMIN, Role.SYSTEM_ADMIN):
            raise AuthorizationError("Only an administrator can grant guardian access")
        if patient.user_id:
            raise ValidationError(
                "This patient already has an owner account; use a care-team grant instead"
            )

    resolved_user_id = grantee_user_id if grantee_user_id is not None else doctor.user_id
    await _reject_duplicate_grantee(db, patient.id, actor.tenant_id, resolved_user_id)

    status = GrantStatus.PENDING if source is GrantSource.PATIENT else GrantStatus.ACTIVE
    grant = PatientAccessGrant(
        tenant_id=actor.tenant_id,
        patient_id=patient.id,
        grantee_user_id=None if doctor is not None else grantee_user_id,
        grantee_doctor_id=grantee_doctor_id,
        permission=permission,
        source=source,
        status=status,
        granted_by=actor.user_id,
        accepted_at=_now() if status is GrantStatus.ACTIVE else None,
        expires_at=expires_at,
        reason=reason,
    )
    db.add(grant)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "This user already holds a grant on this patient (directly or via a doctor entry)"
        ) from exc

    label = await _patient_label(db, patient.id)
    await _audit(action="grant_create", grant=grant, actor_user_id=actor.user_id)
    if status is GrantStatus.PENDING:
        await _notify(
            tenant_id=actor.tenant_id,
            patient_id=patient.id,
            user_ids=[resolved_user_id],
            title="A patient record was shared with you",
            body=(
                f"{label} invited you to their care team. Accept or decline under Access requests."
            ),
            grant=grant,
        )
    else:
        await _notify(
            tenant_id=actor.tenant_id,
            patient_id=patient.id,
            user_ids=[resolved_user_id],
            title="You were granted access to a patient record",
            body=(
                f"Access to {label} was granted to you"
                + (f" until {expires_at.date().isoformat()}" if expires_at else "")
                + "."
            ),
            grant=grant,
        )
    return grant


async def _reject_duplicate_grantee(
    db: AsyncSession, patient_id: UUID, tenant_id: UUID, grantee_user_id: UUID
) -> None:
    """Refuse a second live/pending grant for the same human identity.

    A doctor grantee resolves to its linked user, so granting to
    ``grantee_doctor.user_id == grantee_user_id`` of an existing row would
    hand the same login two rows for one patient.
    """
    result = await db.execute(
        select(PatientAccessGrant).where(
            PatientAccessGrant.tenant_id == tenant_id,
            PatientAccessGrant.patient_id == patient_id,
            PatientAccessGrant.status.in_([GrantStatus.ACTIVE, GrantStatus.PENDING]),
        )
    )
    for existing in result.scalars().all():
        existing_user = await _grantee_user_id(db, existing)
        if existing_user is not None and str(existing_user) == str(grantee_user_id):
            raise ConflictError(
                "This user already holds a grant on this patient (directly or via a doctor entry)"
            )


# ---------------------------------------------------------------------------
# Transitions (atomic, status-guarded — concurrent movers lose cleanly)
# ---------------------------------------------------------------------------


async def accept_grant(db: AsyncSession, grant_id: UUID | str, actor: TokenData):
    """The grantee staff accepts a pending share (pending → active).

    The share is re-validated at accept time: the invitee must still be an
    active staff member (403 after a demotion) and the tenant's
    ``allow_patient_share`` must still be on (409 after a policy flip) — a
    share requested under yesterday's rules must not sail through under
    today's. A duplicate-active race (the partial unique index) answers 409.
    """
    grant_id = UUID(str(grant_id))
    grant = await _get_grant(db, actor.tenant_id, grant_id)
    grantee_user_id = await _grantee_user_id(db, grant)
    if grantee_user_id is None or str(grantee_user_id) != str(actor.user_id):
        raise AuthorizationError("Only the granted staff member can accept this request")
    if grant.source is GrantSource.PATIENT:
        settings = await tenant_access_settings(db, actor.tenant_id)
        if not settings.allow_patient_share:
            raise ConflictError("Patient-initiated sharing is disabled for this tenant")
        grantee = (
            await db.execute(
                select(UserModel).where(
                    UserModel.id == grantee_user_id,
                    UserModel.tenant_id == actor.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if grantee is None or not grantee.is_active or grantee.role not in STAFF_ROLES:
            raise AuthorizationError(
                "Only staff (ADMIN/MANAGER) can hold a patient share; "
                "this request is no longer valid"
            )
    accepted_at = _now()
    try:
        moved = await db.execute(
            update(PatientAccessGrant)
            .where(
                PatientAccessGrant.id == grant_id,
                PatientAccessGrant.tenant_id == actor.tenant_id,
                PatientAccessGrant.status == GrantStatus.PENDING,
            )
            .values(status=GrantStatus.ACTIVE, accepted_at=accepted_at)
        )
        if not moved.rowcount:
            raise ConflictError("This request is no longer pending")
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("This user already holds an active grant on this patient") from exc
    grant = await _get_grant(db, actor.tenant_id, grant_id)
    label = await _patient_label(db, grant.patient_id)
    await _audit(action="grant_accept", grant=grant, actor_user_id=actor.user_id)
    await _notify(
        tenant_id=actor.tenant_id,
        patient_id=grant.patient_id,
        user_ids=[grant.granted_by],
        title="Your share invitation was accepted",
        body=f"{label}: the invited staff member accepted access.",
        grant=grant,
    )
    return grant


async def decline_grant(
    db: AsyncSession, grant_id: UUID | str, actor: TokenData, reason: str | None = None
):
    """The grantee staff declines a pending share (pending → declined)."""
    grant_id = UUID(str(grant_id))
    grant = await _get_grant(db, actor.tenant_id, grant_id)
    grantee_user_id = await _grantee_user_id(db, grant)
    if grantee_user_id is None or str(grantee_user_id) != str(actor.user_id):
        raise AuthorizationError("Only the granted staff member can decline this request")
    moved = await db.execute(
        update(PatientAccessGrant)
        .where(
            PatientAccessGrant.id == grant_id,
            PatientAccessGrant.tenant_id == actor.tenant_id,
            PatientAccessGrant.status == GrantStatus.PENDING,
        )
        .values(status=GrantStatus.DECLINED, reason=reason if reason else grant.reason)
    )
    if not moved.rowcount:
        raise ConflictError("This request is no longer pending")
    await db.commit()
    grant = await _get_grant(db, actor.tenant_id, grant_id)
    label = await _patient_label(db, grant.patient_id)
    await _audit(action="grant_decline", grant=grant, actor_user_id=actor.user_id)
    await _notify(
        tenant_id=actor.tenant_id,
        patient_id=grant.patient_id,
        user_ids=[grant.granted_by],
        title="Your share invitation was declined",
        body=f"{label}: the invited staff member declined access.",
        grant=grant,
    )
    return grant


def _actor_may_manage(actor: TokenData, patient: Patient | None, grant: PatientAccessGrant) -> None:
    """Grantor-side authority to modify/revoke: staff of the tenant, the
    original grantor's surface, or the patient owner for their own share."""
    if actor.role == Role.SYSTEM_ADMIN.value:
        return
    if actor.role == Role.USER.value:
        if (
            grant.source is GrantSource.PATIENT
            and patient is not None
            and patient.user_id
            and str(patient.user_id) == str(actor.user_id)
        ):
            return
        raise AuthorizationError("Only the patient's own account or staff can manage this grant")


async def revoke_grant(db: AsyncSession, grant_id: UUID | str, actor: TokenData):
    """Revoke a live or pending grant (→ revoked); the grantee keeps nothing.

    Staff manage tenant-wide (the endpoint's ``patient:grant`` row-level
    check already narrowed a ``manager_scope=assigned`` manager); the
    patient owner may revoke the share they initiated.
    """
    grant_id = UUID(str(grant_id))
    grant = await _get_grant(db, actor.tenant_id, grant_id)
    patient = (
        await db.execute(select(Patient).where(Patient.id == grant.patient_id))
    ).scalar_one_or_none()
    _actor_may_manage(actor, patient, grant)
    moved = await db.execute(
        update(PatientAccessGrant)
        .where(
            PatientAccessGrant.id == grant_id,
            PatientAccessGrant.tenant_id == actor.tenant_id,
            PatientAccessGrant.status.in_([GrantStatus.ACTIVE, GrantStatus.PENDING]),
        )
        .values(status=GrantStatus.REVOKED)
    )
    if not moved.rowcount:
        raise ConflictError("This grant is already settled")
    await db.commit()
    grant = await _get_grant(db, actor.tenant_id, grant_id)
    grantee_user_id = await _grantee_user_id(db, grant)
    await _audit(action="grant_revoke", grant=grant, actor_user_id=actor.user_id)
    await _notify(
        tenant_id=actor.tenant_id,
        patient_id=grant.patient_id,
        user_ids=[grantee_user_id],
        title="Your access to a patient record was revoked",
        body="The access grant was revoked; you no longer see this record.",
        grant=grant,
    )
    return grant


#: Sentinel for "field not sent" — distinct from an explicit JSON ``null``,
#: which clears the field (S4).
_UNSET: Final = object()


async def update_grant(
    db: AsyncSession,
    grant_id: UUID | str,
    actor: TokenData,
    *,
    permission: GrantPermission | str | None = None,
    expires_at: object | None = _UNSET,
    reason: object | None = _UNSET,
):
    """Grantor-side update of permission / expiry / reason (no status move).

    Field semantics (S4): an explicit ``None`` for ``expires_at``/``reason``
    **clears** the field; the ``_UNSET`` default (i.e. the field was not
    sent — the endpoint forwards only ``model_dump(exclude_unset=True)``)
    leaves it unchanged. ``permission`` is NOT NULL and has no clear state —
    ``None`` is always a no-op. The audit row records that the free-text
    reason changed, never its content (id-shaped payloads).
    """
    grant = await _get_grant(db, actor.tenant_id, UUID(str(grant_id)))
    patient = (
        await db.execute(select(Patient).where(Patient.id == grant.patient_id))
    ).scalar_one_or_none()
    _actor_may_manage(actor, patient, grant)
    if grant.status not in (GrantStatus.PENDING, GrantStatus.ACTIVE):
        raise ConflictError("Only a pending or active grant can be updated")
    changes: dict = {}
    if permission is not None:
        resolved = GrantPermission(permission)
        grant.permission = resolved
        changes["permission"] = resolved.value
    if expires_at is _UNSET:
        pass
    elif expires_at is None:
        if grant.expires_at is not None:
            grant.expires_at = None
            changes["expires_at"] = None
    else:
        _validate_expiry(expires_at)
        grant.expires_at = expires_at
        changes["expires_at"] = expires_at.isoformat()
    if reason is _UNSET:
        pass
    elif reason is None:
        if grant.reason is not None:
            grant.reason = None
            changes["reason"] = None
    else:
        grant.reason = reason
        changes["reason_updated"] = True
    await db.commit()
    await db.refresh(grant)
    await _audit(action="grant_update", grant=grant, actor_user_id=actor.user_id, detail=changes)
    return grant


async def expire_due_grants(db: AsyncSession) -> int:
    """Flip every active-but-expired grant to ``expired``; audit + notify.

    Returns the number of grants flipped this run. Read paths ignore expired
    grants by predicate, so this is hygiene for the ledger rather than an
    enforcement step; the Celery beat sweep
    (``app.workers.tasks.expire_patient_access_grants``) is the production
    caller. Each flip is audited (``grant_expire``, system actor) and the
    grantee and grantor are notified.
    """
    due = (
        (
            await db.execute(
                select(PatientAccessGrant).where(
                    PatientAccessGrant.status == GrantStatus.ACTIVE,
                    PatientAccessGrant.expires_at.is_not(None),
                    PatientAccessGrant.expires_at <= _now(),
                )
            )
        )
        .scalars()
        .all()
    )
    if not due:
        return 0
    expired: list[PatientAccessGrant] = []
    for grant in due:
        moved = await db.execute(
            update(PatientAccessGrant)
            .where(
                PatientAccessGrant.id == grant.id,
                PatientAccessGrant.status == GrantStatus.ACTIVE,
            )
            .values(status=GrantStatus.EXPIRED)
        )
        if moved.rowcount:
            grant.status = GrantStatus.EXPIRED
            expired.append(grant)
    await db.commit()
    for grant in expired:
        await _audit(action="grant_expire", grant=grant, actor_user_id=None)
        grantee = await _grantee_user_id(db, grant)
        await _notify(
            tenant_id=UUID(str(grant.tenant_id)),
            patient_id=grant.patient_id,
            user_ids=[grantee, grant.granted_by],
            title="Access to a patient record expired",
            body="A time-limited access grant reached its expiry; access has ended.",
            grant=grant,
        )
    return len(expired)


# ---------------------------------------------------------------------------
# Listing / predicates
# ---------------------------------------------------------------------------


def grantee_clause(user_id: UUID):
    """``granted to this login`` — direct user grantee, or a doctor grantee
    whose row is linked to the caller's user id."""
    from sqlalchemy import or_

    doctor_ids = select(DoctorModel.id).where(DoctorModel.user_id == user_id)
    return or_(
        PatientAccessGrant.grantee_user_id == user_id,
        PatientAccessGrant.grantee_doctor_id.in_(doctor_ids),
    )


def live_grant_filters(tenant_id: UUID, user_id: UUID | None = None) -> list:
    """The ``active AND not expired`` predicates (tenant-scoped, optional grantee)."""
    from sqlalchemy import or_

    clauses = [
        PatientAccessGrant.tenant_id == tenant_id,
        PatientAccessGrant.status == GrantStatus.ACTIVE,
        or_(
            PatientAccessGrant.expires_at.is_(None),
            PatientAccessGrant.expires_at > _now(),
        ),
    ]
    if user_id is not None:
        clauses.append(grantee_clause(user_id))
    return clauses


async def grants_for_grantee(
    db: AsyncSession, tenant_id: UUID, user_id: UUID, status: GrantStatus
) -> list[PatientAccessGrant]:
    """Every grant of one status where the caller is the grantee (incl. via a doctor row)."""
    result = await db.execute(
        select(PatientAccessGrant)
        .where(
            PatientAccessGrant.tenant_id == tenant_id,
            PatientAccessGrant.status == status,
            grantee_clause(user_id),
        )
        .order_by(PatientAccessGrant.granted_at.desc())
    )
    return list(result.scalars().all())


async def list_patient_grants(
    db: AsyncSession, tenant_id: UUID, patient_id: UUID
) -> list[PatientAccessGrant]:
    """The patient's grant ledger, newest first (the Access tab's list)."""
    result = await db.execute(
        select(PatientAccessGrant)
        .where(
            PatientAccessGrant.tenant_id == tenant_id,
            PatientAccessGrant.patient_id == patient_id,
        )
        .order_by(PatientAccessGrant.granted_at.desc())
    )
    return list(result.scalars().all())
