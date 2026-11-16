"""SCIM 2.0 directory operations (ADR 0054 Phase 6 — program Phase 12B).

The server side of ``/scim/v2/Users`` and ``/scim/v2/Groups`` (RFC 7644),
tenant-bounded by a hashed bearer token
(:class:`~app.models.scim_token.ScimToken`):

- **Tokens** — created once with the plaintext returned to the admin (never
  stored, logged or echoed again); every later request resolves the bearer
  through its sha256, requiring ``active`` and inside the expiry window.
  Revocation flips ``active`` (the row stays as the accountability ledger).
- **Users** — create / get / list (RFC 7644 pagination + ``userName eq``
  filter basics) / patch / delete. SCIM **delete is deprovision**: the
  account is deactivated and its ``token_version`` bumped (every live
  session dies at the next request — §8), never hard-deleted (health
  accounts are records with clinical data, erased through the lifecycle
  lane only).
- **Groups** — create / get / list / patch (member add/remove) / delete;
  every membership change is audited. Groups hold **no role of their own**:
  the tenant's SSO config ``group_role_map`` maps a display name to a role
  and :func:`apply_group_role` re-roles affected members (capped to the SSO
  vocabulary — never SYSTEM_ADMIN), the same mapping the OIDC login path
  applies.
- **Auditing** — every mutation (and every denied authentication attempt)
  writes a fail-closed ``audit_events`` row (§17); the actor is the SCIM
  token (``actor_id`` of its row), tenant-scoped.

Retention: rows live with the tenant (cascade); nothing here prunes.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.enums import Role
from app.models.scim_group import ScimGroup, ScimGroupMember
from app.models.scim_token import SCIM_TOKEN_SCOPES, ScimToken
from app.models.user_model import UserModel
from app.services.audit.audit_service import OUTCOME_DENIED, OUTCOME_OK, log_audit_action

logger = logging.getLogger(__name__)

SCIM_SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_SCHEMA_LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_SCHEMA_PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"

TOKEN_PREFIX = "ha_scim_"
_TOKEN_RANDOM_BYTES = 32

_MAX_PAGE = 200

_USER_FILTER_RE = re.compile(r'^\s*userName\s+eq\s+"([^"]+)"\s*$', re.IGNORECASE)


def hash_token(value: str) -> str:
    """The sha256 lookup key of a bearer value (plaintext never stored)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- tokens ------------------------------------------------------------------


