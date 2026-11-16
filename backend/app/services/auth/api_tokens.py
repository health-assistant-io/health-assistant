"""Scoped API tokens / personal access tokens (ADR 0054 Phase 6, 12C2B).

The server side of ``/me/api-tokens`` and the PAT authentication path:

- **Creation** returns the plaintext exactly once; only the ``sha256`` and
  the lookup prefix persist. Every operation (create/update/rotate/revoke)
  is audited fail-closed and every use writes a throttled ``api_token.used``
  row — the plaintext never appears in an audit payload, a log line or the
  database.
- **Least privilege at every boundary.** The permission list must be a
  subset of the owner's held catalog permissions at creation, rotation,
  update *and* use: a role demotion narrows the token on the next request
  instead of outliving the decision. The effective scope is the owner's
  live role-scope rule capped at tenant (a PAT never reaches the system
  actor), and the patient scope is intersected with the owner's current
  patient reach (own + live grants) — a revoked grant narrows the token.
- **Lifecycle.** Rotation issues a replacement and revokes the predecessor
  in one transaction; revocation sets ``revoked_at`` (the row stays as the
  accountability ledger); optional expiry is enforced at every use.
- **Tenant isolation.** The credential carries the owner's tenant; the
  pre-auth lookup runs in the auth layer's system scope and the resolved
  principal then plants only the tenant GUC (never ``app.system``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from nx_auth.opaque import mint, parse, verify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenant_context
from app.core.authz.permissions import Permission
from app.core.authz.roles import permissions_for
from app.core.errors import NotFoundError, ValidationError
from app.models.api_token import ApiToken, PatientScope
from app.models.enums import Role
from app.models.fhir.patient import Patient
from app.models.user_model import UserModel
from app.schemas.user import TokenData
from app.services.audit.audit_service import OUTCOME_DENIED, log_audit_action
from app.services.auth.access import accessible_patient_ids

logger = logging.getLogger(__name__)

PAT_TOKEN_BRAND = "ha_pat"
"""The kit brand: the bare label, no trailing underscore (the kit inserts it)."""

PAT_TOKEN_PREFIX = f"{PAT_TOKEN_BRAND}_"
"""The opaque value's leading segment (``ha_pat_``) — kept for ``is_pat_token``."""

_PREFIX_BYTES = 6
_USE_AUDIT_INTERVAL_SECONDS = 60

UNSET: object = object()


def _mint_pat() -> tuple[str, str, str]:
    """Mint one health PAT: ``(plaintext, prefix, sha256)`` in the kit's format."""
    plaintext, prefix, digest = mint(brand=PAT_TOKEN_BRAND, prefix_bytes=_PREFIX_BYTES)
    if prefix is None:
        # Unreachable for a branded mint; keeps the non-null prefix column honest.
        raise RuntimeError("a branded opaque mint must return a lookup prefix")
    return plaintext, prefix, digest


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _owner_principal(owner: UserModel) -> TokenData:
    """The owner's own principal shape (no token fields)."""
    return TokenData(
        user_id=owner.id,
        tenant_id=owner.tenant_id,
        role=Role(owner.role).value,
        sub=str(owner.id),
        email=owner.email,
    )


def _normalize_permissions(owner: UserModel, values: list[str] | None) -> list[str]:
    """Validate ``values`` as a non-empty subset of the owner's permissions."""
    held = permissions_for(Role(owner.role))
    normalized: list[Permission] = []
    for value in values or []:
        try:
            permission = Permission(value)
        except ValueError:
            raise ValidationError(f"Unknown permission {value!r}") from None
        if permission not in held:
            raise ValidationError(f"Permission {permission.value!r} is not held by the owner")
        if permission not in normalized:
            normalized.append(permission)
    if not normalized:
        raise ValidationError("At least one permission is required")
    ordered = [permission for permission in Permission if permission in normalized]
    return [permission.value for permission in ordered]


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
    reachable = await accessible_patient_ids(await _owner_principal(owner), db)
    if reachable is not None:
        outside = [value for value in normalized if UUID(value) not in reachable]
        if outside:
            raise ValidationError("The owner cannot reach every requested patient")
    return normalized


