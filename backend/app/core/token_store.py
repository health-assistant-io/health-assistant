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
        await redis_client.set(_session_key(user_id, jti), "1", ex=max(int(ttl_seconds), 1))
    except Exception as e:
        logger.warning("token_store: could not register session jti: %s", e)


async def is_session_active(user_id: str, jti: str, family_id: str | None = None) -> bool:
    try:
        return bool(await redis_client.exists(_session_key(user_id, jti)))
    except Exception as e:
        logger.warning("token_store unavailable, checking auth_sessions: %s", e)
    return await _family_is_live(user_id, family_id)


async def _family_is_live(user_id: str, family_id: str | None) -> bool:
    """Authoritative DB fallback for a Redis outage.

    ``auth_sessions`` is the family record (the Redis jti store is only the
    hot revocation layer), so a revoked row must keep a logged-out access
    token dead even while Redis is unreachable. Fails open only when the
    database is also unavailable (availability-first, same posture as
    before) or when the token carries no family id (non-family token); a
    missing or revoked row is inactive.
    """
    if not family_id:
        logger.warning(
            "token_store unavailable and token has no family id — treating session as active"
        )
        return True
    try:
        from datetime import UTC, datetime
        from uuid import UUID

        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.auth_session_model import AuthSessionModel

        async with AsyncSessionLocal() as session:
            row = await session.scalar(
                select(AuthSessionModel).where(
                    AuthSessionModel.id == UUID(str(family_id)),
                    AuthSessionModel.user_id == UUID(str(user_id)),
                )
            )
        if row is None:
            return False
        now = datetime.now(UTC)
        return row.revoked_at is None and row.expires_at > now and row.absolute_expires_at > now
    except Exception as e:
        logger.warning("auth_sessions fallback unavailable, treating session as active: %s", e)
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
# MFA challenge-token single-use consumption (plan 16 H5)
# ---------------------------------------------------------------------------
# The login MFA challenge mints a short-lived ``mfa_challenge`` JWT whose
# jti is registered here. The FIRST SUCCESSFUL /auth/mfa/verify consumes it
# (GETDEL semantics — a replayed challenge cannot mint a second session);
# failed code attempts deliberately leave it alive (the user may retype
# within the 5-minute TTL) while §7 lockout counting bounds the retries.
# Fails closed on Redis failure, like invites: an outage must not turn a
# single-use challenge into an unlimited-use one.

_MFA_PREFIX = "mfa"


def _mfa_key(user_id: str, jti: str) -> str:
    return f"{_MFA_PREFIX}:{user_id}:{jti}"


async def register_mfa_challenge(user_id: str, jti: str, ttl_seconds: int) -> None:
    try:
        await redis_client.set(_mfa_key(user_id, jti), "1", ex=max(int(ttl_seconds), 1))
    except Exception as e:
        logger.warning("token_store: could not register mfa challenge jti: %s", e)


async def is_mfa_challenge_active(user_id: str, jti: str) -> bool:
    try:
        return bool(await redis_client.exists(_mfa_key(user_id, jti)))
    except Exception as e:
        logger.warning("token_store unavailable, refusing mfa challenge (fail-closed): %s", e)
        return False


async def consume_mfa_challenge(user_id: str, jti: str) -> bool:
    """Atomically consume a challenge jti. True iff it was still valid."""
    try:
        return bool(await redis_client.delete(_mfa_key(user_id, jti)))
    except Exception as e:
        logger.warning("token_store unavailable, refusing mfa challenge (fail-closed): %s", e)
        return False


# ---------------------------------------------------------------------------
# Session-policy window (per family) + family→jti binding (ADR 0054 Phase 3)
# ---------------------------------------------------------------------------
# The tenant security policy (idle / max-age) needs a per-FAMILY activity
# anchor that survives refresh rotation (the family id is stable; the jti
# rolls). ``sesspol:{fid}`` is a tiny {start, seen} hash: ``start`` is the
# family's session anchor (HSETNX — rotations never move it), ``seen`` the
# last verified request. ``sessfam:{fid}`` binds the family to its current
# access jti so the concurrent-session eviction can kill the evicted
# device's bearer immediately, not just its refresh chain.
#
# Key TTL is a long constant (well past the §8 30-day family cap) so the
# window facts outlive any idle gap: a denied session stays denied. A
# missing entry (Redis flush, policy enabled later) re-anchors at now —
# the same degrade-open posture as the jti revocation layer above; the
# refresh path additionally enforces max-age from the authoritative
# ``auth_sessions`` row.

_SESSION_POLICY_PREFIX = "sesspol"
_SESSION_FAMILY_PREFIX = "sessfam"

_SESSION_POLICY_TTL_SECONDS = 60 * 60 * 24 * 45  # 45 days — past the family cap


def _session_policy_key(family_id: str) -> str:
    return f"{_SESSION_POLICY_PREFIX}:{family_id}"


