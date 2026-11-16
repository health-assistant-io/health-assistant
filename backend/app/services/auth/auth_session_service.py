"""``auth_sessions`` refresh families (identity-auth §5/§8) + token issuance.

One row per signed-in device ("family"): the refresh token rotates on
every use and the row holds the sha256 of the *current* refresh ``jti``
(never a raw token). ``expires_at`` is the rolling window (7 days),
``absolute_expires_at`` the hard cap regardless of activity (30 days).

Revocation semantics: logout = revoke the row; logout-all = revoke all
rows **and** bump ``users.token_version`` (access tokens die immediately
via the ``ver`` claim). Refresh replay of a rotated ``jti`` ⇒ the whole
family is revoked + ``ver`` bumped (§8).

The Redis jti store (``app.core.token_store``) remains the hot
revocation layer checked on every request; these rows are the
authoritative family record (device list surface + absolute cap).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal
from app.models.auth_session_model import AuthSessionModel

logger = logging.getLogger(__name__)

#: ``auth_sessions.user_agent`` column width (``0019_session_metadata``).
USER_AGENT_MAX_LENGTH = 512


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(UTC)


def device_hint(user_agent: str | None, *, max_length: int = 200) -> str:
    """Sanitized device hint for ``client_label`` (§5/§12): the request's
    ``User-Agent`` with control characters stripped and whitespace
    collapsed, capped at the column width."""
    if not user_agent:
        return ""
    printable = "".join(ch for ch in user_agent if ch.isprintable())
    return " ".join(printable.split())[:max_length]


@dataclass
class IssuedSession:
    access_token: str
    refresh_token: str
    family_id: str
    access_expires_in: int


async def create_family(
    *,
    user_id: str | UUID,
    refresh_jti_hash: str,
    expires_at: datetime,
    absolute_expires_at: datetime,
    client_label: str = "",
    family_id: str | UUID | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Insert one ``auth_sessions`` row. Returns the family id (``fid``).

    ``ip`` / ``user_agent`` are the device facts observed at issuance
    (migration ``0019_session_metadata``); ``last_seen_at`` starts at the
    mint itself — a fresh family has just been used. ``user_agent`` is
    truncated to the column width (``String(512)``) here rather than letting
    the INSERT fail: a long ``User-Agent`` header is not a sign-in error.
    """
    if not DATABASE_AVAILABLE:
        raise RuntimeError("auth_sessions require the database")
    fid = str(family_id or uuid4())
    async with AsyncSessionLocal() as session:
        session.add(
            AuthSessionModel(
                id=UUID(fid),
                user_id=UUID(str(user_id)),
                refresh_jti_hash=refresh_jti_hash,
                expires_at=expires_at,
                absolute_expires_at=absolute_expires_at,
                client_label=(client_label or "")[:200],
                ip=(ip or None),
                user_agent=(user_agent or None) and str(user_agent)[:USER_AGENT_MAX_LENGTH],
                last_seen_at=utcnow(),
            )
        )
        await session.commit()
    return fid


async def get_family(family_id: str | UUID) -> AuthSessionModel | None:
    if not DATABASE_AVAILABLE:
        return None
    try:
        fid = UUID(str(family_id))
    except ValueError:
        return None
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AuthSessionModel).where(AuthSessionModel.id == fid))
        return result.scalar_one_or_none()


async def rotate_family(
    family_id: str | UUID, *, refresh_jti_hash: str, expires_at: datetime
) -> None:
    """Point the family at the next refresh jti (rolling window).

    The rotation itself is activity: ``last_seen_at`` moves forward. The
    device facts (``ip`` / ``user_agent``) stay as first observed — the
    list answers "which device signed in", not "where it roams".
    """
    if not DATABASE_AVAILABLE:
        return
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(AuthSessionModel)
            .where(AuthSessionModel.id == UUID(str(family_id)))
            .values(
                refresh_jti_hash=refresh_jti_hash,
                expires_at=expires_at,
                last_seen_at=utcnow(),
            )
        )
        await session.commit()


