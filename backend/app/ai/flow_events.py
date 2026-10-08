"""Family chat-flow event vocabulary (guidelines ai-features §5, plan 24 V3).

The chat stream emits the family core (``flow_started`` / ``node_started`` /
``node_finished`` / ``delta`` / ``tool_call`` / ``flow_finished`` /
``flow_failed``) natively — plus health's payload events (``citation`` /
``task`` / ``interrupt`` / ``session``), the career ``builder_state``
pattern: app-specific payloads alongside the family core, documented in
ai-features §5.

Two rulings frozen in plan 24 §2:

* ``interrupt`` is health's **resumable HITL pause** (ask_user turns pause
  at the card and continue via ``/resume``). It is NOT the terminal
  family ``flow_interrupted`` (a server-broadcast stop) — health has no
  broadcast-stop path, so that event is deliberately absent from the set.
* The SSE wire carries every event as one JSON frame,
  ``data: {"event": <name>, ...fields}`` — no legacy sentinels, no
  content-embedded control markers, no ``flow_events`` gate.

``"done"`` is the one engine-internal completion signal that never reaches
the wire (the non-streaming consumer reads the iteration cap from it); it
is not part of this vocabulary.
"""

from enum import StrEnum
from typing import Any


class FlowEvent(StrEnum):
    """Closed set of chat-stream event names (family core + health payload)."""

    # Family core (ai-features §5).
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


CHAT_FLOW = "chat"


def flow_started_event() -> dict[str, Any]:
    return {"event": FlowEvent.FLOW_STARTED, "flow": CHAT_FLOW}


def node_started_event(node: str) -> dict[str, Any]:
    return {"event": FlowEvent.NODE_STARTED, "node": node}


def node_finished_event(node: str, outcome: str) -> dict[str, Any]:
    return {"event": FlowEvent.NODE_FINISHED, "node": node, "outcome": outcome}


def delta_event(text: str) -> dict[str, Any]:
    return {"event": FlowEvent.DELTA, "text": text}


def tool_call_event(
    id: str,
    name: str,
    status: str,
    args: str | None = None,
    result: str | None = None,
) -> dict[str, Any]:
    """§5 ``tool_call`` — ``status`` is ``running`` or ``done`` (a failed
    tool bubbles to the SSE classifier and lands as ``flow_failed``);
    ``args`` is a serialized-safe string, never a raw payload dict."""
    event: dict[str, Any] = {
        "event": FlowEvent.TOOL_CALL,
        "id": id,
        "name": name,
        "status": status,
    }
    if args is not None:
        event["args"] = args
    if result is not None:
        event["result"] = result
    return event


def citation_event(source: str) -> dict[str, Any]:
    """Health payload event: a data-source citation (the tool name)."""
    return {"event": FlowEvent.CITATION, "source": source}


def task_event(task: dict[str, Any]) -> dict[str, Any]:
    """Health payload event: an interactive HITL task card (payload shape
    identical to the former ``[HITL_TASK]`` sentinel JSON)."""
    return {"event": FlowEvent.TASK, "task": task}


def interrupt_event() -> dict[str, Any]:
    """Health payload event: the run paused at an ask_user interrupt (a
    resumable pause — the terminal family ``flow_interrupted`` is a
    different, absent concept here)."""
    return {"event": FlowEvent.INTERRUPT}


def flow_finished_event() -> dict[str, Any]:
    return {"event": FlowEvent.FLOW_FINISHED}


def flow_failed_event(
    retryable: bool,
    code: str | None = None,
    message: str = "",
) -> dict[str, Any]:
    """§5 ``flow_failed``. ``code``/``message`` are set only on the
    endpoint-classified terminal frame (the stable error code the frontend
    localizes; ``message`` is non-empty only for soft guard violations)."""
    event: dict[str, Any] = {"event": FlowEvent.FLOW_FAILED, "retryable": retryable}
    if code is not None:
        event["code"] = code
        event["message"] = message
    return event


def session_event(session_id: str) -> dict[str, Any]:
    """Health payload event: the session id for a freshly auto-created chat
    session (replaces the former ``[SESSION_ID]`` content marker)."""
    return {"event": FlowEvent.SESSION, "session_id": session_id}
