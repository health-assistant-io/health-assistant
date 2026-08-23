"""Server-side refresh-token store (audit A5).

Holds the ``jti`` of every active refresh token in Redis so tokens can be
**rotated** (the old jti is deleted when a new one is minted) and **revoked**
(logout / logout-all). Without this, a stolen refresh token granted access for
its full lifetime with no server-side way to invalidate it.

Keys: ``refresh:{user_id}:{jti}`` with a TTL equal to the token lifetime, so
expired entries self-clean.

Degrades open: if Redis is unreachable, ``is_active`` returns True so an
outage does not lock every user out (rotation/revocation simply stop working
until Redis recovers — the same availability-first tradeoff as rate limiting).
"""

from __future__ import annotations

import logging

from app.core.redis import redis_client

logger = logging.getLogger(__name__)

_PREFIX = "refresh"


def _key(user_id: str, jti: str) -> str:
    return f"{_PREFIX}:{user_id}:{jti}"


def _user_pattern(user_id: str) -> str:
    return f"{_PREFIX}:{user_id}:*"


async def register_refresh(user_id: str, jti: str, ttl_seconds: int) -> None:
    try:
        await redis_client.set(_key(user_id, jti), "1", ex=ttl_seconds)
    except Exception as e:
        logger.warning("token_store: could not register refresh jti: %s", e)


async def is_active(user_id: str, jti: str) -> bool:
    try:
        return bool(await redis_client.exists(_key(user_id, jti)))
    except Exception as e:
        logger.warning("token_store unavailable, treating refresh as active: %s", e)
        return True


async def revoke_refresh(user_id: str, jti: str) -> None:
    try:
        await redis_client.delete(_key(user_id, jti))
    except Exception as e:
        logger.warning("token_store: could not revoke refresh jti: %s", e)


async def revoke_all_refresh(user_id: str) -> int:
    """Revoke every active refresh token for a user (logout-all). Returns count."""
    deleted = 0
    try:
        async for key in redis_client.scan_iter(_user_pattern(user_id)):
            await redis_client.delete(key)
            deleted += 1
    except Exception as e:
        logger.warning("token_store: could not revoke all refresh tokens: %s", e)
    return deleted


# ---------------------------------------------------------------------------
# Session access-token store (jti blocklist model, inverted)
# ---------------------------------------------------------------------------
# Session access tokens minted by /auth/login, /auth/setup, /auth/demo-login,
# /auth/refresh and the tenant-switch surface carry a ``jti`` that is
# registered here with a TTL equal to the token lifetime. ``get_current_user``
# checks it on every request, so ``logout`` / ``logout-all`` / user deletion /
# role changes can kill access tokens immediately (not just refresh tokens).
# Degrades open on Redis failure (same availability-first posture).

_SESSION_PREFIX = "sess"


def _session_key(user_id: str, jti: str) -> str:
    return f"{_SESSION_PREFIX}:{user_id}:{jti}"


def _session_pattern(user_id: str) -> str:
    return f"{_SESSION_PREFIX}:{user_id}:*"


async def register_session(user_id: str, jti: str, ttl_seconds: int) -> None:
    try:
        await redis_client.set(
            _session_key(user_id, jti), "1", ex=max(int(ttl_seconds), 1)
        )
    except Exception as e:
        logger.warning("token_store: could not register session jti: %s", e)


async def is_session_active(user_id: str, jti: str) -> bool:
    try:
        return bool(await redis_client.exists(_session_key(user_id, jti)))
    except Exception as e:
        logger.warning("token_store unavailable, treating session as active: %s", e)
        return True


async def revoke_session(user_id: str, jti: str) -> None:
    try:
        await redis_client.delete(_session_key(user_id, jti))
    except Exception as e:
        logger.warning("token_store: could not revoke session jti: %s", e)


async def revoke_all_sessions(user_id: str) -> int:
    """Revoke every active session access token for a user. Returns count."""
    deleted = 0
    try:
        async for key in redis_client.scan_iter(_session_pattern(user_id)):
            await redis_client.delete(key)
            deleted += 1
    except Exception as e:
        logger.warning("token_store: could not revoke all sessions: %s", e)
    return deleted


async def revoke_everything(user_id: str) -> None:
    """Revoke all refresh tokens AND session access tokens for a user."""
    await revoke_all_refresh(user_id)
    await revoke_all_sessions(user_id)


# ---------------------------------------------------------------------------
# Invite-token single-use consumption
# ---------------------------------------------------------------------------
# Invite JWTs carry a ``jti``; the first successful registration consumes it
# (GETDEL semantics) so a leaked invite cannot onboard unlimited accounts.

_INVITE_PREFIX = "invite"


def _invite_key(jti: str) -> str:
    return f"{_INVITE_PREFIX}:{jti}"


async def register_invite(jti: str, ttl_seconds: int) -> None:
    try:
        await redis_client.set(_invite_key(jti), "1", ex=max(int(ttl_seconds), 1))
    except Exception as e:
        logger.warning("token_store: could not register invite jti: %s", e)


async def consume_invite(jti: str) -> bool:
    """Atomically consume an invite jti. True iff it was still valid.

    Degrades *closed* on Redis failure: a Redis outage must not turn a
    single-use invite into an unlimited-use one, so we refuse the invite
    rather than trust it. Multi-use is the worse failure for an onboarding
    path into health data.
    """
    try:
        return bool(await redis_client.delete(_invite_key(jti)))
    except Exception as e:
        logger.warning("token_store unavailable, refusing invite (fail-closed): %s", e)
        return False


# ---------------------------------------------------------------------------
# OAuth2 api-token revocation (best-effort jti blocklist)
# ---------------------------------------------------------------------------
# Access tokens are stateless JWTs, so revocation works by recording the token's
# ``jti`` in Redis with a TTL equal to the token's remaining lifetime. The
# facade auth dependency (``get_api_principal``) checks this on every request.
# Degrades open: if Redis is unreachable, a revoked token is treated as active
# (matches the availability-first posture of refresh-token revocation).

_API_PREFIX = "api_revoked"


def _api_key(jti: str) -> str:
    return f"{_API_PREFIX}:{jti}"


async def revoke_api_jti(jti: str, ttl_seconds: int) -> None:
    """Record an api-token ``jti`` as revoked for ``ttl_seconds``."""
    try:
        ttl = max(int(ttl_seconds), 1)
        await redis_client.set(_api_key(jti), "1", ex=ttl)
    except Exception as e:
        logger.warning("token_store: could not revoke api jti: %s", e)


async def is_api_revoked(jti: str) -> bool:
    try:
        return bool(await redis_client.exists(_api_key(jti)))
    except Exception as e:
        logger.warning("token_store unavailable, skipping api revocation check: %s", e)
        return False