#: Minimum seconds between ``last_seen_at`` writes for one family — the
#: device list does not need per-request precision, and every request in a
#: busy tenant would otherwise be a write.
LAST_SEEN_THROTTLE_SECONDS = 60


async def touch_last_seen(family_id: str | UUID | None) -> bool:
    """Move a family's ``last_seen_at`` forward, at most once per minute.

    The throttle is a Redis ``SET NX EX`` tick (``sessseen:{fid}``), so a
    normal request costs one failed Redis set and no DB write. Unlike the
    revocation layer this is bookkeeping, not enforcement: a Redis outage
    simply stops the stamps (returns ``False``) instead of failing the
    request.
    """
    from app.core import token_store

    if not family_id or not DATABASE_AVAILABLE:
        return False
    try:
        fid = UUID(str(family_id))
    except (ValueError, TypeError):
        return False
    if not await token_store.claim_seen_tick(str(fid), LAST_SEEN_THROTTLE_SECONDS):
        return False
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(AuthSessionModel)
                .where(AuthSessionModel.id == fid)
                .where(AuthSessionModel.revoked_at.is_(None))
                .values(last_seen_at=utcnow())
            )
            await session.commit()
    except Exception as exc:
        logger.debug("last_seen_at write skipped: %s", exc)
        return False
    return True


async def revoke_family(family_id: str | UUID) -> None:
    if not DATABASE_AVAILABLE:
        return
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(AuthSessionModel)
            .where(AuthSessionModel.id == UUID(str(family_id)))
            .where(AuthSessionModel.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        await session.commit()


async def revoke_all_for_user(user_id: str | UUID) -> int:
    """Revoke every live family for a user (logout-all / force-logout)."""
    if not DATABASE_AVAILABLE:
        return 0
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(AuthSessionModel)
            .where(AuthSessionModel.user_id == UUID(str(user_id)))
            .where(AuthSessionModel.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        await session.commit()
        return int(result.rowcount or 0)


async def list_for_user(user_id: str | UUID) -> list[AuthSessionModel]:
    """Live families for the device list (§12 ``GET /api/v1/me/sessions``)."""
    if not DATABASE_AVAILABLE:
        return []
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AuthSessionModel)
            .where(AuthSessionModel.user_id == UUID(str(user_id)))
            .where(AuthSessionModel.revoked_at.is_(None))
            .order_by(AuthSessionModel.created_at.desc())
        )
        return list(result.scalars().all())


async def known_device(
    user_id: str | UUID,
    *,
    ip: str | None,
    user_agent: str | None,
    exclude_family_id: str | UUID | None = None,
) -> bool:
    """Whether this (ip, user agent) pair already has a live family.

    The login-alert trigger: a sign-in that matches no live family of the
    user is a new device. Reads ``auth_sessions`` (the authoritative device
    record), never the Redis layer. Unknown values never match: a family
    stored before ``0019`` has NULL device facts and is not evidence of
    this device. ``exclude_family_id`` skips the family the current sign-in
    just minted (the caller asks *after* issuance).
    """
    if not DATABASE_AVAILABLE or (ip is None and user_agent is None):
        return True
    statement = (
        select(AuthSessionModel.id)
        .where(AuthSessionModel.user_id == UUID(str(user_id)))
        .where(AuthSessionModel.revoked_at.is_(None))
        .where(AuthSessionModel.ip == ip)
        .where(AuthSessionModel.user_agent == user_agent)
        .limit(1)
    )
    if exclude_family_id is not None:
        statement = statement.where(AuthSessionModel.id != UUID(str(exclude_family_id)))
    async with AsyncSessionLocal() as session:
        result = await session.execute(statement)
        return result.first() is not None


