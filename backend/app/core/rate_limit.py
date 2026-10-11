"""Rate limiting (audit A2, identity-auth §7/§16) — kit-backed (plan 21).

Fixed-window counters so the limit holds across all uvicorn/celery
workers sharing the broker. Used to protect authentication endpoints
against brute-force / credential-stuffing / enumeration.

Design notes:
- The counter backend is the auth-kit's (`nx_auth.ratelimit` — plan 21's
  one family limiter): a `RedisCounter` over this app's `redis_client`
  keeps the exact ``rl:*`` key shape and the **degrade-open** rule (if
  Redis is unreachable, the request proceeds — rate limiting is
  defence-in-depth, not an availability gate; a closed failure mode
  would let a Redis outage lock every user out).
- The public dependency names (``rate_limit``, ``rate_limit_user``,
  ``rate_limit_integration``, ``check_account_limit``) stay as thin
  wrappers over the kit's ``RateLimitPolicy.hit`` so the 8+ call sites
  and their static wiring tests change minimally (plan 21 Phase 4
  ruling: wrappers, no call-site migration).
- Keyed by client IP (+ optional identifier) with a per-account (email)
  companion bucket on login/register (§7). Proxy trust is EXPLICIT
  (audit 2026-08 AUTH-H1): ``HA_TRUSTED_PROXY_COUNT`` declares how many
  rightmost ``X-Forwarded-For`` hops the ingress stack appends — resolved
  through the kit's one ``client_ip`` (D3).
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
from nx_auth.ratelimit import RateLimitPolicy, RedisCounter, client_ip

from app.core.config import get_settings
from app.core.redis import redis_client
from app.core.security import get_current_user

logger = logging.getLogger(__name__)

# The frozen five-bucket family set lives in the kit
# (`nx_auth.ratelimit.BUCKETS`) — product copies are drift-gated.


class _LazyRedisCounter:
    """Bridge to the kit's RedisCounter, reading this module's
    ``redis_client`` binding per call (tests swap it with a fake)."""

    async def incr(self, key: str, window: int) -> int:
        return await RedisCounter(redis_client).incr(key, window)


#: The family limiter policy (plan 21): ceilings stay product-side (the
#: ``HA_RATELIMIT_*`` §16 semantics — None ⇒ route default), the counter
#: is Redis so windows hold across processes, and ``hit`` enforces the
#: degrade-open rule on any backend failure.
_LIMITER = RateLimitPolicy(ceiling=lambda bucket: None, counter=_LazyRedisCounter())


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
    return client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        settings.HA_TRUSTED_PROXY_COUNT,
    )


async def _consume(key: str, max_requests: int, window: int, detail: str) -> None:
    """One fixed-window counter tick; raises 429 (with Retry-After) over budget.

    The kit's ``hit`` owns the counter mechanics (INCR+EXPIRE, keys
    verbatim — the ``rl:*`` shape predates plan 21 and survives it) and
    the degrade-open rule (backend failure ⇒ allow + log).
    """
    if max_requests <= 0:
        return  # bucket disabled
    retry_after = await _LIMITER.hit(key, limit=max_requests, window=window)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": str(retry_after)},
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


async def enforce_integration_rate_limit(
    prefix: str,
    integration_id: str,
    *,
    max_requests: int = 60,
    window: int = 60,
    bucket: str = "default",
    detail: str = "Too many requests for this integration. Please try again later.",
) -> None:
    """Imperative per-integration bucket — companion to
    :func:`rate_limit_integration` for handlers that only know the
    integration row after resolving auth (e.g. the bridge API proxy's AI
    budget, enforced between signature verification and dispatch).

    Same key shape as the dependency form, so both share one budget per
    prefix. Redis-backed, degrade-open, 429 + ``Retry-After`` — all
    inherited from :func:`_consume`.
    """
    limit = _ceiling(bucket, max_requests)
    window_bucket = int(time.time()) // window
    await _consume(
        f"rl:{prefix}:integration:{integration_id}:{window_bucket}", limit, window, detail
    )


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
