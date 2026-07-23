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