async def revoke_others_for_user(
    user_id: str | UUID,
    *,
    keep_family_id: str | UUID | None,
    tenant_id: str | UUID | None = None,
) -> int:
    """Revoke every live family **except** the caller's (self-service sign-out).

    Deliberately not ``logout-all``: the current device keeps working and
    ``users.token_version`` is not bumped, so no other credential of the
    same user dies (an operator holding a separate bearer for their own
    automation is unaffected). Each revoked family gets the same hygiene as
    a concurrent-limit eviction — its bound access jti is dropped from the
    Redis hot layer and its window forgotten — and the sweep is audited
    (``session.revoke_others``, required).
    """
    from app.core import token_store
    from app.services.audit.audit_service import log_audit_action

    if not DATABASE_AVAILABLE:
        return 0
    uid = UUID(str(user_id))
    keep = str(keep_family_id) if keep_family_id else None
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AuthSessionModel)
                    .where(AuthSessionModel.user_id == uid)
                    .where(AuthSessionModel.revoked_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        now = utcnow()
        victims = [
            row
            for row in rows
            if str(row.id) != keep and (row.expires_at > now or row.absolute_expires_at > now)
        ]
        if not victims:
            return 0
        await session.execute(
            update(AuthSessionModel)
            .where(AuthSessionModel.id.in_([victim.id for victim in victims]))
            .values(revoked_at=now)
        )
        await session.commit()

    for victim in victims:
        bound_jti = await token_store.session_family_jti(str(victim.id))
        if bound_jti:
            await token_store.revoke_session(str(user_id), bound_jti)
        await token_store.unbind_session_family(str(victim.id))
        await token_store.session_window_drop(str(victim.id))

    await log_audit_action(
        required=True,
        tenant_id=tenant_id,
        user_id=user_id,
        action="session.revoke_others",
        resource_type="session",
        new_value={"revoked": len(victims), "kept_family": keep},
    )
    return len(victims)


async def issue_session(
    user,
    *,
    auth_mode: str,
    client_label: str = "",
    extra_claims: dict | None = None,
    with_refresh: bool = True,
    family_id: str | None = None,
    absolute_expires_at: datetime | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> IssuedSession:
    """Create one sign-in: an ``auth_sessions`` family row, the access
    token and the rotating refresh token (both contract claims, §8).

    ``user`` is the live ``UserModel`` row (source of ``ver`` and the
    product claims). ``extra_claims`` may add product claims (switched-set,
    scoped tenant…) but never override the standard ones.

    Pass ``family_id`` (refresh flow) to **rotate** an existing family
    instead of creating one: the row keeps its identity/device label and
    ``absolute_expires_at`` (30-day cap) while the refresh jti rolls
    forward inside ``min(now + rolling, absolute)``.

    ``ip`` / ``user_agent`` are the device facts stored on a new family
    (device list + new-device login alerts); a rotation keeps the row's
    original facts and only moves ``last_seen_at``.
    """
    from app.core import token_store
    from app.core.config import settings
    from app.core.security import (
        create_refresh_token,
        create_session_access_token,
    )
    from app.services.auth import security_policy

    access_ttl = timedelta(minutes=settings.HA_AUTH_ACCESS_TTL_MINUTES)
    refresh_ttl = timedelta(days=settings.HA_AUTH_REFRESH_TTL_DAYS)
    absolute_ttl = timedelta(days=settings.HA_AUTH_REFRESH_ABSOLUTE_DAYS)

    claims = {
        "user_id": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": getattr(user.role, "value", user.role),
        "email": user.email,
        "ver": int(getattr(user, "token_version", 1) or 1),
        "auth_mode": auth_mode,
    }
    if extra_claims:
        claims.update(extra_claims)

    # Tenant security policy (ADR 0054 Phase 3) — read per issuance, never
    # cached. The concurrent-session limit applies to NEW families only (a
    # rotation renews its own); over the limit the oldest-*created* live
    # family is evicted (not usage-based LRU — see ``_evict_over_concurrent_limit``)
    # with its bearer killed via the family→jti binding.
    policy = await security_policy.tenant_security_policy_for(getattr(user, "tenant_id", None))
    if family_id is None and with_refresh and policy.concurrent_session_limit is not None:
        await _evict_over_concurrent_limit(
            user_id=user.id,
            tenant_id=getattr(user, "tenant_id", None),
            limit=policy.concurrent_session_limit,
        )

    now = utcnow()
    fid = family_id if (family_id and with_refresh) else (str(uuid4()) if with_refresh else None)
    refresh_token = ""
    if with_refresh:
        refresh_token, refresh_jti = create_refresh_token(
            {**claims, "fid": fid}, expires_delta=refresh_ttl
        )
        rolling_expiry = now + refresh_ttl
        if family_id:
            cap = absolute_expires_at or (now + absolute_ttl)
            await rotate_family(
                family_id,
                refresh_jti_hash=sha256_hex(refresh_jti),
                expires_at=min(rolling_expiry, cap),
            )
        else:
            await create_family(
                user_id=user.id,
                refresh_jti_hash=sha256_hex(refresh_jti),
                expires_at=rolling_expiry,
                absolute_expires_at=now + absolute_ttl,
                client_label=client_label,
                family_id=fid,
                ip=ip,
                user_agent=user_agent,
            )
        await token_store.register_refresh(
            str(user.id), refresh_jti, int(refresh_ttl.total_seconds())
        )

    access_token, access_jti = create_session_access_token(
        {**claims, "fid": fid}, expires_delta=access_ttl
    )
    await token_store.register_session(str(user.id), access_jti, int(access_ttl.total_seconds()))
    if fid:
        await token_store.bind_session_family(fid, access_jti)
        await security_policy.anchor_session_window(policy, fid)

    return IssuedSession(
        access_token=access_token,
        refresh_token=refresh_token,
        family_id=fid or "",
        access_expires_in=int(access_ttl.total_seconds()),
    )


async def _evict_over_concurrent_limit(
    *, user_id: str | UUID, tenant_id: str | UUID | None, limit: int
) -> None:
    """Evict the oldest-*created* live families beyond the tenant's session limit.

    Deliberately **not** usage-based LRU: eviction orders by
    ``auth_sessions.created_at`` (the family's first login), because the
    per-family activity fact (``sesspol:{fid}`` ``seen`` in the hot layer)
    only exists while a window policy is active and degrades open on a
    Redis outage — an eviction decision must not depend on it. The
    longest-lived device is the one signed out.

    Called before minting a new family, so ``len(live) + 1`` families would
    exist after issuance; every family beyond the limit is revoked (refresh
    chain dies at once), its bound access jti is deleted from the hot layer
    and its window dropped. The count-then-evict race is self-correcting
    (the next login evicts again); every eviction is audited
    (``session.evict``, required/fail-closed).
    """
    from app.core import token_store
    from app.services.audit.audit_service import log_audit_action

    if not DATABASE_AVAILABLE:
        return
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AuthSessionModel)
                    .where(AuthSessionModel.user_id == UUID(str(user_id)))
                    .where(AuthSessionModel.revoked_at.is_(None))
                    .order_by(AuthSessionModel.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
    now = utcnow()
    live = [row for row in rows if row.expires_at > now and row.absolute_expires_at > now]
    over = len(live) + 1 - limit
    for victim in live[: max(over, 0)]:
        await revoke_family(victim.id)
        bound_jti = await token_store.session_family_jti(str(victim.id))
        if bound_jti:
            await token_store.revoke_session(str(user_id), bound_jti)
        await token_store.unbind_session_family(str(victim.id))
        await token_store.session_window_drop(str(victim.id))
        await log_audit_action(
            required=True,
            tenant_id=tenant_id,
            user_id=user_id,
            action="session.evict",
            resource_type="session",
            resource_id=victim.id,
            new_value={"cause": "concurrent_session_limit", "limit": limit},
        )