def _session_family_key(family_id: str) -> str:
    return f"{_SESSION_FAMILY_PREFIX}:{family_id}"


async def session_window_anchor(family_id: str) -> None:
    """Anchor a freshly minted (or first-seen) family window at *now*.

    ``start`` is anchored HSETNX-only so refresh rotations keep the original
    session start; ``seen`` is always moved to now (the mint itself is
    activity).
    """
    try:
        import time

        now = str(int(time.time()))
        await redis_client.hsetnx(_session_policy_key(family_id), "start", now)
        await redis_client.hset(_session_policy_key(family_id), mapping={"seen": now})
        await redis_client.expire(_session_policy_key(family_id), _SESSION_POLICY_TTL_SECONDS)
    except Exception as e:
        logger.warning("token_store: could not anchor session window: %s", e)


async def session_window_check(
    family_id: str, *, idle_seconds: int | None = None, max_seconds: int | None = None
) -> str | None:
    """The window verdict: ``None`` = allow, ``"idle"`` / ``"max_age"`` = deny.

    Missing entry → re-anchor at now and allow (degrade-open, see the
    module note). Every allowed check moves ``seen`` forward (the request
    itself is activity) — one hash read + one write per request while a
    window policy is active; no Redis traffic at all when the tenant sets
    neither window.
    """
    if not idle_seconds and not max_seconds:
        return None
    try:
        import time

        meta = await redis_client.hgetall(_session_policy_key(family_id))
        now = int(time.time())
        if not meta:
            await session_window_anchor(family_id)
            return None
        start = int(meta.get("start") or now)
        seen = int(meta.get("seen") or now)
        if max_seconds is not None and now - start > max_seconds:
            return "max_age"
        if idle_seconds is not None and now - seen > idle_seconds:
            return "idle"
        await redis_client.hset(_session_policy_key(family_id), mapping={"seen": str(now)})
        await redis_client.expire(_session_policy_key(family_id), _SESSION_POLICY_TTL_SECONDS)
        return None
    except Exception as e:
        logger.warning("token_store unavailable, allowing session window: %s", e)
        return None


async def session_window_drop(family_id: str) -> None:
    """Forget a family's window (revocation hygiene; harmless if absent)."""
    try:
        await redis_client.delete(_session_policy_key(family_id))
    except Exception as e:
        logger.warning("token_store: could not drop session window: %s", e)


async def bind_session_family(family_id: str, jti: str) -> None:
    """Remember the family's current access jti (concurrent-limit eviction)."""
    try:
        await redis_client.set(_session_family_key(family_id), jti, ex=_SESSION_POLICY_TTL_SECONDS)
    except Exception as e:
        logger.warning("token_store: could not bind session family: %s", e)


async def session_family_jti(family_id: str) -> str | None:
    """The bound access jti of a family, or ``None`` (unbound / Redis out)."""
    try:
        return await redis_client.get(_session_family_key(family_id))
    except Exception as e:
        logger.warning("token_store: could not read session family binding: %s", e)
        return None


async def unbind_session_family(family_id: str) -> None:
    """Drop the family binding (the jti itself is revoked by the caller)."""
    try:
        await redis_client.delete(_session_family_key(family_id))
    except Exception as e:
        logger.warning("token_store: could not unbind session family: %s", e)


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


# ---------------------------------------------------------------------------
# OIDC SSO flow state (state / nonce / PKCE verifier — Phase 12B)
# ---------------------------------------------------------------------------
# One key per authorization attempt, TTL-bounded (10 min) and consumed
# atomically on the callback: ``GETDEL`` semantics make a replayed state
# fail-closed (the second use finds nothing). Unlike the revocation layers
# above this MUST NOT degrade open — an unreadable flow store fails the
# callback (the caller retries the sign-in), never completes it unverified.

_SSO_STATE_PREFIX = "sso_state"
_SSO_STATE_TTL_SECONDS = 10 * 60


def _sso_state_key(state: str) -> str:
    return f"{_SSO_STATE_PREFIX}:{state}"


async def register_sso_state(
    state: str, payload: str, ttl_seconds: int = _SSO_STATE_TTL_SECONDS
) -> None:
    """Store one authorization attempt's state payload (JSON)."""
    await redis_client.set(_sso_state_key(state), payload, ex=max(int(ttl_seconds), 1))


async def peek_sso_state(state: str) -> str | None:
    """Read one flow payload **without** consuming it (authorization first).

    Used by the link flow: the actor must prove they own the intent before
    it is burned. Never degrades open.
    """
    try:
        return await redis_client.get(_sso_state_key(state))
    except Exception as e:
        logger.warning("sso state unavailable (fail-closed): %s", e)
        return None


