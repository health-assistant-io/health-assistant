"""Scoped integration & extension principals — server side (ADR 0054 D8).

This module replaces owner-role impersonation for machine work. An
``integration_principals`` row is a first-class service identity:

- :func:`resolve_integration_actor` resolves the principal bound to a
  ``UserIntegration`` (provisioning one, bounded by the extension's declared
  capabilities, on first use) and returns a ``TokenData`` that carries the
  principal's **own** scopes — never the owner's whole role. The owner stays
  the accountable human (``user_id`` for audit + the permission ceiling).
- Every use re-intersects the stored scope set with the owner's **live** role
  and the extension manifest's derived capability scopes, so a demotion, a
  manifest change or a consent revocation narrows the principal immediately.
  An empty intersection refuses the call.
- :func:`resolve_principal` resolves an opaque ``ha_int_<prefix>_<secret>``
  bearer (prefix lookup + constant-time sha256), plants only the tenant GUC
  (never ``app.system``) and returns the same principal shape.
- Lifecycle: create/rotate/revoke are transactional and audited; rotation
  revokes the predecessor in the same transaction; revoked rows stay as the
  accountability ledger. The plaintext is returned once and never stored,
  logged or echoed.

Least privilege is the whole point: an integration principal can never widen
past its owner, never widen past the extension's declaration, never reaches
the system actor and never mints another credential.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from nx_auth import opaque
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenant_context
from app.core.authz.permissions import Permission
from app.core.authz.roles import permissions_for
from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.extensions import capabilities
from app.models.api_token import PatientScope
from app.models.enums import Role
from app.models.fhir.patient import Patient
from app.models.integration_principal import (
    IntegrationPrincipal,
    IntegrationPrincipalKind,
    IntegrationPrincipalStatus,
)
from app.models.user_integration import UserIntegration
from app.models.user_model import UserModel
from app.schemas.user import TokenData
from app.services.audit.audit_service import OUTCOME_DENIED, log_audit_action
from app.services.auth.access import accessible_patient_ids

logger = logging.getLogger(__name__)

INTEGRATION_PRINCIPAL_PREFIX = "ha_int_"
INTEGRATION_PRINCIPAL_BRAND = "ha_int"
USE_AUDIT_INTERVAL_SECONDS = 60

UNSET: object = object()


def hash_secret(value: str) -> str:
    """The sha256 lookup key of a bearer value (plaintext never stored)."""
    parsed = opaque.parse(value, brand=INTEGRATION_PRINCIPAL_BRAND)
    return parsed[1] if parsed is not None else ""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def generate_secret() -> tuple[str, str, str]:
    """Mint one opaque principal secret: ``(plaintext, prefix, sha256)``."""
    return opaque.mint(brand=INTEGRATION_PRINCIPAL_BRAND)


def secret_prefix_of(value: str | None) -> str | None:
    """The lookup segment of a presented value, or ``None`` when malformed."""
    if not value:
        return None
    parsed = opaque.parse(value, brand=INTEGRATION_PRINCIPAL_BRAND)
    return parsed[0] if parsed is not None else None


def _owner_principal(owner: UserModel) -> TokenData:
    return TokenData(
        user_id=owner.id,
        tenant_id=owner.tenant_id,
        role=Role(owner.role).value,
        sub=str(owner.id),
        email=owner.email,
    )


def derive_permissions(domain: str | None) -> frozenset[Permission]:
    """The permission ceiling an extension's manifest declares for this host."""
    if not domain:
        return frozenset()
    return capabilities.grant_for_domain(domain).permissions


def select_integration(integration_id: UUID | str, *, tenant_id: UUID):
    """A tenant-scoped ``user_integrations`` select (the handler's 404 gate)."""
    return select(UserIntegration).where(
        UserIntegration.id == UUID(str(integration_id)),
        UserIntegration.tenant_id == tenant_id,
    )


def assignable_permissions(current_user: TokenData, domain: str | None) -> frozenset[Permission]:
    """The permissions a caller may assign: their live role ∩ the manifest."""
    held = permissions_for(Role(current_user.role))
    if not domain:
        return held
    return held & derive_permissions(domain)


