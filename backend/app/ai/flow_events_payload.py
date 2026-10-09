"""Health's payload events + closed wire vocabulary (plan 25 A2).

The vendored family core (``ai/flow_events.py``) builds the family-core
events; health's app-specific halves stay here (ADR-0031: payload
events are app-specific): the four payload-event builders (plan 24 §2
rulings) and the closed wire set the vocabulary wall pins — family core
plus health payload. ``flow_interrupted`` is deliberately absent from
the closed set (health has no server-broadcast stop; the resumable HITL
pause is the ``interrupt`` payload event below — the two interruption
semantics are never conflated).
"""

from enum import StrEnum
from typing import Any


class FlowEvent(StrEnum):
    """Closed set of chat-stream event names (family core + health payload)."""

    # Family core (vendored ai/flow_events.py).
    FLOW_STARTED = "flow_started"
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    DELTA = "delta"
    TOOL_CALL = "tool_call"
    FLOW_FINISHED = "flow_finished"
    FLOW_FAILED = "flow_failed"
    # Health payload events (plan 24 §2 ruling).
    CITATION = "citation"
    TASK = "task"
    INTERRUPT = "interrupt"
    SESSION = "session"


def citation_event(source: str) -> dict[str, Any]:
    """Health payload event: a data-source citation (the tool name)."""
    return {"event": FlowEvent.CITATION, "source": source}


def task_event(task: dict[str, Any]) -> dict[str, Any]:
    """Health payload event: an interactive HITL task card (payload shape
    identical to the former bracketed HITL_TASK sentinel JSON)."""
    return {"event": FlowEvent.TASK, "task": task}


def interrupt_event() -> dict[str, Any]:
    """Health payload event: the run paused at an ask_user interrupt (a
    resumable pause — the terminal family ``flow_interrupted`` is a
    different, absent concept here)."""
    return {"event": FlowEvent.INTERRUPT}


def session_event(session_id: str) -> dict[str, Any]:
    """Health payload event: the session id for a freshly auto-created chat
    session (replaces the former bracketed SESSION_ID content marker)."""
    return {"event": FlowEvent.SESSION, "session_id": session_id}
