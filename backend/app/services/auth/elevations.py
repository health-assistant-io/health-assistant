"""The break-glass elevation state machine (ADR 0054 D1/D6 — Phase 12A).

One module owns the elevation lifecycle — request (reason + a subset of the
requester's held permissions + duration), approve (a second person), deny,
revoke, expiry — so callers cannot improvise a state change. Every
transition is audited (``elevation_*`` actions, fail-closed) and the
user-facing ones notify the affected parties through the notification
service.

Approval rules (D1):

- the approver is a **second person**: ``requested_by <> approved_by`` is a
  DB CHECK and the service refuses self-approval before the write;
- the approver's role matches the elevation's scope: a ``tenant`` elevation
  is approved by a second ADMIN of the same tenant, a ``system`` elevation
  by a second SYSTEM_ADMIN;
- a ``system`` elevation is requestable by SYSTEM_ADMIN only; a ``tenant``
  elevation by any holder of ``break_glass:request`` (ADMIN/MANAGER);
- the requested permissions must be a subset of the requester's held
  permissions (the closed catalog) — an elevation widens the effective
  scope of held permissions, it never grants a permission the role lacks;
- one live (pending/active) elevation per principal per scope (partial
  unique index; the service answers 409 first).

Single-admin instances have no elevation path (D6): the only eligible
approver is the requester, the service refuses, and the row stays pending —
recovery is the documented host rescue runbook (``SECURITY.md``), never a
product bypass. No self-approval, no time-delayed fallback.

The consultation half (:func:`active_elevation_decision`) is the
``authorize()`` seam: one time-boxed query per request (``status =
'active'`` and ``expires_at > now()`` evaluated in SQL — no clock skew, no
token caching), whose rows widen the effective scope of exactly the listed
permissions.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.permissions import Permission
from app.core.authz.roles import is_scoped_principal, permissions_for
from app.core.authz.scopes import AccessScope
from app.core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.security import TokenData
from app.models.access_elevation import (
    AccessElevation,
    ElevationScope,
    ElevationStatus,
)
from app.models.enums import (
    NotificationCategory,
    NotificationSeverity,
    NotificationSource,
    NotificationType,
    Role,
)
from app.models.user_model import UserModel
from app.services.audit.audit_service import log_audit_action
from app.services.notifications.notification_service import emit

logger = logging.getLogger(__name__)

#: A break-glass window is an emergency measure, never a standing grant:
#: the requested duration is capped and test-pinned.
MAX_DURATION_MINUTES: Final = 240

#: The legal transitions (constitution rule 7a: one module owns the moves).
TRANSITIONS: Final[dict[ElevationStatus, frozenset[ElevationStatus]]] = {
    ElevationStatus.PENDING: frozenset(
        {ElevationStatus.ACTIVE, ElevationStatus.DENIED, ElevationStatus.REVOKED}
    ),
    ElevationStatus.ACTIVE: frozenset({ElevationStatus.EXPIRED, ElevationStatus.REVOKED}),
    ElevationStatus.REVOKED: frozenset(),
    ElevationStatus.EXPIRED: frozenset(),
    ElevationStatus.DENIED: frozenset(),
}


@dataclass(frozen=True)
class ElevationDecision:
    """The widened reach one active elevation confers on its requester."""

    id: UUID
    tenant_id: UUID | None
    scope: ElevationScope
    permissions: frozenset[str]

    def covers(self, permission: Permission | str) -> bool:
        """Whether ``permission`` is inside the approved set."""
        return str(permission) in self.permissions

    def effective_scope(self) -> AccessScope:
        """The :class:`~app.core.authz.scopes.AccessScope` the listed
        permissions reach while the window is open — a tenant elevation
        widens to tenant reach and never further (a tenant window does not
        open system surfaces); only a system elevation reaches system."""
        return AccessScope.SYSTEM if self.scope is ElevationScope.SYSTEM else AccessScope.TENANT


def _now() -> datetime:
    return datetime.now(UTC)


def _reject_scoped_principal(actor: TokenData, action: str) -> None:
    """Break-glass is a human act: a scoped machine credential cannot move it.

    A PAT or integration principal carries its owner's role name, so without
    this wall it could open (or approve) a time-boxed widening of that role
    while the owner is not present — and break-glass explicitly sits outside
    every scoped credential's reach (ADR 0054 D8 item 5).
    """
    if is_scoped_principal(actor):
        raise AuthorizationError(
            f"A scoped credential cannot {action} a break-glass elevation; "
            "use an interactive session"
        )


def _validate_duration(duration_minutes: int) -> int:
    if not isinstance(duration_minutes, int) or isinstance(duration_minutes, bool):
        raise ValidationError("duration_minutes must be an integer number of minutes")
    if duration_minutes < 1 or duration_minutes > MAX_DURATION_MINUTES:
        raise ValidationError(f"duration_minutes must be between 1 and {MAX_DURATION_MINUTES}")
    return duration_minutes


def _validate_permissions(permissions: list[str], role: Role) -> list[str]:
    """The closed, deduplicated subset of the requester's held permissions."""
    if not permissions:
        raise ValidationError("At least one permission must be requested")
    held = permissions_for(role)
    seen: list[str] = []
    for value in permissions:
        try:
            resolved = Permission(value)
        except ValueError:
            raise ValidationError(f"Unknown permission: {value}") from None
        if resolved not in held:
            raise AuthorizationError(
                "An elevation can only widen permissions the requester already holds"
            )
        if resolved.value not in seen:
            seen.append(resolved.value)
    return sorted(seen)


