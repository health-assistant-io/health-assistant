"""Redis-backed rate limiting (audit A2).

Fixed-window counters in Redis so the limit holds across all uvicorn/celery
workers sharing the broker. Used to protect authentication endpoints against
brute-force / credential-stuffing / enumeration.

Design notes:
- Degrades open: if Redis is unreachable, the request proceeds (rate limiting
  is defence-in-depth, not an availability gate — mirroring the
  ``DATABASE_AVAILABLE`` philosophy). A closed failure mode would let a Redis
  outage lock every user out.
- Keyed by client IP (+ optional identifier). The IP is read from
  ``X-Forwarded-For`` (first hop) when a trusted proxy set it, else the peer.
- Returns a FastAPI dependency suitable for ``Depends(...)``.
"""
from __future__ import annotations

import logging
import time

from fastapi import HTTPException, Request, status

from app.core.redis import redis_client

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
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
