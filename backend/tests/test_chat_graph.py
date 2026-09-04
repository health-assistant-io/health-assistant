"""Phase 3.2: the LangGraph chat engine must mirror run_reasoning_loop
event-for-event (parity gate for the AI_AGENT_ENGINE=graph flag).

Scenarios: no-tool clean break, tool-call turn, HITL proposal (trimmed
feedback + proactive persistence + no citation), max-iterations cap, SSE
sentinel parity, and a checkpointer-attached compile (Phase 3.3 path).
"""

import json
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.ai.agents.chat_agent import run_reasoning_loop, stream_loop_as_sse
from app.ai.graphs.chat_agent import build_chat_graph, chat_engine_iter


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class ScriptedLLM:
    """Non-streaming fake: pops one AIMessage per ainvoke."""

    def __init__(self, responses: List[AIMessage]):
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, history):
        self.calls += 1
        return self.responses.pop(0)


class ScriptedStreamingLLM:
    """Streaming fake: each response is a list of AIMessageChunk pieces fed to
    astream. Supports the accumulated-content quirk (providers re-emitting)."""

    def __init__(self, responses: List[List[AIMessageChunk]]):
        self.responses = list(responses)

    async def astream(self, history):
        for piece in self.responses.pop(0):
            yield piece


def _tool(name: str, observation: Any):
    tool = MagicMock()
    tool.name = name
    tool.ainvoke = AsyncMock(return_value=observation)
    return tool


def _service():
    svc = MagicMock()
    svc.save_message = AsyncMock(side_effect=lambda **kw: MagicMock(id="msg-proactive"))
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
# Parity: non-streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parity_nonstreaming_no_tools():
    history = [HumanMessage("hi")]

    def make_llm():
        return ScriptedLLM([AIMessage(content="Hello!")])

    kwargs = dict(
        tools=[],
        history=history,
        max_iterations=3,
        streaming=False,
        chat_session_service=None,
        session_id=None,
    )
    loop_events = await _collect(run_reasoning_loop(make_llm(), **kwargs))
    graph_events = await _collect(chat_engine_iter(make_llm(), **kwargs))
    assert loop_events == [
        ("content", "Hello!"),
        ("done", False),
    ]
    assert graph_events == loop_events


@pytest.mark.asyncio
async def test_parity_nonstreaming_tool_call_then_answer():
    def make_llm():
        return ScriptedLLM(
            [
                AIMessage(content="", tool_calls=[TOOL_CALL]),
                AIMessage(content="The patient is fine."),
            ]
        )

    tools = [_tool("get_patient_summary", '{"status": "ok"}')]
    history = [HumanMessage("check")]
    kwargs = dict(
        tools=tools,
        history=history,
        max_iterations=5,
        streaming=False,
        chat_session_service=None,
        session_id=None,
    )
    loop_events = await _collect(run_reasoning_loop(make_llm(), **kwargs))
    graph_events = await _collect(chat_engine_iter(make_llm(), **kwargs))
    assert graph_events == loop_events
    kinds = [k for k, _ in loop_events]
    assert kinds == [
        "tool_call_exec",
        "tool_call_result",
        "citation",
        "tool_call_finished",
        "content",
        "done",
    ]
    assert ("done", False) == loop_events[-1]