def _approver_role_for(scope: ElevationScope) -> Role:
    """The role a second approver must hold for this elevation's scope."""
    return Role.SYSTEM_ADMIN if scope is ElevationScope.SYSTEM else Role.ADMIN


async def _load_for_decision(
    db: AsyncSession, elevation_id: UUID, actor: TokenData
) -> AccessElevation:
    """One elevation by id, visible to the actor (cross-tenant → hidden 404).

    Tenant elevations are visible to members of their tenant; system
    elevations (NULL tenant) only to SYSTEM_ADMIN actors. The SYSTEM_ADMIN
    operator reach extends to every row (RLS grants the same reach — the
    containment authority), while the strict per-scope approver rules bind
    in :func:`_assert_approver` regardless of visibility.
    """
    result = await db.execute(select(AccessElevation).where(AccessElevation.id == elevation_id))
    elevation = result.scalar_one_or_none()
    if elevation is None:
        raise NotFoundError("Access elevation not found")
    if actor.role == Role.SYSTEM_ADMIN.value:
        return elevation
    if elevation.scope is ElevationScope.SYSTEM:
        raise NotFoundError("Access elevation not found")
    if str(elevation.tenant_id) != str(actor.tenant_id):
        raise NotFoundError("Access elevation not found")
    return elevation


async def _audit(
    *,
    action: str,
    elevation: AccessElevation,
    actor_user_id: UUID | None,
    detail: dict | None = None,
) -> None:
    """Audit one transition (id-shaped payload — the reason is never embedded)."""
    payload = elevation.to_dict()
    if detail:
        payload.update(detail)
    payload.pop("tenant_id", None)
    await log_audit_action(
        required=True,
        tenant_id=UUID(str(elevation.tenant_id)) if elevation.tenant_id else None,
        user_id=actor_user_id,
        action=action,
        resource_type="AccessElevation",
        resource_id=elevation.id,
        new_value=payload,
    )