async def create_token(
    db: AsyncSession,
    *,
    tenant_id,
    name: str,
    created_by=None,
    expires_at: datetime | None = None,
    scopes: list[str] | None = None,
) -> tuple[ScimToken, str]:
    """Create one SCIM credential. Returns ``(row, plaintext)`` — the
    plaintext rides the creation response once and is never recoverable."""
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_RANDOM_BYTES)
    row = ScimToken(
        tenant_id=tenant_id,
        name=(name or "scim")[:200],
        token_hash=hash_token(plaintext),
        scopes=list(scopes or [SCIM_TOKEN_SCOPES[0]]),
        active=True,
        expires_at=expires_at,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    await log_audit_action(
        required=True,
        tenant_id=tenant_id,
        user_id=created_by,
        action="scim.token_created",
        resource_type="scim_token",
        resource_id=row.id,
        new_value={"name": row.name},
    )
    return row, plaintext


async def revoke_token(db: AsyncSession, *, tenant_id, token_id, actor_id=None) -> ScimToken:
    """Revoke one token (``active = false``); the hash never resolves again."""
    row = await get_token(db, tenant_id=tenant_id, token_id=token_id)
    if not row.active:
        raise ConflictError("Token is already revoked")
    row.active = False
    db.add(row)
    await db.flush()
    await log_audit_action(
        required=True,
        tenant_id=tenant_id,
        user_id=actor_id,
        action="scim.token_revoked",
        resource_type="scim_token",
        resource_id=row.id,
        new_value={"name": row.name},
    )
    return row


async def get_token(db: AsyncSession, *, tenant_id, token_id) -> ScimToken:
    result = await db.execute(
        select(ScimToken).where(
            ScimToken.tenant_id == tenant_id,
            ScimToken.id == token_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError("SCIM token not found")
    return row


async def list_tokens(db: AsyncSession, *, tenant_id) -> list[ScimToken]:
    """The tenant's tokens, live first (hash-free projections upstream)."""
    result = await db.execute(
        select(ScimToken)
        .where(ScimToken.tenant_id == tenant_id)
        .order_by(ScimToken.active.desc(), ScimToken.created_at.desc())
    )
    return list(result.scalars().all())


async def resolve_principal(token_value: str | None) -> ScimToken | None:
    """Resolve a bearer to a live credential row (or ``None``).

    The lookup key is the sha256 of the presented value; ``active`` and the
    optional ``expires_at`` gate. A deleted tenant cascades its rows away,
    so a stale bearer resolves to nothing — the same 401 as a revoked one.
    """
    if not token_value or not token_value.strip():
        return None
    result = await _principal_session_execute(hash_token(token_value.strip()))
    row = result.scalar_one_or_none() if result is not None else None
    if row is None or not row.is_live:
        return None
    return row


async def _principal_session_execute(token_hash: str):
    """The hash lookup on a short-lived session (no ambient principal)."""
    from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal

    if not DATABASE_AVAILABLE:
        return None
    async with AsyncSessionLocal() as session:
        return await session.execute(select(ScimToken).where(ScimToken.token_hash == token_hash))


async def touch_last_used(principal: ScimToken) -> None:
    """Record usage (throttled to one write per minute per token)."""
    now = utcnow()
    last = principal.last_used_at
    if last is not None and (now - last.replace(tzinfo=last.tzinfo or UTC)).total_seconds() < 60:
        return
    from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal

    if not DATABASE_AVAILABLE:
        return
    async with AsyncSessionLocal() as session:
        merged = await session.merge(ScimToken(id=principal.id, last_used_at=now))
        merged.last_used_at = now
        await session.commit()


async def audit_denied(action: str, *, detail: dict) -> None:
    """One fail-closed denial row for a failed SCIM authentication.

    The tenant is unknown (that is what the refusal means) — the row rides
    the system chain like the anonymous auth denials.
    """
    await log_audit_action(
        required=True,
        tenant_id=None,
        user_id=None,
        action=action,
        resource_type="scim",
        outcome=OUTCOME_DENIED,
        new_value=detail,
    )


# --- users -------------------------------------------------------------------


def to_scim_user(user: UserModel, *, location_base: str = "") -> dict[str, Any]:
    """RFC 7643 ``User`` projection (no password material, ever).

    Reads **loaded attribute values only** — a server-side ``onupdate``
    column (``updated_at``) is unloaded right after a flush, and a lazy
    refresh from sync context is the MissingGreenlet bug class.
    """
    loaded = sa_inspect(user).dict
    full_name = loaded.get("full_name") or ""
    parts = full_name.split(" ", 1)
    location = f"{location_base}/Users/{loaded.get('id')}" if location_base else None
    created = loaded.get("created_at")
    updated = loaded.get("updated_at")
    return {
        "schemas": [SCIM_SCHEMA_USER],
        "id": str(loaded.get("id")),
        "userName": loaded.get("email"),
        "active": bool(loaded.get("is_active")),
        "name": {"formatted": full_name, "givenName": parts[0] if parts else ""},
        "displayName": full_name or loaded.get("email"),
        "emails": [{"value": loaded.get("email"), "primary": True, "type": "work"}],
        "meta": {
            "resourceType": "User",
            "created": created.isoformat() if created else None,
            "lastModified": updated.isoformat() if updated else None,
            "location": location,
        },
    }


def parse_user_filter(filter_expr: str | None) -> str | None:
    """The one supported filter: ``userName eq "..."`` (case-insensitive)."""
    if not filter_expr:
        return None
    match = _USER_FILTER_RE.match(filter_expr)
    if match is None:
        raise ValidationError('Unsupported filter; only userName eq "..." is implemented')
    return match.group(1).strip().lower()


async def list_users(
    db: AsyncSession,
    *,
    tenant_id,
    start_index: int = 1,
    count: int = 100,
    email_equals: str | None = None,
) -> tuple[list[UserModel], int]:
    """One page of tenant users (SCIM pagination is 1-based)."""
    count = max(0, min(int(count), _MAX_PAGE))
    start_index = max(1, int(start_index))
    query = select(UserModel).where(UserModel.tenant_id == tenant_id)
    total_query = (
        select(func.count()).select_from(UserModel).where(UserModel.tenant_id == tenant_id)
    )
    if email_equals:
        query = query.where(UserModel.email == email_equals)
        total_query = total_query.where(UserModel.email == email_equals)
    total = (await db.execute(total_query)).scalar() or 0
    result = await db.execute(
        query.order_by(UserModel.created_at.asc()).offset(start_index - 1).limit(count)
    )
    return list(result.scalars().all()), int(total)


async def get_user(db: AsyncSession, *, tenant_id, user_id) -> UserModel:
    """One tenant user (cross-tenant ids answer hidden-404 — §17)."""
    result = await db.execute(
        select(UserModel).where(
            UserModel.tenant_id == tenant_id,
            UserModel.id == user_id,
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found")
    return user


async def create_user(
    db: AsyncSession,
    *,
    tenant_id,
    user_name: str,
    full_name: str = "",
    active: bool = True,
    actor_id=None,
    principal: ScimToken | None = None,
) -> UserModel:
    """Directory user creation (JIT-style provisioning, SCIM-flavored).

    The role is the SSO cap: the tenant's enabled config ``default_role``
    when present, else ``USER`` — SCIM is never a path to a wider role than
    SSO itself would grant.
    """
    from app.services.tenancy.user_service import create_user as service_create_user

    email = user_name.strip().lower()
    existing = await db.execute(select(UserModel).where(UserModel.email == email))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("A user with this email already exists")

    role = await _default_role_for_tenant(db, tenant_id)
    user = await service_create_user(
        email=email,
        password_hash=None,
        tenant_id=tenant_id,
        role=role,
        full_name=(full_name or "")[:200],
        session=db,
    )
    if not active:
        await _deactivate(db, user, actor=actor_id, action="scim.user_created")
    else:
        await log_audit_action(
            required=True,
            tenant_id=tenant_id,
            user_id=actor_id,
            action="scim.user_created",
            resource_type="user",
            resource_id=user.id,
            new_value={"email": user.email, "role": role, "via": "scim"},
        )
    return user


async def _default_role_for_tenant(db: AsyncSession, tenant_id) -> str:
    from app.models.tenant_sso_config import TenantSSOConfig

    result = await db.execute(
        select(TenantSSOConfig)
        .where(TenantSSOConfig.tenant_id == tenant_id, TenantSSOConfig.enabled.is_(True))
        .order_by(TenantSSOConfig.created_at.asc())
        .limit(1)
    )
    config = result.scalar_one_or_none()
    if config is None:
        return Role.USER.value
    return str(config.default_role)


async def _deactivate(db: AsyncSession, user: UserModel, *, actor, action: str) -> None:
    """Deactivate + bump ``token_version`` — every live session dies (§8)."""
    from app.services.tenancy.user_service import bump_token_version

    was_active = bool(user.is_active)
    user.is_active = False
    db.add(user)
    await db.flush()
    if was_active:
        await bump_token_version(user.id, session=db)
    await log_audit_action(
        required=True,
        tenant_id=user.tenant_id,
        user_id=actor,
        action=action,
        resource_type="user",
        resource_id=user.id,
        outcome=OUTCOME_OK,
        old_value={"is_active": was_active},  # gate-allow: *, audit diff
        new_value={"is_active": False, "token_version_bumped": was_active},
    )


async def patch_user(
    db: AsyncSession,
    *,
    tenant_id,
    user_id,
    operations: list[dict],
    actor_id=None,
) -> UserModel:
    """RFC 7644 PatchOp (the subset directories actually push).

    ``replace`` on ``active`` (deprovision/re-activate), ``userName``,
    ``displayName``/``name.*``; ``remove active`` is rejected (a directory
    that un-sets activity must replace it). Unknown paths answer the typed
    ``invalidPath`` 400 — never a silent ignore.
    """
    user = await get_user(db, tenant_id=tenant_id, user_id=user_id)
    for operation in operations or []:
        op = str(operation.get("op", "")).lower()
        path = str(operation.get("path", "") or "").strip().lower()
        value = operation.get("value")
        if isinstance(value, dict) and not path:
            for inner_path, inner_value in value.items():
                user = await _apply_user_op(
                    db,
                    user,
                    tenant_id=tenant_id,
                    op=op,
                    path=str(inner_path),
                    value=inner_value,
                    actor_id=actor_id,
                )
            continue
        user = await _apply_user_op(
            db, user, tenant_id=tenant_id, op=op, path=path, value=value, actor_id=actor_id
        )
    return user


async def _apply_user_op(
    db: AsyncSession,
    user: UserModel,
    *,
    tenant_id,
    op: str,
    path: str,
    value: Any,
    actor_id=None,
) -> UserModel:
    if op not in ("replace", "add"):
        raise ValidationError(f"Unsupported SCIM patch op {op!r}")
    if path == "active":
        if op == "remove":
            raise ValidationError("active cannot be removed; replace it with true/false")
        if not isinstance(value, bool):
            raise ValidationError("active must be a boolean")
        if value is False and user.is_active:
            await _deactivate(db, user, actor=actor_id, action="scim.user_deactivated")
        elif value is True and not user.is_active:
            user.is_active = True
            db.add(user)
            await db.flush()
            await log_audit_action(
                required=True,
                tenant_id=tenant_id,
                user_id=actor_id,
                action="scim.user_reactivated",
                resource_type="user",
                resource_id=user.id,
                new_value={"is_active": True},
            )
        return user
    if path in ("username", "emails", 'emails[type eq "work"].value'):
        if op == "remove":
            raise ValidationError("userName cannot be removed")
        email = str(_single_value(value)).strip().lower()
        if email and email != user.email:
            clash = await db.execute(
                select(func.count()).select_from(UserModel).where(UserModel.email == email)
            )
            if (clash.scalar() or 0) > 0:
                raise ConflictError("A user with this email already exists")
            user.email = email
            db.add(user)
            await db.flush()
        return user
    if path in ("displayname", "name.formatted", "name"):
        user.full_name = str(_single_value(value) or "")[:200]
        db.add(user)
        await db.flush()
        return user
    if path in ("name.givenname",) and op != "remove":
        parts = (user.full_name or "").split(" ", 1)
        rest = parts[1] if len(parts) > 1 else ""
        user.full_name = f"{str(_single_value(value) or '')[:100]} {rest}".strip()
        db.add(user)
        await db.flush()
        return user
    raise ValidationError(f"Unsupported SCIM patch path {path!r}")


def _single_value(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


async def delete_user(
    db: AsyncSession,
    *,
    tenant_id,
    user_id,
    actor_id=None,
) -> None:
    """SCIM delete = deprovision: deactivate + ``token_version`` bump.

    The row is never hard-deleted here (health accounts carry clinical
    records; erasure is the lifecycle lane's decision, not a directory
    client's). Repeat deletes are idempotent (still deactivated).
    """
    user = await get_user(db, tenant_id=tenant_id, user_id=user_id)
    await _deactivate(db, user, actor=actor_id, action="scim.user_deprovisioned")


# --- groups ------------------------------------------------------------------


def to_scim_group(
    group: ScimGroup,
    members: list[UserModel],
    *,
    location_base: str = "",
) -> dict[str, Any]:
    return {
        "schemas": [SCIM_SCHEMA_GROUP],
        "id": str(group.id),
        "displayName": group.display_name,
        "externalId": group.external_id,
        "active": bool(group.active),
        "members": [
            {"value": str(member.id), "display": member.email, "type": "User"} for member in members
        ],
        "meta": {
            "resourceType": "Group",
            "created": group.created_at.isoformat() if group.created_at else None,
            "lastModified": group.updated_at.isoformat() if group.updated_at else None,
            "location": f"{location_base}/Groups/{group.id}" if location_base else None,
        },
    }


async def get_group(db: AsyncSession, *, tenant_id, group_id) -> ScimGroup:
    result = await db.execute(
        select(ScimGroup).where(ScimGroup.tenant_id == tenant_id, ScimGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise NotFoundError("Group not found")
    return group


async def group_members(db: AsyncSession, group_id) -> list[UserModel]:
    result = await db.execute(
        select(UserModel)
        .join(ScimGroupMember, ScimGroupMember.user_id == UserModel.id)
        .where(ScimGroupMember.group_id == group_id)
        .order_by(UserModel.email.asc())
    )
    return list(result.scalars().all())


async def list_groups(
    db: AsyncSession,
    *,
    tenant_id,
    start_index: int = 1,
    count: int = 100,
    display_name_equals: str | None = None,
) -> tuple[list[ScimGroup], int]:
    count = max(0, min(int(count), _MAX_PAGE))
    start_index = max(1, int(start_index))
    query = select(ScimGroup).where(ScimGroup.tenant_id == tenant_id)
    total_query = (
        select(func.count()).select_from(ScimGroup).where(ScimGroup.tenant_id == tenant_id)
    )
    if display_name_equals:
        query = query.where(func.lower(ScimGroup.display_name) == display_name_equals.lower())
        total_query = total_query.where(
            func.lower(ScimGroup.display_name) == display_name_equals.lower()
        )
    total = (await db.execute(total_query)).scalar() or 0
    result = await db.execute(
        query.order_by(ScimGroup.created_at.asc()).offset(start_index - 1).limit(count)
    )
    return list(result.scalars().all()), int(total)


async def create_group(
    db: AsyncSession,
    *,
    tenant_id,
    display_name: str,
    external_id: str | None = None,
    member_ids: list | None = None,
    actor_id=None,
) -> ScimGroup:
    name = (display_name or "").strip()
    if not name:
        raise ValidationError("displayName is required")
    existing = await db.execute(
        select(ScimGroup).where(
            ScimGroup.tenant_id == tenant_id,
            func.lower(ScimGroup.display_name) == name.lower(),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("A group with this displayName already exists")
    group = ScimGroup(
        tenant_id=tenant_id,
        display_name=name[:255],
        external_id=(external_id or None),
        active=True,
        created_by=actor_id,
    )
    db.add(group)
    await db.flush()
    added = await _set_members(db, group, target_ids=member_ids or [], actor_id=actor_id)
    await log_audit_action(
        required=True,
        tenant_id=tenant_id,
        user_id=actor_id,
        action="scim.group_created",
        resource_type="scim_group",
        resource_id=group.id,
        new_value={"displayName": group.display_name, "members_added": len(added)},
    )
    return group


async def _resolve_member_ids(db: AsyncSession, tenant_id, ids: list) -> list:
    """Validated member ids — foreign-tenant ids answer hidden-404."""
    resolved = []
    for raw in ids or []:
        member_id = raw.get("value") if isinstance(raw, dict) else raw
        await get_user(db, tenant_id=tenant_id, user_id=member_id)
        resolved.append(member_id)
    return resolved


async def _set_members(
    db: AsyncSession,
    group: ScimGroup,
    *,
    target_ids: list,
    actor_id=None,
) -> list:
    """Diff the membership to ``target_ids``; audit what moved."""
    current_rows = (
        (await db.execute(select(ScimGroupMember).where(ScimGroupMember.group_id == group.id)))
        .scalars()
        .all()
    )
    current = {str(row.user_id) for row in current_rows}
    resolved = await _resolve_member_ids(db, group.tenant_id, target_ids)
    target = {str(member_id) for member_id in resolved}
    added = [member_id for member_id in resolved if str(member_id) not in current]
    removed = [row.user_id for row in current_rows if str(row.user_id) not in target]
    for member_id in added:
        db.add(ScimGroupMember(group_id=group.id, user_id=member_id, added_by=actor_id))
    for member_id in removed:
        await db.execute(
            ScimGroupMember.__table__.delete()
            .where(ScimGroupMember.group_id == group.id)
            .where(ScimGroupMember.user_id == member_id)
        )
    if added or removed:
        await db.flush()
        await _apply_group_role_to_members(db, group, member_ids=added + [str(m) for m in removed])
        await log_audit_action(
            required=True,
            tenant_id=group.tenant_id,
            user_id=actor_id,
            action="scim.group_membership_changed",
            resource_type="scim_group",
            resource_id=group.id,
            new_value={
                "displayName": group.display_name,
                "added": [str(m) for m in added],
                "removed": [str(m) for m in removed],
            },
        )
    return added


async def patch_group(
    db: AsyncSession,
    *,
    tenant_id,
    group_id,
    operations: list[dict],
    actor_id=None,
) -> ScimGroup:
    """RFC 7644 Group patch: displayName replace, member add/remove/replace."""
    group = await get_group(db, tenant_id=tenant_id, group_id=group_id)
    for operation in operations or []:
        op = str(operation.get("op", "")).lower()
        path = str(operation.get("path", "") or "").strip().lower()
        value = operation.get("value")
        if path == "displayname" and op == "replace":
            name = str(_single_value(value) or "").strip()
            if not name:
                raise ValidationError("displayName cannot be empty")
            group.display_name = name[:255]
            db.add(group)
            await db.flush()
            continue
        if path == "members" or (not path and isinstance(value, dict) and "members" in value):
            payload = value.get("members") if isinstance(value, dict) else value
            payload = payload if isinstance(payload, list) else []
            if op == "add":
                current_rows = (
                    (
                        await db.execute(
                            select(ScimGroupMember).where(ScimGroupMember.group_id == group.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                target = [str(row.user_id) for row in current_rows] + [
                    item.get("value") for item in payload if isinstance(item, dict)
                ]
                await _set_members(db, group, target_ids=target, actor_id=actor_id)
            elif op == "remove":
                group = await _remove_members(db, group, payload=payload, actor_id=actor_id)
            elif op == "replace":
                await _set_members(
                    db,
                    group,
                    target_ids=[item.get("value") for item in payload if isinstance(item, dict)],
                    actor_id=actor_id,
                )
            else:
                raise ValidationError(f"Unsupported SCIM patch op {op!r}")
            continue
        if path == "externalid" and op == "replace":
            group.external_id = str(_single_value(value) or "")[:255] or None
            db.add(group)
            await db.flush()
            continue
        raise ValidationError(f"Unsupported SCIM patch path {path!r}")
    return group


async def _remove_members(
    db: AsyncSession, group: ScimGroup, *, payload: list, actor_id=None
) -> ScimGroup:
    current_rows = (
        (await db.execute(select(ScimGroupMember).where(ScimGroupMember.group_id == group.id)))
        .scalars()
        .all()
    )
    current = {str(row.user_id) for row in current_rows}
    remove_ids = [
        item.get("value") for item in payload if isinstance(item, dict) and item.get("value")
    ]
    resolved = await _resolve_member_ids(db, group.tenant_id, remove_ids)
    targets = [member_id for member_id in resolved if str(member_id) in current]
    if not targets:
        return group
    for member_id in targets:
        await db.execute(
            ScimGroupMember.__table__.delete()
            .where(ScimGroupMember.group_id == group.id)
            .where(ScimGroupMember.user_id == member_id)
        )
    await db.flush()
    await _apply_group_role_to_members(db, group, member_ids=[str(m) for m in targets])
    await log_audit_action(
        required=True,
        tenant_id=group.tenant_id,
        user_id=actor_id,
        action="scim.group_membership_changed",
        resource_type="scim_group",
        resource_id=group.id,
        new_value={"displayName": group.display_name, "removed": [str(m) for m in targets]},
    )
    return group


async def delete_group(db: AsyncSession, *, tenant_id, group_id, actor_id=None) -> None:
    """Group deletion (members cascade; every move is audited)."""
    group = await get_group(db, tenant_id=tenant_id, group_id=group_id)
    members = await group_members(db, group.id)
    await _apply_group_role_to_members(db, group, member_ids=[str(m.id) for m in members])
    await log_audit_action(
        required=True,
        tenant_id=tenant_id,
        user_id=actor_id,
        action="scim.group_deleted",
        resource_type="scim_group",
        resource_id=group.id,
        new_value={"displayName": group.display_name, "members_released": len(members)},
    )
    await db.delete(group)
    await db.flush()


# --- group → role ------------------------------------------------------------


async def _apply_group_role_to_members(
    db: AsyncSession,
    group: ScimGroup,
    *,
    member_ids: list[str],
) -> None:
    """Recompute the mapped role for affected members (membership moves).

    Deterministic: the member's groups ordered by display name; the first
    name the tenant config's ``group_role_map`` maps wins, else the config's
    ``default_role``; no config ⇒ the role stays untouched. SYSTEM_ADMIN
    rows are never touched (SSO is never a path to or from it).
    """
    from app.models.tenant_sso_config import TenantSSOConfig

    if not member_ids:
        return
    config_result = await db.execute(
        select(TenantSSOConfig)
        .where(TenantSSOConfig.tenant_id == group.tenant_id, TenantSSOConfig.enabled.is_(True))
        .order_by(TenantSSOConfig.created_at.asc())
        .limit(1)
    )
    config = config_result.scalar_one_or_none()
    if config is None:
        return
    for member_id in dict.fromkeys(str(m) for m in member_ids):
        user = await get_user(db, tenant_id=group.tenant_id, user_id=member_id)
        current = getattr(user.role, "value", user.role)
        if current == Role.SYSTEM_ADMIN.value:
            continue
        rows = await db.execute(
            select(ScimGroup.display_name)
            .join(ScimGroupMember, ScimGroupMember.group_id == ScimGroup.id)
            .where(ScimGroupMember.user_id == user.id)
            .where(ScimGroup.tenant_id == group.tenant_id)
            .order_by(ScimGroup.display_name.asc())
        )
        names = [row[0] for row in rows.all()]
        computed = config.role_for_groups(names) or str(config.default_role)
        computed = str(computed).strip().upper()
        from app.models.tenant_sso_config import SSO_ASSIGNABLE_ROLES

        if computed not in SSO_ASSIGNABLE_ROLES:
            continue
        if computed == current:
            continue
        user.role = Role(computed)
        db.add(user)
        await db.flush()
        await log_audit_action(
            required=True,
            tenant_id=group.tenant_id,
            user_id=user.id,
            action="scim.role_change",
            resource_type="user",
            resource_id=user.id,
            old_value={"role": current},  # gate-allow: *, audit diff
            new_value={"role": computed, "group": group.display_name},
        )


__all__ = [
    "SCIM_SCHEMA_GROUP",
    "SCIM_SCHEMA_LIST",
    "SCIM_SCHEMA_PATCH",
    "SCIM_SCHEMA_USER",
    "TOKEN_PREFIX",
    "audit_denied",
    "create_group",
    "create_token",
    "create_user",
    "delete_group",
    "delete_user",
    "get_group",
    "get_token",
    "get_user",
    "group_members",
    "hash_token",
    "list_groups",
    "list_tokens",
    "list_users",
    "parse_user_filter",
    "patch_group",
    "patch_user",
    "resolve_principal",
    "revoke_token",
    "to_scim_group",
    "to_scim_user",
    "touch_last_used",
]