def _live_scopes(row: IntegrationPrincipal, owner: UserModel, domain: str | None) -> list[str]:
    """Stored scopes ∩ owner's live role ∩ the manifest declaration."""
    held = {permission.value for permission in permissions_for(Role(owner.role))}
    declared = {permission.value for permission in derive_permissions(domain)} if domain else None
    effective: list[Permission] = []
    for value in row.scopes or []:
        try:
            permission = Permission(value)
        except ValueError:
            continue
        if permission.value not in held:
            continue
        if declared is not None and permission.value not in declared:
            continue
        effective.append(permission)
    return [permission.value for permission in Permission if permission in effective]


def _audit_payload(row: IntegrationPrincipal) -> dict[str, Any]:
    """The safe audit projection — never the secret or its hash."""
    return {
        "name": row.name,
        "kind": row.kind,
        "integration_id": str(row.integration_id) if row.integration_id else None,
        "owner_user_id": str(row.owner_user_id),
        "scopes": list(row.scopes or []),
        "patient_scope": row.patient_scope,
        "patient_ids": [str(value) for value in (row.patient_ids or [])],
        "status": row.status,
        "secret_prefix": row.secret_prefix,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


async def _audit(
    db: AsyncSession,
    action: str,
    row: IntegrationPrincipal,
    *,
    actor_id: UUID | None,
    prior: dict | None = None,
) -> None:
    await log_audit_action(
        tenant_id=row.tenant_id,
        user_id=actor_id,
        action=action,
        resource_type="integration_principal",
        resource_id=row.id,
        old_value=prior,  # gate-allow: *, audit diff
        new_value=_audit_payload(row),
        required=True,
        session=db,
    )


def _resolve_expiry(expires_at: datetime | None) -> datetime | None:
    if expires_at is None:
        return None
    if _as_utc(expires_at) <= _utcnow():
        raise ValidationError("expires_at must be in the future")
    return _as_utc(expires_at)


def _normalize_scopes(
    owner: UserModel, values: list[str] | None, *, ceiling: frozenset[Permission] | None
) -> list[str]:
    """Validate ``values`` as a non-empty subset of the owner and the ceiling.

    ``ceiling=None`` means "not bound to an extension" (owner subset only); an
    empty frozenset is a real ceiling that permits nothing.
    """
    held = permissions_for(Role(owner.role))
    normalized: list[Permission] = []
    for value in values or []:
        try:
            permission = Permission(value)
        except ValueError:
            raise ValidationError(f"Unknown permission {value!r}") from None
        if permission not in held:
            raise ValidationError(f"Permission {permission.value!r} is not held by the owner")
        if ceiling is not None and permission not in ceiling:
            raise ValidationError(
                f"Permission {permission.value!r} is not declared by the extension"
            )
        if permission not in normalized:
            normalized.append(permission)
    if not normalized:
        raise ValidationError("At least one permission is required")
    return [permission.value for permission in Permission if permission in normalized]


async def _validate_patient_ids(
    db: AsyncSession, owner: UserModel, patient_scope: PatientScope, patient_ids: list
) -> list[str]:
    """Validate the explicit allowlist against the tenant and owner's reach."""
    if patient_scope is not PatientScope.GRANTED:
        if patient_ids:
            raise ValidationError("patient_ids is only allowed with patient_scope='granted'")
        return []
    normalized: list[str] = []
    for value in patient_ids or []:
        try:
            patient_id = UUID(str(value))
        except ValueError:
            raise ValidationError("Invalid patient id") from None
        if str(patient_id) not in normalized:
            normalized.append(str(patient_id))
    if not normalized:
        raise ValidationError("patient_scope='granted' requires at least one patient id")
    result = await db.execute(
        select(Patient.id).where(
            Patient.tenant_id == owner.tenant_id,
            Patient.id.in_([UUID(value) for value in normalized]),
        )
    )
    existing = {str(row[0]) for row in result.all()}
    missing = [value for value in normalized if value not in existing]
    if missing:
        raise ValidationError("Unknown patient id for this tenant")
    reachable = await accessible_patient_ids(_owner_principal(owner), db)
    if reachable is not None:
        outside = [value for value in normalized if UUID(value) not in reachable]
        if outside:
            raise ValidationError("The owner cannot reach every requested patient")
    return normalized


async def _load_owner(db: AsyncSession, owner_id: UUID) -> UserModel:
    owner = (
        await db.execute(select(UserModel).where(UserModel.id == owner_id))
    ).scalar_one_or_none()
    if owner is None or not owner.is_active:
        raise NotFoundError("Principal owner not found")
    return owner


async def _load_integration(
    db: AsyncSession, integration_id: UUID, *, tenant_id: UUID
) -> UserIntegration:
    integration = (
        await db.execute(
            select(UserIntegration).where(
                UserIntegration.id == integration_id,
                UserIntegration.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if integration is None:
        raise NotFoundError("Integration not found")
    return integration


async def active_principal_for_integration(
    db: AsyncSession, integration: UserIntegration
) -> IntegrationPrincipal | None:
    """The live principal bound to ``integration`` (``None`` = none)."""
    result = await db.execute(
        select(IntegrationPrincipal).where(
            IntegrationPrincipal.integration_id == integration.id,
            IntegrationPrincipal.tenant_id == integration.tenant_id,
            IntegrationPrincipal.status == IntegrationPrincipalStatus.ACTIVE,
        )
    )
    row = result.scalar_one_or_none()
    if row is not None and row.is_live:
        return row
    return None


async def provision_for_integration(
    db: AsyncSession,
    integration: UserIntegration,
    *,
    actor_id: UUID | None = None,
) -> IntegrationPrincipal | None:
    """Provision the integration's principal from its declared capabilities.

    Returns the existing live row when one is present, ``None`` when the
    extension declares no data-bearing capability (a chat-only connector has
    no ingest identity), and a freshly flushed row otherwise. A revoked row is
    **not** silently replaced — revocation is a decision, not a gap.
    """
    existing = await active_principal_for_integration(db, integration)
    if existing is not None:
        return existing

    revoked = (
        (
            await db.execute(
                select(IntegrationPrincipal.id).where(
                    IntegrationPrincipal.integration_id == integration.id,
                    IntegrationPrincipal.tenant_id == integration.tenant_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if revoked is not None:
        return None

    owner = await _load_owner(db, integration.user_id)
    if str(owner.tenant_id) != str(integration.tenant_id):
        return None
    ceiling = derive_permissions(integration.provider)
    scopes = _normalize_optional(
        [permission.value for permission in ceiling], owner=owner, ceiling=ceiling
    )
    if not scopes:
        return None

    row = IntegrationPrincipal(
        tenant_id=integration.tenant_id,
        owner_user_id=owner.id,
        name=_integration_principal_name(integration),
        kind=IntegrationPrincipalKind.INTEGRATION.value,
        integration_id=integration.id,
        scopes=scopes,
        patient_scope=PatientScope.GRANTED.value,
        patient_ids=[str(integration.patient_id)],
        status=IntegrationPrincipalStatus.ACTIVE.value,
        created_by=actor_id or owner.id,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        return await active_principal_for_integration(db, integration)
    await _audit(db, "integration_principal.provisioned", row, actor_id=actor_id or owner.id)
    return row


def _normalize_optional(
    values: list[str], *, owner: UserModel, ceiling: frozenset[Permission]
) -> list[str]:
    """Provisioning variant: intersect silently (no request to validate)."""
    held = {permission.value for permission in permissions_for(Role(owner.role))}
    declared = {permission.value for permission in ceiling}
    selected = {Permission(value) for value in values if value in held and value in declared}
    return [permission.value for permission in Permission if permission in selected]


def _integration_principal_name(integration: UserIntegration) -> str:
    label = integration.instance_name or integration.provider
    return f"{integration.provider}: {label}"[:200]


def principal_from_row(
    row: IntegrationPrincipal, owner: UserModel, *, domain: str | None
) -> TokenData:
    """Build the principal ``TokenData``, re-intersecting scopes live."""
    return TokenData(
        user_id=owner.id,
        tenant_id=owner.tenant_id,
        role=Role(owner.role).value,
        sub=str(owner.id),
        email=owner.email,
        token_kind="integration_principal",
        integration_principal_id=str(row.id),
        integration_name=row.name,
        integration_permissions=_live_scopes(row, owner, domain),
        integration_patient_scope=row.patient_scope,
        integration_patient_ids=[str(value) for value in (row.patient_ids or [])],
    )


async def _ensure_provisioned(
    integration: UserIntegration,
) -> IntegrationPrincipal | None:
    """Provision on first use in a **short-lived** session.

    Resolution may be called from read paths and from concurrent request
    turns; writing into the caller's long-lived transaction (and auditing
    there) would let two turns block each other on the principal row or the
    audit chain. The provisioning transaction is therefore its own and
    commits immediately; the caller re-reads the row afterwards.
    """
    from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal

    if not DATABASE_AVAILABLE:
        return None
    async with AsyncSessionLocal() as session:
        existing = await active_principal_for_integration(session, integration)
        if existing is not None:
            return existing
        row = await provision_for_integration(session, integration)
        await session.commit()
        if row is not None:
            await session.refresh(row)
        return row


async def resolve_actor(db: AsyncSession, integration: UserIntegration) -> TokenData:
    """Resolve (or provision) the integration's principal as a ``TokenData``.

    The principal is looked up in the caller's session; when it does not
    exist yet it is provisioned in a short-lived committed transaction
    (:func:`_ensure_provisioned`) so first use cannot deadlock a concurrent
    turn. Use bookkeeping is written outside the caller's transaction too.

    Raises:
        AuthorizationError: no live principal (revoked, no declared scopes,
            owner/tenant mismatch, or the owner lacks every declared scope).
        NotFoundError: the owner or integration is gone.
    """
    row = await active_principal_for_integration(db, integration)
    if row is None:
        row = await _ensure_provisioned(integration)
    if row is None:
        raise AuthorizationError(
            f"Integration {integration.id} has no active principal — connect a "
            "principal or restore the revoked one; owner impersonation is not "
            "available"
        )
    if str(row.tenant_id) != str(integration.tenant_id):
        raise AuthorizationError("Integration principal belongs to another tenant")
    owner = await _load_owner(db, row.owner_user_id)
    if str(owner.tenant_id) != str(row.tenant_id):
        raise AuthorizationError("Integration principal owner is outside its tenant")
    principal = principal_from_row(row, owner, domain=integration.provider)
    if not principal.integration_permissions:
        raise AuthorizationError(
            f"Integration principal {row.id} holds no live scope — the owner's "
            "role, the extension declaration and the stored scopes do not intersect"
        )
    return principal


async def active_principal(db: AsyncSession, integration: UserIntegration) -> TokenData | None:
    """The integration's live principal as a ``TokenData`` — **read-only**.

    For read paths (listing chat tools) that need the acting scopes but must
    never provision or stamp use: an integration with no live principal
    answers ``None`` and the caller decides (tool exposure stays bounded by
    the route's own permission + patient-access gates).
    """
    row = await active_principal_for_integration(db, integration)
    if row is None:
        return None
    owner = await _load_owner(db, row.owner_user_id)
    principal = principal_from_row(row, owner, domain=integration.provider)
    return principal if principal.integration_permissions else None


async def record_use(db: AsyncSession, integration: UserIntegration) -> None:
    """Stamp the principal's use inside the **caller's** transaction.

    Called by write paths that own their transaction (a sync turn) — never by
    read paths or concurrent request turns, where a per-use audit write would
    contend on the audit-chain head with an open caller transaction. Bearer
    requests stamp their own use in the auth path (:func:`_record_use`).
    """
    row = await active_principal_for_integration(db, integration)
    if row is None:
        return
    now = _utcnow()
    if (
        row.last_used_at is not None
        and (now - _as_utc(row.last_used_at)).total_seconds() < USE_AUDIT_INTERVAL_SECONDS
    ):
        return
    owner = await _load_owner(db, row.owner_user_id)
    principal = principal_from_row(row, owner, domain=integration.provider)
    row.last_used_at = now
    db.add(row)
    await _record_use(principal, now, prior=None, session=db)


async def resolve_principal(value: str | None) -> TokenData | None:
    """Resolve an opaque principal bearer (pre-auth lookup in system scope).

    Invalid, expired, revoked, rotated, cross-tenant and scope-less principals
    all answer ``None``; the caller maps that to the same 401 as any failed
    bearer. The resolved principal plants only the tenant GUC.
    """
    if not value:
        return None
    prefix = secret_prefix_of(value.strip())
    if prefix is None:
        return None
    digest = hash_secret(value.strip())
    if not digest:
        return None

    from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal

    if not DATABASE_AVAILABLE:
        return None

    domain: str | None = None
    with tenant_context.system_scope("auth:integration_principal"):
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(IntegrationPrincipal).where(IntegrationPrincipal.secret_prefix == prefix)
            )
            row = result.scalar_one_or_none()
            if row is None or not opaque.verify(row.secret_hash or "", digest):
                return None
            if not row.is_live:
                return None
            if (
                IntegrationPrincipalKind(row.kind) is IntegrationPrincipalKind.INTEGRATION
                and row.integration_id is None
            ):
                return None
            owner = (
                await session.execute(select(UserModel).where(UserModel.id == row.owner_user_id))
            ).scalar_one_or_none()
            if owner is None or not owner.is_active:
                return None
            if str(owner.tenant_id) != str(row.tenant_id):
                return None
            if row.integration_id is not None:
                integration = (
                    await session.execute(
                        select(UserIntegration).where(
                            UserIntegration.id == row.integration_id,
                            UserIntegration.tenant_id == row.tenant_id,
                        )
                    )
                ).scalar_one_or_none()
                if integration is None:
                    return None
                domain = integration.provider
            principal = principal_from_row(row, owner, domain=domain)
            if not principal.integration_permissions:
                return None
            last_used_at = row.last_used_at

    tenant_context.set_principal(principal)
    await _record_use(principal, last_used_at, prior=last_used_at, session=None)
    return principal


async def _record_use(
    principal: TokenData,
    now: datetime,
    *,
    prior: datetime | None,
    session: AsyncSession | None,
) -> None:
    """Stamp ``last_used_at`` and audit the use (throttled per minute)."""
    if prior is not None and (now - _as_utc(prior)).total_seconds() < USE_AUDIT_INTERVAL_SECONDS:
        return
    tenant_id = principal.tenant_id
    owner_id = principal.user_id
    principal_id = UUID(str(principal.integration_principal_id))
    payload = {
        "principal_id": str(principal_id),
        "principal_name": principal.integration_name,
        "owner_user_id": str(owner_id) if owner_id else None,
        "scopes": list(principal.integration_permissions or []),
    }
    if session is not None:
        await log_audit_action(
            tenant_id=tenant_id,
            user_id=owner_id,
            action="integration_principal.used",
            resource_type="integration_principal",
            resource_id=principal_id,
            new_value=payload,
            required=True,
            session=session,
        )
        return
    from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal

    if not DATABASE_AVAILABLE:
        return
    async with AsyncSessionLocal() as audit_session:
        row = await audit_session.get(IntegrationPrincipal, principal_id)
        if row is not None:
            row.last_used_at = now
            audit_session.add(row)
        await log_audit_action(
            tenant_id=tenant_id,
            user_id=owner_id,
            action="integration_principal.used",
            resource_type="integration_principal",
            resource_id=principal_id,
            new_value=payload,
            required=True,
            session=audit_session,
        )
        await audit_session.commit()


async def audit_denied(*, reason: str, prefix: str | None = None) -> None:
    """One fail-closed denial row for a failed principal authentication."""
    await log_audit_action(
        required=True,
        tenant_id=None,
        user_id=None,
        action="integration_principal.denied",
        resource_type="integration_principal",
        outcome=OUTCOME_DENIED,
        new_value={"reason": reason, "secret_prefix": prefix},
    )
    logger.info("integration principal authentication denied: %s", reason)


async def list_principals(
    db: AsyncSession, *, tenant_id: UUID, owner_id: UUID | None = None
) -> list[IntegrationPrincipal]:
    """Principal rows in one tenant (optionally one owner's)."""
    stmt = select(IntegrationPrincipal).where(IntegrationPrincipal.tenant_id == tenant_id)
    if owner_id is not None:
        stmt = stmt.where(IntegrationPrincipal.owner_user_id == owner_id)
    result = await db.execute(stmt.order_by(IntegrationPrincipal.created_at.desc()))
    return list(result.scalars().all())


async def get_principal(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    principal_id: UUID | str,
    owner_id: UUID | None = None,
) -> IntegrationPrincipal:
    """One tenant principal; another owner's id is a hidden 404."""
    try:
        resolved = UUID(str(principal_id))
    except ValueError:
        raise NotFoundError("Integration principal not found") from None
    stmt = select(IntegrationPrincipal).where(
        IntegrationPrincipal.id == resolved,
        IntegrationPrincipal.tenant_id == tenant_id,
    )
    if owner_id is not None:
        stmt = stmt.where(IntegrationPrincipal.owner_user_id == owner_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Integration principal not found")
    return row


async def create_principal(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    owner: UserModel,
    name: str,
    kind: str = IntegrationPrincipalKind.SERVICE.value,
    integration_id: UUID | str | None = None,
    scopes: list[str] | None = None,
    patient_scope: str = PatientScope.ALL.value,
    patient_ids: list | None = None,
    expires_at: datetime | None = None,
    created_by: UUID | None = None,
) -> tuple[IntegrationPrincipal, str]:
    """Create one principal; returns ``(row, plaintext)`` — plaintext once."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValidationError("Principal name is required")
    if len(clean_name) > 200:
        raise ValidationError("Principal name is too long")
    try:
        resolved_kind = IntegrationPrincipalKind(kind)
    except ValueError:
        raise ValidationError("Unknown principal kind") from None
    try:
        scope = PatientScope(patient_scope)
    except ValueError:
        raise ValidationError("Unknown patient scope") from None

    integration: UserIntegration | None = None
    if integration_id is not None:
        integration = await _load_integration(db, UUID(str(integration_id)), tenant_id=tenant_id)
        resolved_kind = IntegrationPrincipalKind.INTEGRATION
        if owner.tenant_id != integration.tenant_id:
            raise NotFoundError("Integration not found")
        if owner.id != integration.user_id:
            owner = await _load_owner(db, integration.user_id)
    elif resolved_kind is IntegrationPrincipalKind.INTEGRATION:
        raise ValidationError("An integration principal requires integration_id")
    if str(owner.tenant_id) != str(tenant_id):
        raise NotFoundError("Principal owner not found")

    if integration is not None:
        existing = await active_principal_for_integration(db, integration)
        if existing is not None:
            raise ValidationError(
                "An active principal already exists for this integration — rotate it "
                "instead of creating a second one"
            )

    ceiling = derive_permissions(integration.provider if integration is not None else None)
    resolved_scopes = _normalize_scopes(
        owner, scopes, ceiling=ceiling if integration is not None else None
    )
    resolved_patients = await _validate_patient_ids(db, owner, scope, list(patient_ids or []))
    plaintext, prefix, digest = generate_secret()
    row = IntegrationPrincipal(
        tenant_id=tenant_id,
        owner_user_id=owner.id,
        name=clean_name,
        kind=resolved_kind.value,
        integration_id=integration.id if integration is not None else None,
        scopes=resolved_scopes,
        patient_scope=scope.value,
        patient_ids=resolved_patients,
        status=IntegrationPrincipalStatus.ACTIVE.value,
        secret_prefix=prefix,
        secret_hash=digest,
        expires_at=_resolve_expiry(expires_at),
        created_by=created_by or owner.id,
    )
    db.add(row)
    await db.flush()
    await _audit(db, "integration_principal.created", row, actor_id=created_by or owner.id)
    return row, plaintext


async def rotate_principal(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    principal_id: UUID | str,
    actor_id: UUID,
    owner_id: UUID | None = None,
) -> tuple[IntegrationPrincipal, str]:
    """Issue a replacement and revoke the predecessor in one transaction."""
    predecessor = await get_principal(
        db, tenant_id=tenant_id, principal_id=principal_id, owner_id=owner_id
    )
    if IntegrationPrincipalStatus(predecessor.status) is not IntegrationPrincipalStatus.ACTIVE:
        raise ValidationError("Only an active principal can be rotated")
    owner = await _load_owner(db, predecessor.owner_user_id)
    domain = None
    if predecessor.integration_id is not None:
        integration = await _load_integration(db, predecessor.integration_id, tenant_id=tenant_id)
        domain = integration.provider
    ceiling = derive_permissions(domain)
    resolved_scopes = _normalize_scopes(
        owner, list(predecessor.scopes or []), ceiling=ceiling if domain else None
    )

    now = _utcnow()
    predecessor.status = IntegrationPrincipalStatus.ROTATED.value
    predecessor.revoked_at = now
    predecessor.updated_by = actor_id
    db.add(predecessor)
    await db.flush()

    plaintext, prefix, digest = generate_secret()
    row = IntegrationPrincipal(
        tenant_id=predecessor.tenant_id,
        owner_user_id=predecessor.owner_user_id,
        name=predecessor.name,
        kind=predecessor.kind,
        integration_id=predecessor.integration_id,
        scopes=resolved_scopes,
        patient_scope=predecessor.patient_scope,
        patient_ids=list(predecessor.patient_ids or []),
        status=IntegrationPrincipalStatus.ACTIVE.value,
        secret_prefix=prefix,
        secret_hash=digest,
        expires_at=predecessor.expires_at,
        rotated_from=predecessor.id,
        created_by=actor_id,
    )
    db.add(row)
    await db.flush()
    await _audit(
        db,
        "integration_principal.rotated",
        row,
        actor_id=actor_id,
        prior={"revoked_principal_id": str(predecessor.id)},
    )
    return row, plaintext


async def revoke_principal(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    principal_id: UUID | str,
    actor_id: UUID,
    owner_id: UUID | None = None,
) -> IntegrationPrincipal:
    """Revoke one principal; the secret never resolves again."""
    row = await get_principal(db, tenant_id=tenant_id, principal_id=principal_id, owner_id=owner_id)
    if IntegrationPrincipalStatus(row.status) is IntegrationPrincipalStatus.REVOKED:
        raise ValidationError("Principal is already revoked")
    row.status = IntegrationPrincipalStatus.REVOKED.value
    row.revoked_at = _utcnow()
    row.updated_by = actor_id
    db.add(row)
    await db.flush()
    await _audit(db, "integration_principal.revoked", row, actor_id=actor_id)
    return row


async def delete_for_integration(db: AsyncSession, *, tenant_id: UUID, integration_id: UUID) -> int:
    """Remove every principal bound to an erased connector (tenant-scoped).

    The principal FKs the integration with ``ON DELETE SET NULL`` while the
    ``ck_integration_principals_integration_kind_requires_binding`` check
    requires an ``integration``-kind row to name its integration, so deleting
    the connector row with a surviving principal would fail at the database.
    Connector erasure therefore revokes (audited, at the caller) and then
    removes the bound principal rows in the same transaction. Returns the
    number of rows removed.
    """
    from sqlalchemy import delete

    result = await db.execute(
        delete(IntegrationPrincipal).where(
            IntegrationPrincipal.tenant_id == tenant_id,
            IntegrationPrincipal.integration_id == integration_id,
        )
    )
    await db.flush()
    return result.rowcount or 0


__all__ = [
    "INTEGRATION_PRINCIPAL_PREFIX",
    "UNSET",
    "active_principal_for_integration",
    "assignable_permissions",
    "audit_denied",
    "create_principal",
    "delete_for_integration",
    "derive_permissions",
    "generate_secret",
    "get_principal",
    "hash_secret",
    "list_principals",
    "principal_from_row",
    "provision_for_integration",
    "record_use",
    "resolve_actor",
    "resolve_principal",
    "revoke_principal",
    "rotate_principal",
    "secret_prefix_of",
    "select_integration",
]