def _audit_payload(row: ApiToken) -> dict:
    """The safe audit projection — never the plaintext or its hash."""
    return {
        "name": row.name,
        "token_prefix": row.token_prefix,
        "permissions": list(row.permissions or []),
        "patient_scope": row.patient_scope,
        "patient_ids": [str(value) for value in (row.patient_ids or [])],
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


async def _audit(
    db: AsyncSession,
    action: str,
    row: ApiToken,
    *,
    actor_id: UUID | None,
    prior: dict | None = None,
) -> None:
    await log_audit_action(
        tenant_id=row.tenant_id,
        user_id=actor_id,
        action=action,
        resource_type="api_token",
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


async def create_token(
    db: AsyncSession,
    *,
    owner: UserModel,
    name: str,
    permissions: list[str],
    patient_scope: str = PatientScope.ALL.value,
    patient_ids: list | None = None,
    expires_at: datetime | None = None,
    created_by: UUID | None = None,
) -> tuple[ApiToken, str]:
    """Create one token; returns ``(row, plaintext)`` — plaintext once only."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValidationError("Token name is required")
    if len(clean_name) > 200:
        raise ValidationError("Token name is too long")
    try:
        scope = PatientScope(patient_scope)
    except ValueError:
        raise ValidationError("Unknown patient scope") from None
    resolved_permissions = _normalize_permissions(owner, permissions)
    resolved_patients = await _validate_patient_ids(db, owner, scope, list(patient_ids or []))
    plaintext, prefix, digest = _mint_pat()
    row = ApiToken(
        tenant_id=owner.tenant_id,
        owner_user_id=owner.id,
        name=clean_name,
        token_prefix=prefix,
        token_hash=digest,
        permissions=resolved_permissions,
        patient_scope=scope.value,
        patient_ids=resolved_patients,
        expires_at=_resolve_expiry(expires_at),
        created_by=created_by or owner.id,
    )
    db.add(row)
    await db.flush()
    await _audit(db, "api_token.created", row, actor_id=created_by or owner.id)
    return row, plaintext


async def list_tokens(db: AsyncSession, *, owner_id: UUID, tenant_id: UUID) -> list[ApiToken]:
    """Every token row owned by ``owner_id`` (revoked/expired rows included)."""
    result = await db.execute(
        select(ApiToken)
        .where(ApiToken.owner_user_id == owner_id, ApiToken.tenant_id == tenant_id)
        .order_by(ApiToken.created_at.desc())
    )
    return list(result.scalars().all())


async def get_token(
    db: AsyncSession, *, owner_id: UUID, tenant_id: UUID, token_id: UUID | str
) -> ApiToken:
    """One owned token row or a hidden 404 (never another owner's)."""
    try:
        resolved = UUID(str(token_id))
    except ValueError:
        raise NotFoundError("API token not found") from None
    result = await db.execute(
        select(ApiToken).where(
            ApiToken.id == resolved,
            ApiToken.owner_user_id == owner_id,
            ApiToken.tenant_id == tenant_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("API token not found")
    return row


async def update_token(
    db: AsyncSession,
    *,
    owner: UserModel,
    token_id: UUID | str,
    name: object = UNSET,
    permissions: object = UNSET,
    patient_scope: object = UNSET,
    patient_ids: object = UNSET,
    expires_at: object = UNSET,
    actor_id: UUID | None = None,
) -> ApiToken:
    """Update the mutable facts, re-validating every subset law."""
    row = await get_token(db, owner_id=owner.id, tenant_id=owner.tenant_id, token_id=token_id)
    if row.revoked_at is not None:
        raise ValidationError("A revoked token cannot be updated")
    prior = _audit_payload(row)
    if name is not UNSET:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValidationError("Token name is required")
        if len(clean_name) > 200:
            raise ValidationError("Token name is too long")
        row.name = clean_name
    if permissions is not UNSET:
        row.permissions = _normalize_permissions(owner, list(permissions or []))
    target_scope = PatientScope(row.patient_scope)
    target_ids: list = list(row.patient_ids or [])
    if patient_scope is not UNSET:
        try:
            target_scope = PatientScope(str(patient_scope))
        except ValueError:
            raise ValidationError("Unknown patient scope") from None
        if target_scope is not PatientScope.GRANTED:
            target_ids = []
    if patient_ids is not UNSET:
        target_ids = list(patient_ids or [])
    if patient_scope is not UNSET or patient_ids is not UNSET:
        row.patient_ids = await _validate_patient_ids(db, owner, target_scope, target_ids)
        row.patient_scope = target_scope.value
    if expires_at is not UNSET:
        row.expires_at = _resolve_expiry(expires_at)  # type: ignore[arg-type]
    db.add(row)
    await db.flush()
    await _audit(db, "api_token.updated", row, actor_id=actor_id, prior=prior)
    return row


async def rotate_token(
    db: AsyncSession, *, owner: UserModel, token_id: UUID | str, actor_id: UUID | None = None
) -> tuple[ApiToken, str]:
    """Issue a replacement for a live token and revoke the predecessor."""
    predecessor = await get_token(
        db, owner_id=owner.id, tenant_id=owner.tenant_id, token_id=token_id
    )
    if predecessor.revoked_at is not None:
        raise ValidationError("A revoked token cannot be rotated")
    now = _utcnow()
    resolved_permissions = _normalize_permissions(owner, list(predecessor.permissions or []))
    plaintext, prefix, digest = _mint_pat()
    row = ApiToken(
        tenant_id=predecessor.tenant_id,
        owner_user_id=predecessor.owner_user_id,
        name=predecessor.name,
        token_prefix=prefix,
        token_hash=digest,
        permissions=resolved_permissions,
        patient_scope=predecessor.patient_scope,
        patient_ids=list(predecessor.patient_ids or []),
        expires_at=predecessor.expires_at,
        rotated_from=predecessor.id,
        created_by=actor_id or owner.id,
    )
    db.add(row)
    predecessor.revoked_at = now
    db.add(predecessor)
    await db.flush()
    await _audit(
        db,
        "api_token.rotated",
        row,
        actor_id=actor_id,
        prior={"revoked_token_id": str(predecessor.id)},
    )
    return row, plaintext


async def revoke_token(
    db: AsyncSession, *, owner_id: UUID, tenant_id: UUID, token_id: UUID | str, actor_id: UUID
) -> ApiToken:
    """Revoke one owned token; the hash never resolves again."""
    row = await get_token(db, owner_id=owner_id, tenant_id=tenant_id, token_id=token_id)
    if row.revoked_at is not None:
        raise ValidationError("Token is already revoked")
    row.revoked_at = _utcnow()
    row.updated_by = actor_id
    db.add(row)
    await db.flush()
    await _audit(db, "api_token.revoked", row, actor_id=actor_id)
    return row


def principal_from_row(row: ApiToken, owner: UserModel) -> TokenData:
    """Build the PAT principal, re-intersecting the permission set live.

    ``pat_permissions`` carries only what the owner *still* holds, so a
    demotion narrows every outstanding token on its next request.
    """
    held = permissions_for(Role(owner.role))
    effective = [value for value in (row.permissions or []) if value in {p.value for p in held}]
    return TokenData(
        user_id=owner.id,
        tenant_id=owner.tenant_id,
        role=Role(owner.role).value,
        sub=str(owner.id),
        email=owner.email,
        token_kind="pat",
        pat_id=str(row.id),
        pat_prefix=row.token_prefix,
        pat_permissions=effective,
        pat_patient_scope=row.patient_scope,
        pat_patient_ids=[str(value) for value in (row.patient_ids or [])],
    )


async def resolve_principal(value: str | None) -> TokenData | None:
    """Resolve a presented bearer to a live PAT principal (or ``None``).

    The lookup is a pre-auth step, so it runs in the auth layer's system
    scope; the returned principal then plants **only** the tenant GUC
    (``app.system`` is never set for a PAT). Invalid, expired, revoked and
    cross-tenant (owner/tenant mismatch) credentials all answer ``None``.
    """
    if not value:
        return None
    presented = parse(value.strip(), brand=PAT_TOKEN_BRAND, prefix_bytes=_PREFIX_BYTES)
    if presented is None:
        return None
    prefix, digest = presented

    from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal

    if not DATABASE_AVAILABLE:
        return None

    with tenant_context.system_scope("auth:pat"):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ApiToken).where(ApiToken.token_prefix == prefix))
            row = result.scalar_one_or_none()
            if row is None or not verify(row.token_hash, digest) or not row.is_live:
                return None
            owner_result = await session.execute(
                select(UserModel).where(UserModel.id == row.owner_user_id)
            )
            owner = owner_result.scalar_one_or_none()
            if owner is None or not owner.is_active:
                return None
            if str(owner.tenant_id) != str(row.tenant_id):
                return None
            principal = principal_from_row(row, owner)
            last_used_at = row.last_used_at

    tenant_context.set_principal(principal)
    await _record_use(principal, last_used_at)
    return principal


async def _record_use(principal: TokenData, last_used_at: datetime | None) -> None:
    """Stamp ``last_used_at`` and audit the use (throttled per minute)."""
    from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal

    if not DATABASE_AVAILABLE:
        return
    now = _utcnow()
    if last_used_at is not None and (now - _as_utc(last_used_at)).total_seconds() < (
        _USE_AUDIT_INTERVAL_SECONDS
    ):
        return
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ApiToken).where(ApiToken.id == UUID(str(principal.pat_id)))
        )
        row = result.scalar_one_or_none()
        if row is None:
            return
        row.last_used_at = now
        session.add(row)
        await log_audit_action(
            tenant_id=row.tenant_id,
            user_id=row.owner_user_id,
            action="api_token.used",
            resource_type="api_token",
            resource_id=row.id,
            new_value={
                "token_prefix": row.token_prefix,
                "permissions": list(principal.pat_permissions or []),
            },
            required=True,
            session=session,
        )
        await session.commit()


async def audit_denied(*, reason: str, prefix: str | None = None) -> None:
    """One fail-closed denial row for a failed PAT authentication."""
    await log_audit_action(
        required=True,
        tenant_id=None,
        user_id=None,
        action="api_token.denied",
        resource_type="api_token",
        outcome=OUTCOME_DENIED,
        new_value={"reason": reason, "token_prefix": prefix},
    )
    logger.info("api token authentication denied: %s", reason)


__all__ = [
    "PAT_TOKEN_BRAND",
    "PAT_TOKEN_PREFIX",
    "UNSET",
    "audit_denied",
    "create_token",
    "get_token",
    "list_tokens",
    "principal_from_row",
    "resolve_principal",
    "revoke_token",
    "rotate_token",
    "update_token",
]
