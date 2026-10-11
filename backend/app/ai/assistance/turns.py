"""Chat turn single-flight lock + SSE error classification.

Shared by the ``/ai-assistance`` endpoints and the health-assistant bridge
(2026-10-11, R7 Phase 2): both surfaces stream chat turns and must agree on
(a) one streaming turn per session at a time and (b) the exact terminal
``flow_failed`` frame shape, so PWA and mobile turns on the same session
interlock correctly and clients see one error contract.
"""

import json
import uuid

from app.ai.flow_events import flow_failed_event

#: Per-session single-flight turn lock (plan 13 H2, study FIFO reference →
#: career's rejection shape): one streaming turn per session at a time — the
#: lock is per-SESSION, not per-branch, so two branches of one session never
#: stream concurrently (branch pointer writes and checkpoint threads would
#: otherwise interleave). Acquired before the StreamingResponse is built and
#: released when the stream generator closes.
_ACTIVE_TURNS: set[uuid.UUID] = set()

TURN_LOCK_MESSAGE = "A reply is already streaming for this chat session."


class TurnConflictError(Exception):
    """Another turn is already streaming for the session (HTTP 409 upstream)."""


def acquire_turn(session_id: uuid.UUID) -> None:
    if session_id in _ACTIVE_TURNS:
        raise TurnConflictError(TURN_LOCK_MESSAGE)
    _ACTIVE_TURNS.add(session_id)


def release_turn(session_id: uuid.UUID) -> None:
    _ACTIVE_TURNS.discard(session_id)


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