# ---------------------------------------------------------------------------
# Parity: streaming (incl. the provider accumulated-content quirk)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parity_streaming_tool_call_and_quirky_deltas():
    # Second chunk RE-EMITS the accumulated content (provider quirk the loop
    # reconciles) — both engines must produce identical deltas.
    scripted = [
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
    tools = [_tool("get_patient_summary", "ok")]
    kwargs = dict(
        tools=tools,
        history=[HumanMessage("check")],
        max_iterations=5,
        streaming=True,
        chat_session_service=None,
        session_id=None,
    )
    loop_events = await _collect(
        run_reasoning_loop(ScriptedStreamingLLM(scripted), **kwargs)
    )
    graph_events = await _collect(
        chat_engine_iter(ScriptedStreamingLLM(scripted), **kwargs)
    )
    assert graph_events == loop_events
    deltas = [d for k, d in loop_events if k == "content"]
    assert deltas == ["Analy", "zing now", "All good."]


# ---------------------------------------------------------------------------
# HITL proposal semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_hitl_proposal_trimmed_feedback_proactive_save():
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
    svc_loop = _service()
    svc_graph = _service()
    kwargs_loop = dict(
        tools=tools,
        history=[HumanMessage("add ibuprofen")],
        max_iterations=5,
        streaming=True,
        chat_session_service=svc_loop,
        session_id="11111111-1111-1111-1111-111111111111",
    )
    kwargs_graph = dict(kwargs_loop, chat_session_service=svc_graph)
    loop_events = await _collect(run_reasoning_loop(make_llm(), **kwargs_loop))
    graph_events = await _collect(chat_engine_iter(make_llm(), **kwargs_graph))
    assert graph_events == loop_events

    hitl_events = [d for k, d in graph_events if k == "hitl_task"]
    assert len(hitl_events) == 1
    assert hitl_events[0]["task_type"] == "create_medication"
    # Proactive save ran exactly once per engine, with the task attached; the
    # final save went through update_message_fields (proactive message exists).
    for svc in (svc_loop, svc_graph):
        assert svc.save_message.await_count == 1
        save_kwargs = svc.save_message.await_args.kwargs
        assert save_kwargs["tasks"] == [hitl_events[0]]
        assert svc.update_message_fields.await_count == 1
    # No citation for proposals; result payload is the trimmed feedback.
    assert "citation" not in [k for k, _ in graph_events]
    results = [d for k, d in graph_events if k == "tool_call_result"]
    assert results[0]["result"] != HITL_OBSERVATION


# ---------------------------------------------------------------------------
# Max-iterations cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_max_iterations_cap_streams_save_and_done_true():
    class AlwaysToolsLLM:
        async def astream(self, history):
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "get_patient_summary",
                        "args": "",
                        "id": f"c{len(history)}",
                        "index": None,
                    }
                ],
            )

    tools = [_tool("get_patient_summary", "ok")]
    svc = _service()
    kwargs = dict(
        tools=tools,
        history=[HumanMessage("check")],
        max_iterations=2,
        streaming=True,
        chat_session_service=svc,
        session_id="22222222-2222-2222-2222-222222222222",
    )
    graph_events = await _collect(chat_engine_iter(AlwaysToolsLLM(), **kwargs))
    assert graph_events[-1] == ("done", True)
    # Final save happened (streaming always), no proactive save (no HITL).
    assert svc.save_message.await_count == 1
    assert svc.update_message_fields.await_count == 0


@pytest.mark.asyncio
async def test_graph_zero_iterations_never_calls_llm():
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
    assert events == [("done", True)]
    assert llm.calls == 0


# ---------------------------------------------------------------------------
# SSE sentinel parity + checkpointer compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_sentinels_identical_between_engines():
    llm_loop = ScriptedLLM(
        [
            AIMessage(content="", tool_calls=[TOOL_CALL]),
            AIMessage(content="Answer."),
        ]
    )
    llm_graph = ScriptedLLM(
        [
            AIMessage(content="", tool_calls=[TOOL_CALL]),
            AIMessage(content="Answer."),
        ]
    )
    tools = [_tool("get_patient_summary", "ok")]
    kwargs = dict(
        tools=tools,
        history=[HumanMessage("check")],
        max_iterations=5,
        streaming=False,
        chat_session_service=None,
        session_id=None,
    )
    loop_sse = [
        chunk
        async for chunk in stream_loop_as_sse(run_reasoning_loop(llm_loop, **kwargs))
    ]
    graph_sse = [
        chunk
        async for chunk in stream_loop_as_sse(chat_engine_iter(llm_graph, **kwargs))
    ]
    assert graph_sse == loop_sse
    assert "[CITATION] get_patient_summary" in loop_sse
    assert "[TOOL_CALL_FINISHED]" in loop_sse


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
    assert events[-1] == ("done", False)


def test_graph_exposes_expected_nodes():
    graph = build_chat_graph()
    nodes = set(graph.get_graph().nodes.keys())
    assert {"agent_step", "tool_exec", "finalize"} <= nodes