async def drop_sso_state(state: str) -> None:
    """Consume one flow payload after a successful use (single-use law)."""
    try:
        await redis_client.delete(_sso_state_key(state))
    except Exception as e:
        logger.warning("sso state drop failed (expires via TTL): %s", e)


async def consume_sso_state(state: str) -> str | None:
    """Pop one authorization attempt's payload (single-use) or ``None``.

    A missing entry covers expiry, an unknown state, and a **replay** —
    the first consume deletes the key. Never degrades open: a Redis
    outage fails the callback, it does not skip the check.
    """
    try:
        return await redis_client.getdel(_sso_state_key(state))
    except Exception as e:
        logger.warning("sso state unavailable (fail-closed): %s", e)
        return None


# ---------------------------------------------------------------------------
# Session activity ticks (device list ``last_seen_at`` throttle — Phase 12C1)
# ---------------------------------------------------------------------------
# Pure bookkeeping: ``SET NX EX`` succeeding means "no stamp in the last
# ``ttl`` seconds", so the DB write happens at most once per window. A Redis
# outage answers True (stamp away — the row is only informational) so the
# device list stays truthful when Redis is flaky; the caller still bounds
# the write rate with the DB-side update.

_SESSION_SEEN_PREFIX = "sessseen"


def _session_seen_key(family_id: str) -> str:
    return f"{_SESSION_SEEN_PREFIX}:{family_id}"


async def claim_seen_tick(family_id: str, ttl_seconds: int) -> bool:
    """Reserve the right to write one ``last_seen_at`` stamp."""
    try:
        return bool(
            await redis_client.set(
                _session_seen_key(family_id), "1", ex=max(int(ttl_seconds), 1), nx=True
            )
        )
    except TypeError:
        # Test doubles without the ``nx`` keyword: fall back to a plain
        # exists/set pair (the throttle only loses precision).
        try:
            if await redis_client.exists(_session_seen_key(family_id)):
                return False
            await redis_client.set(_session_seen_key(family_id), "1", ex=max(int(ttl_seconds), 1))
            return True
        except Exception as e:
            logger.warning("seen tick unavailable, stamping: %s", e)
            return True
    except Exception as e:
        logger.warning("seen tick unavailable, stamping: %s", e)
        return True


# ---------------------------------------------------------------------------
# Step-up proofs (session-bound re-authentication — Phase 12C1)
# ---------------------------------------------------------------------------
# ``stepup:{fid}:{action}`` holds a JSON proof, TTL-bounded (10 min). The
# proof is a **window**, not a ticket: it records that this family
# re-authenticated as its owner within the last ten minutes, for this
# action. Reuse inside the window is intended (one re-authentication can
# cover the retry of a failed request); crossing the window, the action or
# the family is not. The payload also carries ``issued_at`` so expiry is
# decided by the caller without trusting Redis clock behaviour
# (deterministic in tests).
# This is an enforcement control, so it NEVER degrades open: an unreadable
# store answers "no proof" and the action is refused.

_STEP_UP_PREFIX = "stepup"


def _step_up_key(family_id: str, action: str) -> str:
    return f"{_STEP_UP_PREFIX}:{family_id}:{action}"


async def register_step_up(family_id: str, action: str, proof: str, ttl_seconds: int) -> None:
    """Record one session-bound step-up proof for one action."""
    try:
        await redis_client.set(_step_up_key(family_id, action), proof, ex=max(int(ttl_seconds), 1))
    except Exception as e:
        logger.warning("step-up store unavailable (proof not kept): %s", e)


async def read_step_up(family_id: str, action: str) -> str | None:
    """Read the proof for ``(family, action)`` or ``None`` (absent/outage)."""
    try:
        return await redis_client.get(_step_up_key(family_id, action))
    except Exception as e:
        logger.warning("step-up store unavailable (fail-closed): %s", e)
        return None


# ---------------------------------------------------------------------------
# WebAuthn ceremony challenges (program 12C2)
# ---------------------------------------------------------------------------
# One challenge per ceremony, keyed by its purpose (registration by user,
# assertion by the challenge itself, step-up by family+action). The value is
# JSON ``{challenge, user_id, action?}``. ``consume`` uses DELETE-returning
# semantics, so a replay — including a replay of a malformed first attempt —
# fails closed. An unreadable store answers "no challenge".


async def register_webauthn_challenge(key: str, payload: str, ttl_seconds: int) -> None:
    """Store one ceremony challenge payload for its short lifetime."""
    try:
        await redis_client.set(key, payload, ex=max(int(ttl_seconds), 1))
    except Exception as e:
        logger.warning("webauthn challenge store unavailable (challenge not kept): %s", e)


async def consume_webauthn_challenge(key: str) -> str | None:
    """Atomically read + delete a ceremony challenge (single-use)."""
    try:
        return await redis_client.getdel(key)
    except Exception as e:
        logger.warning("webauthn challenge store unavailable (fail-closed): %s", e)
        return None
