"""Redis-backed rate limiting (audit A2).

Fixed-window counters in Redis so the limit holds across all uvicorn/celery
workers sharing the broker. Used to protect authentication endpoints against
brute-force / credential-stuffing / enumeration.

Design notes:
- Degrades open: if Redis is unreachable, the request proceeds (rate limiting
  is defence-in-depth, not an availability gate — mirroring the
  ``DATABASE_AVAILABLE`` philosophy). A closed failure mode would let a Redis
  outage lock every user out.
- Keyed by client IP (+ optional identifier). Proxy trust is EXPLICIT
  (audit 2026-08 AUTH-H1): ``TRUSTED_PROXY_COUNT`` declares how many
  rightmost ``X-Forwarded-For`` hops the ingress stack appends; only that
  suffix is honored. With no configured proxies the header is ignored and
  the socket peer is used — a spoofable header can no longer mint fresh
  rate-limit buckets per request.
- Returns a FastAPI dependency suitable for ``Depends(...)``.
"""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException, Request, status

from app.core.config import get_settings
from app.core.redis import redis_client

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    settings = get_settings()
    trusted = settings.TRUSTED_PROXY_COUNT
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and trusted > 0:
        # The ingress appends the real client then (optionally) further
        # proxies. The RIGHTMOST ``trusted`` entries are ours; anything to
        # the left of that is client-supplied and spoofable.
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if len(hops) > trusted:
            return hops[-trusted]
        return hops[0] if hops else "unknown"
    return request.client.host if request.client else "unknown"


def _limiter_dep(prefix: str, max_requests: int, window: int):
    """Build a FastAPI dependency that enforces a fixed-window limit."""

    async def _check(request: Request):
        ip = _client_ip(request)
        bucket = int(time.time()) // window
        key = f"rl:{prefix}:{ip}:{bucket}"
        try:
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, window)
        except Exception as e:  # Redis unreachable — degrade open.
            logger.warning("Rate-limit backend unavailable, allowing request: %s", e)
            return
        if count > max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(window)},
            )

    return _check


def rate_limit(prefix: str, max_requests: int, window: int = 60):
    """``Depends(rate_limit("login", 10))`` style dependency factory.

    ``max_requests`` per ``window`` seconds per client IP.
    """
    return _limiter_dep(prefix, max_requests, window)


def _integration_limiter_dep(prefix: str, max_requests: int, window: int):
    """Build a FastAPI dependency that enforces a fixed-window limit keyed by
    the ``integration_id`` path parameter (in addition to the per-IP limit).

    Used by the unauthenticated webhook + API-proxy routes — the
    ``integration_id`` is the natural unit for "how hard is *this* instance
    being driven", and a per-instance cap survives a distributed flood that a
    per-IP cap alone wouldn't catch.
    """

    async def _check(integration_id: str):
        bucket = int(time.time()) // window
        key = f"rl:{prefix}:integration:{integration_id}:{bucket}"
        try:
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, window)
        except Exception as e:  # Redis unreachable — degrade open.
            logger.warning("Rate-limit backend unavailable, allowing request: %s", e)
            return
        if count > max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests for this integration. Please try again later.",
                headers={"Retry-After": str(window)},
            )

    return _check


def rate_limit_integration(prefix: str, max_requests: int, window: int = 60):
    """Per-integration rate limit keyed on the ``integration_id`` path param.

    Pair with :func:`rate_limit` (per-IP) on the same route for both
    distributed- and targeted-flood protection.
    """
    return _integration_limiter_dep(prefix, max_requests, window)