async def _notify(
    *,
    elevation: AccessElevation,
    user_ids: list[UUID | None],
    title: str,
    body: str,
    severity: NotificationSeverity = NotificationSeverity.INFO,
) -> None:
    """Best-effort fan-out; failures never break the flow."""
    targets = [{"kind": "USER", "id": str(user_id)} for user_id in user_ids if user_id]
    if not targets:
        return
    try:
        await emit(
            source=NotificationSource.SYSTEM,
            type=NotificationType.SYSTEM_UPDATE,
            category=NotificationCategory.SYSTEM,
            severity=severity,
            title=title,
            body=body,
            tenant_id=elevation.tenant_id,
            targets=targets,
            payload={"elevation_id": str(elevation.id), "status": elevation.status.value},
            source_ref={"elevation_id": str(elevation.id)},
        )
    except Exception:
        logger.exception("Elevation notification failed (elevation=%s)", elevation.id)


async def _eligible_approvers(db: AsyncSession, elevation: AccessElevation) -> list[UUID]:
    """The users who could approve this elevation (role + tenant, minus requester)."""
    role = _approver_role_for(ElevationScope(elevation.scope))
    stmt = select(UserModel.id).where(
        UserModel.role == role,
        UserModel.is_active.is_(True),
        UserModel.id != elevation.requested_by,
    )
    if elevation.tenant_id is not None:
        stmt = stmt.where(UserModel.tenant_id == elevation.tenant_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


async def request_elevation(
    db: AsyncSession,
    *,
    actor: TokenData,
    scope: ElevationScope | str,
    permissions: list[str],
    duration_minutes: int,
    reason: str,
    metadata: dict | None = None,
) -> AccessElevation:
    """Open a break-glass request (status ``pending``); audit + notify approvers.

    Typed errors: 403 a system-scope request by a non-SYSTEM_ADMIN (or a
    permission the requester does not hold), 400 an unknown permission /
    empty reason / out-of-range duration, 409 a live elevation for the same
    principal and scope already exists.
    """
    _reject_scoped_principal(actor, "request")
    resolved_scope = ElevationScope(scope)
    role = Role(actor.role)
    if resolved_scope is ElevationScope.SYSTEM and role is not Role.SYSTEM_ADMIN:
        raise AuthorizationError("Only SYSTEM_ADMIN can request a system-scope elevation")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("A break-glass request requires a reason")
    _validate_duration(duration_minutes)
    resolved_permissions = _validate_permissions(list(permissions), role)
    existing = await db.execute(
        select(AccessElevation.id).where(
            AccessElevation.scope == resolved_scope,
            AccessElevation.requested_by == actor.user_id,
            AccessElevation.status.in_([ElevationStatus.PENDING, ElevationStatus.ACTIVE]),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("You already have a live elevation for this scope")

    elevation = AccessElevation(
        tenant_id=None if resolved_scope is ElevationScope.SYSTEM else actor.tenant_id,
        requested_by=actor.user_id,
        scope=resolved_scope,
        status=ElevationStatus.PENDING,
        permissions=resolved_permissions,
        reason=reason.strip(),
        duration_minutes=duration_minutes,
        metadata=metadata,
    )
    db.add(elevation)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("You already have a live elevation for this scope") from exc
    await db.refresh(elevation)

    await _audit(action="elevation_request", elevation=elevation, actor_user_id=actor.user_id)
    approvers = await _eligible_approvers(db, elevation)
    await _notify(
        elevation=elevation,
        user_ids=approvers,
        title="Break-glass access requested",
        body=(
            f"A second approval is required: {role.value} requested "
            f"{resolved_scope.value}-scope elevation for "
            f"{len(resolved_permissions)} permission(s), {duration_minutes} min."
        ),
        severity=NotificationSeverity.WARNING,
    )
    return elevation


# ---------------------------------------------------------------------------
# Approve / deny / revoke
# ---------------------------------------------------------------------------


def _assert_approver(actor: TokenData, elevation: AccessElevation) -> None:
    """The two-person approval authority (role for the scope + not the requester)."""
    if str(actor.user_id) == str(elevation.requested_by):
        raise AuthorizationError("Break-glass elevations cannot be approved by the requester")
    required_role = _approver_role_for(ElevationScope(elevation.scope))
    if actor.role != required_role.value:
        raise AuthorizationError(
            f"A {required_role.value} must approve a {elevation.scope}-scope elevation"
        )


async def approve_elevation(db: AsyncSession, elevation_id: UUID | str, actor: TokenData):
    """The second person approves (pending → active) and the window opens.

    ``starts_at``/``expires_at`` are stamped at approval time (the window is
    the approver's decision, not the requester's request moment); the
    duration was validated at request time. Typed errors: 404 invisible or
    unknown row, 403 the requester themselves / a role the scope does not
    accept, 409 a row that is no longer pending.
    """
    _reject_scoped_principal(actor, "approve")
    elevation_id = UUID(str(elevation_id))
    elevation = await _load_for_decision(db, elevation_id, actor)
    _assert_approver(actor, elevation)
    opened_at = _now()
    expires_at = opened_at + timedelta(minutes=elevation.duration_minutes)
    moved = await db.execute(
        update(AccessElevation)
        .where(
            AccessElevation.id == elevation_id,
            AccessElevation.status == ElevationStatus.PENDING,
        )
        .values(
            status=ElevationStatus.ACTIVE,
            approved_by=actor.user_id,
            approved_at=opened_at,
            starts_at=opened_at,
            expires_at=expires_at,
        )
    )
    if not moved.rowcount:
        raise ConflictError("This request is no longer pending")
    await db.commit()
    elevation = await _load_for_decision(db, elevation_id, actor)
    await _audit(action="elevation_approve", elevation=elevation, actor_user_id=actor.user_id)
    await _notify(
        elevation=elevation,
        user_ids=[elevation.requested_by],
        title="Break-glass access approved",
        body=(
            f"Your {elevation.scope.value}-scope elevation is active until "
            f"{expires_at.isoformat()}. Every use is audited."
        ),
    )
    return elevation


async def deny_elevation(
    db: AsyncSession, elevation_id: UUID | str, actor: TokenData, reason: str | None = None
):
    """The second person refuses (pending → denied); the requester is notified."""
    _reject_scoped_principal(actor, "deny")
    elevation_id = UUID(str(elevation_id))
    elevation = await _load_for_decision(db, elevation_id, actor)
    _assert_approver(actor, elevation)
    moved = await db.execute(
        update(AccessElevation)
        .where(
            AccessElevation.id == elevation_id,
            AccessElevation.status == ElevationStatus.PENDING,
        )
        .values(status=ElevationStatus.DENIED)
    )
    if not moved.rowcount:
        raise ConflictError("This request is no longer pending")
    await db.commit()
    elevation = await _load_for_decision(db, elevation_id, actor)
    await _audit(
        action="elevation_deny",
        elevation=elevation,
        actor_user_id=actor.user_id,
        detail={"decision_reason_recorded": bool(reason)},
    )
    await _notify(
        elevation=elevation,
        user_ids=[elevation.requested_by],
        title="Break-glass access denied",
        body=f"Your {elevation.scope.value}-scope elevation request was denied.",
    )
    return elevation


def _assert_revoker(actor: TokenData, elevation: AccessElevation) -> None:
    """Revoke authority: the requester (giving it up) or an eligible approver.

    Revocation only ever *removes* reach, so SYSTEM_ADMIN may contain any
    tenant's window — approval strictness does not invert into revocation
    strictness.
    """
    if str(actor.user_id) == str(elevation.requested_by):
        return
    if actor.role == Role.SYSTEM_ADMIN.value:
        return
    if actor.role == _approver_role_for(ElevationScope(elevation.scope)).value:
        if elevation.scope is ElevationScope.TENANT and str(elevation.tenant_id) != str(
            actor.tenant_id
        ):
            raise NotFoundError("Access elevation not found")
        return
    raise AuthorizationError("Only the requester or an eligible approver can revoke")


async def revoke_elevation(db: AsyncSession, elevation_id: UUID | str, actor: TokenData):
    """Close a pending or active elevation early (→ ``revoked``)."""
    _reject_scoped_principal(actor, "revoke")
    elevation_id = UUID(str(elevation_id))
    elevation = await _load_for_decision(db, elevation_id, actor)
    _assert_revoker(actor, elevation)
    revoked_at = _now()
    moved = await db.execute(
        update(AccessElevation)
        .where(
            AccessElevation.id == elevation_id,
            AccessElevation.status.in_([ElevationStatus.PENDING, ElevationStatus.ACTIVE]),
        )
        .values(status=ElevationStatus.REVOKED, revoked_at=revoked_at)
    )
    if not moved.rowcount:
        raise ConflictError("This elevation is already settled")
    await db.commit()
    elevation = await _load_for_decision(db, elevation_id, actor)
    await _audit(action="elevation_revoke", elevation=elevation, actor_user_id=actor.user_id)
    await _notify(
        elevation=elevation,
        user_ids=[elevation.requested_by, elevation.approved_by],
        title="Break-glass access revoked",
        body=f"The {elevation.scope.value}-scope elevation was revoked; access has ended.",
    )
    return elevation


# ---------------------------------------------------------------------------
# Expiry (the beat sweep; read paths are predicate-safe regardless)
# ---------------------------------------------------------------------------


async def expire_due_elevations(db: AsyncSession) -> int:
    """Flip every active-but-expired elevation to ``expired``; audit + notify.

    Returns the number of rows flipped this run. The consultation predicate
    (``expires_at > now()`` in SQL) already ignores due rows, so this is
    hygiene for the ledger rather than an enforcement step; the Celery beat
    sweep (``app.workers.tasks.expire_access_elevations``) is the production
    caller. Each flip is audited (``elevation_expire``, system actor) and
    the requester and approver are notified.
    """
    due = (
        (
            await db.execute(
                select(AccessElevation).where(
                    AccessElevation.status == ElevationStatus.ACTIVE,
                    AccessElevation.expires_at.is_not(None),
                    AccessElevation.expires_at <= func.now(),
                )
            )
        )
        .scalars()
        .all()
    )
    if not due:
        return 0
    expired: list[AccessElevation] = []
    for elevation in due:
        moved = await db.execute(
            update(AccessElevation)
            .where(
                AccessElevation.id == elevation.id,
                AccessElevation.status == ElevationStatus.ACTIVE,
            )
            .values(status=ElevationStatus.EXPIRED)
        )
        if moved.rowcount:
            elevation.status = ElevationStatus.EXPIRED
            expired.append(elevation)
    await db.commit()
    for elevation in expired:
        await _audit(action="elevation_expire", elevation=elevation, actor_user_id=None)
        await _notify(
            elevation=elevation,
            user_ids=[elevation.requested_by, elevation.approved_by],
            title="Break-glass access expired",
            body="A time-boxed elevation reached its expiry; access has ended.",
        )
    return len(expired)


# ---------------------------------------------------------------------------
# authorize() consultation + listing
# ---------------------------------------------------------------------------


def _consult_filters(user_id: UUID, tenant_id: UUID | str | None, *, is_system: bool) -> list:
    """The time-boxed consultation predicates (evaluated in SQL, one query).

    ``expires_at > now()`` is the box; a system-scope elevation is consulted
    only for a SYSTEM_ADMIN actor (the system door never opens for anyone
    else), and a tenant elevation only within the tenant the request
    operates on.
    """
    clauses = [
        AccessElevation.requested_by == user_id,
        AccessElevation.status == ElevationStatus.ACTIVE,
        AccessElevation.starts_at.is_(None) | (AccessElevation.starts_at <= func.now()),
        AccessElevation.expires_at.is_not(None),
        AccessElevation.expires_at > func.now(),
    ]
    if is_system:
        clauses.append(
            or_(
                AccessElevation.scope == ElevationScope.SYSTEM,
                and_(
                    AccessElevation.scope == ElevationScope.TENANT,
                    AccessElevation.tenant_id == tenant_id,
                ),
            )
        )
    else:
        clauses.append(
            and_(
                AccessElevation.scope == ElevationScope.TENANT,
                AccessElevation.tenant_id == tenant_id,
            )
        )
    return clauses


async def active_elevation_decision(
    db: AsyncSession,
    *,
    user_id: UUID | str,
    tenant_id: UUID | str | None,
    role: str,
) -> ElevationDecision | None:
    """The caller's active elevation, or ``None`` (the ``authorize()`` seam).

    One time-boxed query per request — the caller passes the session, so no
    token carries elevation state and expiry is evaluated by the database at
    query time. A system-scope elevation answers only for a SYSTEM_ADMIN
    actor; a tenant elevation only inside its own tenant.
    """
    is_system = role == Role.SYSTEM_ADMIN.value
    if tenant_id is None and not is_system:
        return None
    result = await db.execute(
        select(AccessElevation)
        .where(*_consult_filters(UUID(str(user_id)), tenant_id, is_system=is_system))
        .order_by(AccessElevation.expires_at.desc())
        .limit(1)
    )
    elevation = result.scalar_one_or_none()
    if elevation is None:
        return None
    return ElevationDecision(
        id=elevation.id,
        tenant_id=UUID(str(elevation.tenant_id)) if elevation.tenant_id else None,
        scope=ElevationScope(elevation.scope),
        permissions=frozenset(elevation.permissions or []),
    )


async def elevations_for_requester(
    db: AsyncSession, user_id: UUID, tenant_id: UUID, *, is_system: bool
) -> list[AccessElevation]:
    """The caller's own elevations, newest first (``GET /me/elevations``)."""
    stmt = select(AccessElevation).where(AccessElevation.requested_by == user_id)
    if is_system:
        stmt = stmt.where(
            or_(
                AccessElevation.scope == ElevationScope.SYSTEM,
                AccessElevation.tenant_id == tenant_id,
            )
        )
    else:
        stmt = stmt.where(AccessElevation.tenant_id == tenant_id)
    result = await db.execute(stmt.order_by(AccessElevation.requested_at.desc()).limit(100))
    return list(result.scalars().all())


async def list_elevations(
    db: AsyncSession,
    *,
    tenant_id: UUID | None,
    scope: ElevationScope | None = None,
    status: ElevationStatus | None = None,
) -> tuple[list[AccessElevation], int]:
    """The admin ledger for one tenant filter (``None`` = system-wide view).

    ``tenant_id`` narrows to one tenant's rows; ``scope='system'`` selects
    the NULL-tenant instance-level rows and ignores ``tenant_id``; with
    neither filter every row answers (the SYSTEM_ADMIN ``all`` view).
    """
    clauses = []
    if scope is ElevationScope.SYSTEM:
        pass
    elif tenant_id is not None:
        clauses.append(AccessElevation.tenant_id == tenant_id)
    if scope is not None:
        clauses.append(AccessElevation.scope == scope)
    elif tenant_id is None:
        clauses.append(
            or_(
                AccessElevation.tenant_id.is_not(None),
                AccessElevation.scope == ElevationScope.SYSTEM,
            )
        )
    if status is not None:
        clauses.append(AccessElevation.status == status)
    total_result = await db.execute(
        select(func.count()).select_from(AccessElevation).where(*clauses)
    )
    total = total_result.scalar_one()
    result = await db.execute(
        select(AccessElevation)
        .where(*clauses)
        .order_by(AccessElevation.requested_at.desc())
        .limit(250)
    )
    return list(result.scalars().all()), total
