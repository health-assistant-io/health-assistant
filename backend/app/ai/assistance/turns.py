"""Chat turn single-flight lock + SSE error classification.

Shared by the ``/ai-assistance`` endpoints and the health-assistant bridge
(2026-10-11, R7 Phase 2): both surfaces stream chat turns and must agree on
(a) one streaming turn per session at a time and (b) the exact terminal
``flow_failed`` frame shape, so PWA and mobile turns on the same session
interlock correctly and clients see one error contract.
"""

import json
import logging
import uuid

from app.ai.flow_events import flow_failed_event
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

TURN_LOCK_MESSAGE = "A reply is already streaming for this chat session."

#: Lock key TTL (seconds) — bounds every leak class (a returned-but-never-
#: iterated StreamingResponse whose generator body never runs, a crashed
#: handler, a lost ``finally``): 15 min ~ 10x a worst-case turn.
TURN_LOCK_TTL_SECONDS = 900

#: Atomic compare-and-delete — only the turn whose token is still stored may
#: remove the key; a late ``finally`` from a superseded turn must not unlock
#: a newer one.
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


class TurnConflictError(Exception):
    """Another turn is already streaming for the session (HTTP 409 upstream)."""


def _turn_key(session_id: uuid.UUID) -> str:
    return f"turn:{session_id}"


async def _compare_and_delete(key: str, token: str) -> int:
    """Run :data:`_RELEASE_SCRIPT` atomically in Redis.

    Kept as a small module-level callable so tests can inject a fake
    release path (the FakeRedis pattern has no Lua engine) by
    monkeypatching this binding.
    """
    return await redis_client.eval(_RELEASE_SCRIPT, 1, key, token)


async def acquire_turn(session_id: uuid.UUID) -> str | None:
    """Acquire the per-session single-flight turn lock (one streaming turn
    per session at a time — per session, not per branch, so two branches of
    one session never stream concurrently).

    Runs ``SET turn:{session_id} {token} NX EX 900`` with a fresh owner
    token (``uuid4`` hex) and returns it; the caller MUST pass the same
    token to :func:`release_turn`.

    Returns:
        The owner token, or ``None`` when Redis was unreachable and the
        turn was allowed anyway (degrade-open — nothing to release).

    Raises:
        TurnConflictError: another turn currently holds the session's lock.

    Multi-worker correctness: the lock lives in Redis (``app.core.redis.
    redis_client``), so every uvicorn worker and replica interlocks on the
    same key — the previous in-process set was silently per-worker.

    TTL rationale: 900 s ~ 10x a worst-case turn (~90 s of tool calls plus
    streaming), so every leak class — a returned-but-never-iterated
    ``StreamingResponse`` whose generator (and ``finally``) never runs, a
    crashed handler, a lost cleanup — self-heals when the key expires
    instead of persisting until restart.

    Degrade-open: on ANY Redis error the turn is ALLOWED and a loud warning
    is logged (family precedent — ``app/core/rate_limit.py``: availability
    over protection). A Redis outage is a lost interlock, never a chat
    outage; keys written before the outage are reclaimed by the TTL.
    """
    token = uuid.uuid4().hex
    try:
        locked = await redis_client.set(
            _turn_key(session_id), token, nx=True, ex=TURN_LOCK_TTL_SECONDS
        )
    except Exception:
        logger.warning(
            "Turn lock acquire degraded open for session %s (Redis unreachable)",
            session_id,
            exc_info=True,
        )
        return None
    if not locked:
        raise TurnConflictError(TURN_LOCK_MESSAGE)
    return token


async def release_turn(session_id: uuid.UUID, token: str | None) -> None:
    """Release the session's turn lock, but only if ``token`` still owns it.

    Compare-and-delete via :func:`_compare_and_delete` (Lua, atomic in
    Redis): a late ``finally`` from a superseded turn — e.g. its key
    expired and a newer turn re-locked — deletes nothing. ``token=None``
    (degrade-open acquire wrote no key) is a no-op.

    Degrades open on any Redis error (loud warning): the 900 s TTL reclaims
    the key, so a failed release never wedges the session.
    """
    if token is None:
        return
    try:
        await _compare_and_delete(_turn_key(session_id), token)
    except Exception:
        logger.warning(
            "Turn lock release degraded open for session %s "
            "(Redis unreachable; TTL will reclaim the key)",
            session_id,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Streaming error classification
# ---------------------------------------------------------------------------
# The chat stream endpoints catch exceptions and forward them to the client as
# an SSE ``{"error": ..., "error_type": ...}`` payload. We never forward raw
# provider SDK strings (e.g. OpenAI's "Connection error.") because they leak
# the upstream vendor and aren't localized. Instead we classify the exception
# into a short, stable code that the frontend maps to a translated message.
# ValueError is the channel for *intentional* client-facing guard violations
# (ownership/no-tasks/pending-task checks); its message is already user-facing
# and is forwarded verbatim with error_type="guard".

_STREAM_ERROR_TYPES = (
    "connection",
    "auth",
    "rate_limit",
    "timeout",
    "generic",
    "guard",
)

# Provider-SDK exception classes (openai v1) mapped to stable error codes.
# Matched by class NAME via the MRO instead of isinstance so this module —
# like every non-factory module (ADR-0008) — never imports a provider SDK.
# Order matters: APITimeoutError subclasses APIConnectionError.
# ``model_config`` (provider HTTP 400) means the request/model shape was
# rejected — almost always a model/task mismatch (e.g. a reasoning model
# that cannot run chat tools). The frontend maps it to actionable guidance
# (check the AI Configuration task assignment).
_PROVIDER_ERROR_NAMES: tuple[tuple[str, str], ...] = (
    ("APITimeoutError", "timeout"),
    ("APIConnectionError", "connection"),
    ("AuthenticationError", "auth"),
    ("RateLimitError", "rate_limit"),
    ("BadRequestError", "model_config"),
)


def classify_stream_error(exc: Exception) -> tuple[str, str]:
    """Return ``(error_type, user_message)`` for a streaming exception.

    The ``error_type`` is a stable lowercase code the frontend localizes.
    ``user_message`` is non-empty only for soft guard messages (ValueError);
    for provider errors it is empty so the frontend MUST localize from the
    code (no raw SDK text leaves the server).
    """
    if isinstance(exc, ValueError):
        # Intentional guard violation — message is already user-facing.
        return ("guard", str(exc))

    # Map known LLM/provider errors to non-leaky codes by walking the MRO —
    # LangChain passes the provider SDK's exception classes through verbatim.
    names = {cls.__name__ for cls in type(exc).__mro__}
    for name, code in _PROVIDER_ERROR_NAMES:
        if name in names:
            return (code, "")
    return ("generic", "")


def stream_error_frame(exc: Exception) -> str:
    """Build the terminal SSE ``flow_failed`` frame for a streaming exception.

    Carries the §5 payload: the classified ``code`` (the stable error_type
    the frontend localizes), ``message`` (non-empty only for soft guard
    violations), and ``retryable`` (true for the transient provider
    classes)."""
    error_type, user_message = classify_stream_error(exc)
    retryable = error_type in ("connection", "timeout", "rate_limit")
    frame = flow_failed_event(retryable, code=error_type, message=user_message)
    return f"data: {json.dumps(frame)}\n\n"
