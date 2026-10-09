"""Vocabulary wall for the health chat stream (plan 24 V3, ai-features §5).

Every event the backend can put on the chat-stream wire must be a member of
the closed :class:`app.ai.flow_events.FlowEvent` set — the family core plus
health's payload events. The engine's internal ``done`` completion signal is
the single sanctioned non-vocabulary item and must never cross the SSE
boundary. Mirrors career's V1 and study's V2 wall tests.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from app.ai.agents.chat_agent import stream_events_as_sse
from app.ai.flow_events import FlowEvent
from app.ai.graphs.chat_agent import chat_engine_iter

FAMILY_CORE = {
    "flow_started",
    "node_started",
    "node_finished",
    "delta",
    "tool_call",
    "flow_finished",
    "flow_failed",
}
HEALTH_PAYLOAD = {"citation", "task", "interrupt", "session"}

LEGACY_NAMES = {
    # Sentinel kinds (deleted with the Phase 6.2 dual-emit layer).
    "content",
    "tool_call_start",
    "tool_call_exec",
    "tool_call_result",
    "tool_call_finished",
    "hitl_task",
    "flow_event",
    # Family event health must NOT adopt (no server-broadcast stop exists).
    "flow_interrupted",
}

LEGACY_WIRE_MARKERS = (
    "[TOOL_CALL_START]",
    "[TOOL_CALL_EXEC]",
    "[TOOL_CALL_RESULT]",
    "[TOOL_CALL_FINISHED]",
    "[CITATION]",
    "[HITL_TASK]",  # gate-allow: \[HITL_TASK\] (deny-list: proves absence)
    "[SESSION_ID]",  # gate-allow: \[SESSION_ID\] (deny-list: proves absence)
    "[FLOW_EVENT]",
    '"content"',
    '"flow_event"',
    '"error_type"',
)


def test_flow_event_set_is_closed_and_expected():
    """The vocabulary is exactly family core + health payload events."""
    assert set(FlowEvent) == FAMILY_CORE | HEALTH_PAYLOAD
    for legacy in LEGACY_NAMES:
        assert legacy not in set(FlowEvent), f"legacy name {legacy!r} re-introduced"


class ScriptedStreamingLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    async def astream(self, history):
        for piece in self.responses.pop(0):
            yield piece


def make_tool(name, observation):
    tool = MagicMock()
    tool.name = name
    tool.ainvoke = AsyncMock(return_value=observation)
    return tool


def test_builders_carry_event_names():
    """Every builder's dict names itself with its enum member."""
    from app.ai.flow_events import (
        citation_event,
        delta_event,
        flow_failed_event,
        flow_finished_event,
        flow_started_event,
        interrupt_event,
        node_finished_event,
        node_started_event,
        session_event,
        task_event,
        tool_call_event,
    )

    builders = [
        flow_started_event(),
        node_started_event("agent_step"),
        node_finished_event("agent_step", "ok"),
        delta_event("hi"),
        tool_call_event(id="t1", name="get_x", status="running"),
        tool_call_event(id="t1", name="get_x", status="done", args="{}", result="ok"),
        citation_event("get_x"),
        task_event({"proposal_id": "p"}),
        interrupt_event(),
        flow_finished_event(),
        flow_failed_event(True),
        flow_failed_event(False, code="guard", message="no"),
        session_event("s1"),
    ]
    for event in builders:
        assert event["event"] in set(FlowEvent)


async def _collect_events(gen):
    return [event async for event in gen]


async def _sse_frames(gen):
    return [frame async for frame in stream_events_as_sse(gen)]


def _parse(frames):
    return [json.loads(f.removeprefix("data: ").strip()) for f in frames]


@pytest.mark.asyncio
async def test_engine_vocabulary_clean_tool_turn():
    """A full tool turn emits only vocabulary names (+ internal done)."""
    llm = ScriptedStreamingLLM(
        [
            [
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"name": "get_patient_summary", "args": "", "id": "c1", "index": None}
                    ],
                )
            ],
            [AIMessageChunk(content="All good.")],
        ]
    )
    events = await _collect_events(
        chat_engine_iter(
            llm,
            tools=[make_tool("get_patient_summary", "ok")],
            history=[HumanMessage("check")],
            max_iterations=5,
            streaming=True,
            chat_session_service=None,
            session_id=None,
        )
    )
    names = [e["event"] for e in events]
    assert set(names) <= set(FlowEvent) | {"done"}
    assert "delta" in names and "tool_call" in names and "citation" in names


@pytest.mark.asyncio
async def test_wire_vocabulary_clean_tool_turn():
    """Every SSE frame is a vocabulary event; no legacy marker on the wire."""
    llm = ScriptedStreamingLLM(
        [
            [
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"name": "get_patient_summary", "args": "", "id": "c1", "index": None}
                    ],
                )
            ],
            [AIMessageChunk(content="All good.")],
        ]
    )
    frames = await _sse_frames(
        chat_engine_iter(
            llm,
            tools=[make_tool("get_patient_summary", "ok")],
            history=[HumanMessage("check")],
            max_iterations=5,
            streaming=True,
            chat_session_service=None,
            session_id=None,
        )
    )
    parsed = _parse(frames)
    assert {p["event"] for p in parsed} <= set(FlowEvent)
    wire = "".join(frames)
    for marker in LEGACY_WIRE_MARKERS:
        assert marker not in wire, f"legacy marker {marker!r} on the wire"


@pytest.mark.asyncio
async def test_endpoint_wire_includes_session_and_error_frames(async_client):
    """Endpoint-level wall: the session payload event and the classified
    ``flow_failed`` error frame are the only non-engine frames — and every
    frame names a vocabulary event."""
    from tests._auth_helpers import auth_headers, create_user

    user = await create_user()
    headers = await auth_headers(user)

    service_frames = [
        'data: {"event": "session", "session_id": "' + str(uuid4()) + '"}\n\n',
        'data: {"event": "delta", "text": "hi"}\n\n',
    ]

    async def _fake_stream(**kwargs):
        for frame in service_frames:
            yield frame
        raise ValueError("Chat session not found.")

    async def _fake_assist(**kwargs):
        return _fake_stream(**kwargs)

    with patch("app.api.v1.endpoints.ai_assistance.AIAssistanceService") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.assist = _fake_assist
        mock_cls.return_value = mock_instance

        resp = await async_client.post(
            "/api/v1/ai-assistance/stream",
            headers=headers,
            json={"task_type": "chat", "user_input": "hi", "context": {}},
        )
        assert resp.status_code == 200
        parsed = _parse([f for f in resp.text.split("\n\n") if f.strip()])
        assert {p["event"] for p in parsed} == {"session", "delta", "flow_failed"}
        failure = next(p for p in parsed if p["event"] == "flow_failed")
        assert failure["code"] == "guard"
        assert failure["message"] == "Chat session not found."
        assert failure["retryable"] is False
        for marker in LEGACY_WIRE_MARKERS:
            assert marker not in resp.text


def test_flow_events_gate_parameter_is_gone():
    """No compat knob: neither streaming endpoint accepts ``flow_events``."""
    from app.api.v1.endpoints import ai_assistance

    for endpoint in (ai_assistance.assist_user_stream, ai_assistance.resume_hitl_session):
        import inspect

        params = inspect.signature(endpoint).parameters
        assert "flow_events" not in params
