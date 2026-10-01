"""Redis-backed rate limiting (audit A2, identity-auth §7/§16).

Fixed-window counters in Redis so the limit holds across all uvicorn/celery
workers sharing the broker. Used to protect authentication endpoints against
brute-force / credential-stuffing / enumeration.

Design notes:
- Degrades open: if Redis is unreachable, the request proceeds (rate limiting
  is defence-in-depth, not an availability gate — mirroring the
  ``DATABASE_AVAILABLE`` philosophy). A closed failure mode would let a Redis
  outage lock every user out.
- Keyed by client IP (+ optional identifier) with a per-account (email)
  companion bucket on login/register (§7). Proxy trust is EXPLICIT
  (audit 2026-08 AUTH-H1): ``HA_TRUSTED_PROXY_COUNT`` declares how many
  rightmost ``X-Forwarded-For`` hops the ingress stack appends; only that
  suffix is honored. With no configured proxies the header is ignored and
  the socket peer is used — a spoofable header can no longer mint fresh
  rate-limit buckets per request.
- §16 per-bucket ceilings: ``HA_RATELIMIT_AUTH`` / ``HA_RATELIMIT_AUTH_EMAIL``
  / ``HA_RATELIMIT_AI`` / ``HA_RATELIMIT_MCP`` / ``HA_RATELIMIT_DEFAULT``
  (career reference, requests per minute). When set, a ceiling overrides the
  route's built-in default; ``0`` disables the bucket; unset ⇒ route defaults.
- Returns a FastAPI dependency suitable for ``Depends(...)``.
"""

from __future__ import annotations

import logging
import time

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.core.redis import redis_client
from app.core.security import get_current_user

logger = logging.getLogger(__name__)

BUCKETS = ("AUTH", "AUTH_EMAIL", "AI", "MCP", "DEFAULT")


def _ceiling(bucket: str, fallback: int) -> int:
    """Resolve the effective per-minute ceiling for a §16 bucket.

    ``fallback`` is the route's built-in default (pre-§16 behavior). The
    env knob wins when set; 0/negative disables enforcement for the bucket.
    """
    settings = get_settings()
    if not getattr(settings, "HA_RATELIMIT_ENABLED", True):
        return 0
    value = getattr(settings, f"HA_RATELIMIT_{bucket.upper()}", None)
    return int(value) if value is not None else int(fallback)


def _client_ip(request: Request) -> str:
    settings = get_settings()
    trusted = settings.HA_TRUSTED_PROXY_COUNT
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


async def _consume(key: str, max_requests: int, window: int, detail: str) -> None:
    """One fixed-window counter tick; raises 429 (with Retry-After) over budget."""
    if max_requests <= 0:
        return  # bucket disabled
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
            detail=detail,
            headers={"Retry-After": str(window)},
        )


def _limiter_dep(prefix: str, max_requests: int, window: int, bucket: str):
    """Build a FastAPI dependency that enforces a fixed-window limit."""

    async def _check(request: Request):
        limit = _ceiling(bucket, max_requests)
        ip = _client_ip(request)
        window_bucket = int(time.time()) // window
        await _consume(
            f"rl:{prefix}:{ip}:{window_bucket}",
            limit,
            window,
            "Too many requests. Please try again later.",
        )

    return _check


def rate_limit(prefix: str, max_requests: int, window: int = 60, bucket: str = "default"):
    """``Depends(rate_limit("login", 10, bucket="auth"))`` style dependency factory.

    ``max_requests`` per ``window`` seconds per client IP (the route's
    default; overridden per §16 when ``HA_RATELIMIT_<BUCKET>`` is set).
    """
    return _limiter_dep(prefix, max_requests, window, bucket)


async def check_account_limit(
    prefix: str,
    account: str,
    *,
    max_requests: int = 30,
    window: int = 60,
    bucket: str = "auth_email",
    detail: str = "Too many requests. Please try again later.",
) -> None:
    """§7 per-account (email) bucket — imperative companion to the per-IP
    dependency, called from the login/register/refresh handlers where the
    account identity is only known after reading the body.

    Keyed on the lowercased account so one mailbox cannot be brute-forced
    from a botnet (each IP gets its own per-IP budget; the account budget
    is shared across all of them).
    """
    account_key = str(account or "").strip().lower() or "anonymous"
    limit = _ceiling(bucket, max_requests)
    window_bucket = int(time.time()) // window
    await _consume(f"rl:{prefix}:acct:{account_key}:{window_bucket}", limit, window, detail)


def _integration_limiter_dep(prefix: str, max_requests: int, window: int, bucket: str):
    """Build a FastAPI dependency that enforces a fixed-window limit keyed by
    the ``integration_id`` path parameter (in addition to the per-IP limit).

    Used by the unauthenticated webhook + API-proxy routes — the
    ``integration_id`` is the natural unit for "how hard is *this* instance
    being driven", and a per-instance cap survives a distributed flood that a
    per-IP cap alone wouldn't catch.
    """

    async def _check(integration_id: str):
        limit = _ceiling(bucket, max_requests)
        window_bucket = int(time.time()) // window
        await _consume(
            f"rl:{prefix}:integration:{integration_id}:{window_bucket}",
            limit,
            window,
            "Too many requests for this integration. Please try again later.",
        )

    return _check


def rate_limit_integration(
    prefix: str, max_requests: int, window: int = 60, bucket: str = "default"
):
    """Per-integration rate limit keyed on the ``integration_id`` path param.

    Pair with :func:`rate_limit` (per-IP) on the same route for both
    distributed- and targeted-flood protection.
    """
    return _integration_limiter_dep(prefix, max_requests, window, bucket)


def rate_limit_user(prefix: str, max_requests: int, window: int = 60, bucket: str = "ai"):
    """Per-user rate limit for authenticated routes (audit 2026-09-11 S-4).

    Keyed on the ``user_id`` of the ``get_current_user``-resolved caller —
    AI endpoints cost real money per call, so a per-identity cap is the
    natural unit (a distributed flood behind one NAT still can't burn more
    than the per-user budget). Degrades open exactly like the IP limiter.
    """

    async def _check(
        request: Request,
        current_user=Depends(get_current_user),
    ):
        limit = _ceiling(bucket, max_requests)
        user_id = getattr(current_user, "user_id", None) or _client_ip(request)
        window_bucket = int(time.time()) // window
        await _consume(
            f"rl:{prefix}:u:{user_id}:{window_bucket}",
            limit,
            window,
            "Too many requests. Please try again later.",
        )

    return _check
