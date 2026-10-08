"""Phase 3.2 + 8: the LangGraph chat engine's family-event behavior.

Since the Phase 8 decommission this is the ONLY engine; these tests pin the
family chat vocabulary (ai-features §5 + health payload events, plan 24 V3)
that the frontend and the SSE endpoint consume: ``delta`` text streaming,
``tool_call`` lifecycle, ``citation``/``task`` payload events, proactive
persistence, and the iteration cap.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.ai.agents.chat_agent import stream_events_as_sse
from app.ai.graphs.chat_agent import build_chat_graph, chat_engine_iter

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class ScriptedLLM:
    """Non-streaming fake: pops one AIMessage per ainvoke."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, history):
        self.calls += 1
        return self.responses.pop(0)


class ScriptedStreamingLLM:
    """Streaming fake: each response is a list of AIMessageChunk pieces fed to
    astream. Supports the accumulated-content quirk (providers re-emitting)."""

    def __init__(self, responses):
        self.responses = list(responses)

    def bind_tools(self, tools):
        return self

    async def astream(self, history):
        for piece in self.responses.pop(0):
            yield piece


def _tool(name, observation):
    tool = MagicMock()
    tool.name = name
    tool.ainvoke = AsyncMock(return_value=observation)
    return tool


def _service():
    svc = MagicMock()
    svc.save_message = AsyncMock(side_effect=lambda **kw: MagicMock(id="msg-1"))
    svc.update_message_fields = AsyncMock(return_value=None)
    return svc


HITL_OBSERVATION = json.dumps(
    {"__hitl__": True, "task": {"task_type": "create_medication", "title": "Ibu"}}
)

TOOL_CALL = {
    "name": "get_patient_summary",
    "args": {"patient_id": "p1"},
    "id": "call_1",
    "type": "tool_call",
}


async def _collect(gen):
    return [event async for event in gen]


def _names(events):
    return [e["event"] for e in events]


# ---------------------------------------------------------------------------
# Delta / tool_call behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nonstreaming_no_tools_clean_break():
    llm = ScriptedLLM([AIMessage(content="Hello!")])
    events = await _collect(
        chat_engine_iter(
            llm,
            tools=[],
            history=[HumanMessage("hi")],
            max_iterations=3,
            streaming=False,
            chat_session_service=None,
            session_id=None,
        )
    )
    assert _names(events) == [
        "flow_started",
        "node_started",
        "delta",
        "node_finished",
        "node_started",
        "done",
        "node_finished",
        "flow_finished",
    ]
    assert [e["text"] for e in events if e["event"] == "delta"] == ["Hello!"]
    assert next(e for e in events if e["event"] == "done")["reached_max"] is False


@pytest.mark.asyncio
async def test_tool_call_then_answer():
    def make_llm():
        return ScriptedLLM(
            [
                AIMessage(content="", tool_calls=[TOOL_CALL]),
                AIMessage(content="The patient is fine."),
            ]
        )

    tools = [_tool("get_patient_summary", '{"status": "ok"}')]
    events = await _collect(
        chat_engine_iter(
            make_llm(),
            tools=tools,
            history=[HumanMessage("check")],
            max_iterations=5,
            streaming=False,
            chat_session_service=None,
            session_id=None,
        )
    )
    names = _names(events)
    assert names == [
        "flow_started",
        "node_started",  # agent_step (1st LLM call)
        "node_finished",
        "node_started",  # tool_exec
        "tool_call",  # exec (running)
        "tool_call",  # result (done)
        "citation",
        "node_finished",
        "node_started",  # agent_step (2nd LLM call)
        "delta",
        "node_finished",
        "node_started",  # finalize
        "done",
        "node_finished",
        "flow_finished",
    ]
    tool_events = [e for e in events if e["event"] == "tool_call"]
    assert tool_events[0]["status"] == "running"
    assert tool_events[0]["name"] == "get_patient_summary"
    assert tool_events[1]["status"] == "done"
    assert tool_events[1]["result"] == '{"status": "ok"}'
    # §5: args is a serialized-safe string, never a raw dict.
    assert isinstance(tool_events[1]["args"], str)
    assert json.loads(tool_events[1]["args"]) == {"patient_id": "p1"}
    citations = [e for e in events if e["event"] == "citation"]
    assert citations[0]["source"] == "get_patient_summary"


@pytest.mark.asyncio
async def test_streaming_tool_call_and_quirky_deltas():
    """Second chunk RE-EMITS the accumulated content (provider quirk) — the
    dedup must yield true deltas only."""

    def make_llm():
        return ScriptedStreamingLLM(
            [
                [
                    AIMessageChunk(content="Analy"),
                    AIMessageChunk(content="Analyzing now"),
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": "get_patient_summary",
                                "args": "",
                                "id": "call_1",
                                "index": None,
                            }
                        ],
                    ),
                ],
                [AIMessageChunk(content="All good.")],
            ]
        )

    tools = [_tool("get_patient_summary", "ok")]
    events = await _collect(
        chat_engine_iter(
            make_llm(),
            tools=tools,
            history=[HumanMessage("check")],
            max_iterations=5,
            streaming=True,
            chat_session_service=None,
            session_id=None,
        )
    )
    deltas = [e["text"] for e in events if e["event"] == "delta"]
    assert deltas == ["Analy", "zing now", "All good."]
    # The streamed tool-name announce arrives as a running tool_call.
    announces = [e for e in events if e["event"] == "tool_call" and e["status"] == "running"]
    assert announces[0]["name"] == "get_patient_summary"


