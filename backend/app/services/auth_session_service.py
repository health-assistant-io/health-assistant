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
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.core.database import DATABASE_AVAILABLE, AsyncSessionLocal
from app.models.auth_session_model import AuthSessionModel

logger = logging.getLogger(__name__)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
) -> str:
    """Insert one ``auth_sessions`` row. Returns the family id (``fid``)."""
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
        result = await session.execute(
            select(AuthSessionModel).where(AuthSessionModel.id == fid)
        )
        return result.scalar_one_or_none()


async def rotate_family(
    family_id: str | UUID, *, refresh_jti_hash: str, expires_at: datetime
) -> None:
    """Point the family at the next refresh jti (rolling window)."""
    if not DATABASE_AVAILABLE:
        return
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(AuthSessionModel)
            .where(AuthSessionModel.id == UUID(str(family_id)))
            .values(refresh_jti_hash=refresh_jti_hash, expires_at=expires_at)
        )
        await session.commit()


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


async def issue_session(
    user,
    *,
    auth_mode: str,
    client_label: str = "",
    extra_claims: dict | None = None,
    with_refresh: bool = True,
    family_id: str | None = None,
    absolute_expires_at: datetime | None = None,
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
    """
    from app.core import token_store
    from app.core.config import settings
    from app.core.security import (
        create_refresh_token,
        create_session_access_token,
    )

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
            )
        await token_store.register_refresh(
            str(user.id), refresh_jti, int(refresh_ttl.total_seconds())
        )

    access_token, access_jti = create_session_access_token(
        {**claims, "fid": fid}, expires_delta=access_ttl
    )
    await token_store.register_session(
        str(user.id), access_jti, int(access_ttl.total_seconds())
    )

    return IssuedSession(
        access_token=access_token,
        refresh_token=refresh_token,
        family_id=fid or "",
        access_expires_in=int(access_ttl.total_seconds()),
    )
