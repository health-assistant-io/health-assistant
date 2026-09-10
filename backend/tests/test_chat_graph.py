"""Phase 3.2 + 8: the LangGraph chat engine's sentinel/delta behavior.

Since the Phase 8 decommission this is the ONLY engine; these tests pin the
frozen legacy contract (content deltas, ``[TOOL_CALL_*]`` / ``[CITATION]`` /
``[HITL_TASK]`` sentinels, proactive persistence, iteration cap) that the
frontend and the SSE endpoint consume.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.ai.agents.chat_agent import stream_loop_as_sse
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


# ---------------------------------------------------------------------------
# Sentinel/delta behavior
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
    legacy = [(k, d) for k, d in events if k != "flow_event"]
    assert legacy == [("content", "Hello!"), ("done", False)]


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
    kinds = [k for k, _ in events if k != "flow_event"]
    assert kinds == [
        "tool_call_exec",
        "tool_call_result",
        "citation",
        "tool_call_finished",
        "content",
        "done",
    ]
    assert [(k, d) for k, d in events if k != "flow_event"][-1] == ("done", False)
    results = [d for k, d in events if k == "tool_call_result"]
    assert results[0]["result"] == '{"status": "ok"}'


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
    deltas = [d for k, d in events if k == "content"]
    assert deltas == ["Analy", "zing now", "All good."]


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

    hitl_events = [d for k, d in events if k == "hitl_task"]
    assert len(hitl_events) == 1
    assert hitl_events[0]["task_type"] == "create_medication"
    # Proactive save ran exactly once, with the task attached; the final save
    # went through update_message_fields (proactive message exists).
    assert svc.save_message.await_count == 1
    save_kwargs = svc.save_message.await_args.kwargs
    assert save_kwargs["tasks"] == [hitl_events[0]]
    assert svc.update_message_fields.await_count == 1
    # No citation for proposals; result payload is the trimmed feedback.
    assert "citation" not in [k for k, _ in events]
    results = [d for k, d in events if k == "tool_call_result"]
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
    legacy = [(k, d) for k, d in events if k != "flow_event"]
    assert legacy[-1] == ("done", True)
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
    assert [(k, d) for k, d in events if k != "flow_event"] == [("done", True)]
    assert llm.calls == 0


# ---------------------------------------------------------------------------
# SSE sentinel output + checkpointer compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_sentinel_stream():
    llm = ScriptedLLM(
        [
            AIMessage(content="", tool_calls=[TOOL_CALL]),
            AIMessage(content="Answer."),
        ]
    )
    tools = [_tool("get_patient_summary", "ok")]
    sse = [
        chunk
        async for chunk in stream_loop_as_sse(
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
    assert "[CITATION] get_patient_summary" in sse
    assert "[TOOL_CALL_FINISHED]" in sse
    assert sse[-1] == "Answer."


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
    legacy = [(k, d) for k, d in events if k != "flow_event"]
    assert legacy[-1] == ("done", False)


def test_graph_exposes_expected_nodes():
    graph = build_chat_graph()
    nodes = set(graph.get_graph().nodes.keys())
    assert {"agent_step", "tool_exec", "finalize"} <= nodes