# ---------------------------------------------------------------------------
# HITL proposal semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hitl_proposal_trimmed_feedback_proactive_save():
    def make_llm():
        return ScriptedStreamingLLM(
            [
                [
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": "propose_medication",
                                "args": "",
                                "id": "call_1",
                                "index": None,
                            }
                        ],
                    )
                ],
                [AIMessageChunk(content="Drafted the medication.")],
            ]
        )

    tools = [_tool("propose_medication", HITL_OBSERVATION)]
    svc = _service()
    events = await _collect(
        chat_engine_iter(
            make_llm(),
            tools=tools,
            history=[HumanMessage("add ibuprofen")],
            max_iterations=5,
            streaming=True,
            chat_session_service=svc,
            session_id="11111111-1111-1111-1111-111111111111",
        )
    )

    task_events = [e for e in events if e["event"] == "task"]
    assert len(task_events) == 1
    assert task_events[0]["task"]["task_type"] == "create_medication"
    # Proactive save ran exactly once, with the task attached; the final save
    # went through update_message_fields (proactive message exists).
    assert svc.save_message.await_count == 1
    save_kwargs = svc.save_message.await_args.kwargs
    assert save_kwargs["tasks"] == [task_events[0]["task"]]
    assert svc.update_message_fields.await_count == 1
    # No citation for proposals; the tool result is the trimmed feedback.
    assert "citation" not in _names(events)
    results = [e for e in events if e["event"] == "tool_call" and e["status"] == "done"]
    assert results[0]["result"] != HITL_OBSERVATION


# ---------------------------------------------------------------------------
# Max-iterations cap
# ---------------------------------------------------------------------------


class AlwaysToolsLLM:
    def bind_tools(self, tools):
        return self

    async def astream(self, history):
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[
                {"name": "get_patient_summary", "args": "", "id": "c1", "index": None}
            ],
        )


@pytest.mark.asyncio
async def test_max_iterations_cap_streams_save_and_done_true():
    tools = [_tool("get_patient_summary", "ok")]
    svc = _service()
    events = await _collect(
        chat_engine_iter(
            AlwaysToolsLLM(),
            tools=tools,
            history=[HumanMessage("check")],
            max_iterations=2,
            streaming=True,
            chat_session_service=svc,
            session_id="22222222-2222-2222-2222-222222222222",
        )
    )
    done = [e for e in events if e["event"] == "done"]
    assert done and done[0]["reached_max"] is True
    assert events[-1]["event"] == "flow_finished"
    # Final save happened (streaming always), no proactive save (no HITL).
    assert svc.save_message.await_count == 1
    assert svc.update_message_fields.await_count == 0


@pytest.mark.asyncio
async def test_zero_iterations_never_calls_llm():
    llm = ScriptedLLM([AIMessage(content="should not run")])
    events = await _collect(
        chat_engine_iter(
            llm,
            tools=[],
            history=[],
            max_iterations=0,
            streaming=False,
            chat_session_service=None,
            session_id=None,
        )
    )
    assert _names(events) == [
        "flow_started",
        "node_started",  # finalize (route_start short-circuits the LLM)
        "done",
        "node_finished",
        "flow_finished",
    ]
    assert events[2]["reached_max"] is True
    assert llm.calls == 0


# ---------------------------------------------------------------------------
# SSE output + checkpointer compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_family_stream():
    llm = ScriptedLLM(
        [
            AIMessage(content="", tool_calls=[TOOL_CALL]),
            AIMessage(content="Answer."),
        ]
    )
    tools = [_tool("get_patient_summary", "ok")]
    frames = [
        frame
        async for frame in stream_events_as_sse(
            chat_engine_iter(
                llm,
                tools=tools,
                history=[HumanMessage("check")],
                max_iterations=5,
                streaming=False,
                chat_session_service=None,
                session_id=None,
            )
        )
    ]
    parsed = [json.loads(f.removeprefix("data: ").strip()) for f in frames]
    assert {"event": "citation", "source": "get_patient_summary"} in parsed
    assert {"event": "flow_finished"} in parsed[-1:]
    assert parsed[-1]["event"] == "flow_finished"
    assert {"event": "delta", "text": "Answer."} in parsed
    # The internal done signal never reaches the wire.
    assert "done" not in [p["event"] for p in parsed]


@pytest.mark.asyncio
async def test_graph_compiles_with_inmemory_checkpointer():
    from langgraph.checkpoint.memory import InMemorySaver

    events = await _collect(
        chat_engine_iter(
            ScriptedLLM([AIMessage(content="Hello!")]),
            tools=[],
            history=[HumanMessage("hi")],
            max_iterations=3,
            streaming=False,
            chat_session_service=None,
            session_id="33333333-3333-3333-3333-333333333333",
            checkpointer=InMemorySaver(),
        )
    )
    assert events[-1]["event"] == "flow_finished"


def test_graph_exposes_expected_nodes():
    graph = build_chat_graph()
    nodes = set(graph.get_graph().nodes.keys())
    assert {"agent_step", "tool_exec", "finalize"} <= nodes
