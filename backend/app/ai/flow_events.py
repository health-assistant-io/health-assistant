# VENDORED FILE — family canonical (ADR-0031, plan 25). Source of truth:
# dev/contracts/flow_events.py; synced byte-identical to
# backend/app/ai/flow_events.py in study-assistant, health-assistant/core
# and career-assistant by dev/scripts/sync-flow-events.sh, sha-checked by
# dev/scripts/verify-wiring.sh. NEVER edit the vendored copy in-repo —
# change the dev canonical, then re-run the sync (contract changes = one
# dev commit + one sync run, never per-repo edits).
"""Family chat-flow event vocabulary + builders — the shared core
(guidelines ai-features §5, plan 25).

One canonical implementation of the family-core events every chat turn
emits, replacing the study/health modules plan 24 V2/V3 wrote 24 h apart.
App-specific halves stay app-side per the ADR-0031 split: step lists and
labels, tool-id derivation, payload-event builders, run-id gating.

Builders take the union of the family signatures as optional kwargs and
build byte-identical payloads for every §5 transport mapping: the
in-band event-name key defaults to ``"event"`` (health SSE family
frames); study's WS envelope passes ``name_key="type"``; career's SSE
named events lift the name out of the dict (``event.pop("event")``) so
the payload rides the ``event:`` line alone. The per-repo
vocabulary-wall tests pin the emitted wire shapes — they are the
acceptance tests for this module.
"""

from enum import StrEnum
from typing import Any


class FlowEvent(StrEnum):
    """Closed set of family-core chat-stream event names (§5)."""

    FLOW_STARTED = "flow_started"
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    DELTA = "delta"
    TOOL_CALL = "tool_call"
    FLOW_FINISHED = "flow_finished"
    FLOW_FAILED = "flow_failed"
    FLOW_INTERRUPTED = "flow_interrupted"


CHAT_FLOW = "chat"


def flow_started_event(
    flow: str = CHAT_FLOW,
    run_id: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    *,
    name_key: str = "event",
) -> dict[str, Any]:
    event: dict[str, Any] = {name_key: FlowEvent.FLOW_STARTED, "flow": flow}
    if run_id is not None:
        event["run_id"] = run_id
    if steps is not None:
        event["steps"] = [dict(step) for step in steps]
    return event


def node_started_event(node: str, *, name_key: str = "event") -> dict[str, Any]:
    return {name_key: FlowEvent.NODE_STARTED, "node": node}


def node_finished_event(node: str, outcome: str, *, name_key: str = "event") -> dict[str, Any]:
    return {name_key: FlowEvent.NODE_FINISHED, "node": node, "outcome": outcome}


def delta_event(text: str, kind: str | None = None, *, name_key: str = "event") -> dict[str, Any]:
    event: dict[str, Any] = {name_key: FlowEvent.DELTA, "text": text}
    if kind is not None:
        event["kind"] = kind
    return event


def tool_call_event(
    id: str,
    name: str,
    status: str,
    args: str | None = None,
    result: str | None = None,
    title: str | None = None,
    duration_ms: int | None = None,
    *,
    name_key: str = "event",
) -> dict[str, Any]:
    """§5 ``tool_call``. ``args``/``result`` are serialized-safe summary
    strings, never raw payload dicts; the tool id is app-derived
    (tool-id derivation stays app-side per the plan 25 split)."""
    event: dict[str, Any] = {
        name_key: FlowEvent.TOOL_CALL,
        "id": id,
        "name": name,
        "status": status,
    }
    if args is not None:
        event["args"] = args
    if result is not None:
        event["result"] = result
    if title is not None:
        event["title"] = title
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    return event


def flow_finished_event(
    run_id: str | None = None,
    result_ref: str | None = None,
    model: str | None = None,
    total_ms: int | None = None,
    tool_count: int | None = None,
    *,
    name_key: str = "event",
) -> dict[str, Any]:
    event: dict[str, Any] = {name_key: FlowEvent.FLOW_FINISHED}
    if run_id is not None:
        event["run_id"] = run_id
    if result_ref is not None:
        event["result_ref"] = result_ref
    if model is not None:
        event["model"] = model
    if total_ms is not None:
        event["total_ms"] = total_ms
    if tool_count is not None:
        event["tool_count"] = tool_count
    return event


def flow_failed_event(
    retryable: bool,
    code: str | None = None,
    message: str = "",
    *,
    name_key: str = "event",
) -> dict[str, Any]:
    """§5 ``flow_failed``. ``code``/``message`` are set only on the
    endpoint-classified terminal frame (the stable error code the
    frontend localizes)."""
    event: dict[str, Any] = {name_key: FlowEvent.FLOW_FAILED, "retryable": retryable}
    if code is not None:
        event["code"] = code
        event["message"] = message
    return event


def flow_interrupted_event(
    reason: str,
    partial: bool,
    *,
    name_key: str = "event",
) -> dict[str, Any]:
    """§5 ``flow_interrupted`` — the terminal broadcast stop. Carries no
    run id on purpose: the library reducer gates on ``'run_id' in
    event`` so the stop reads for every consumer's live turn."""
    return {name_key: FlowEvent.FLOW_INTERRUPTED, "reason": reason, "partial": partial}
