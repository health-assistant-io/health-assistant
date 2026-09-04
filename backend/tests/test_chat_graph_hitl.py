"""Phase 3.3: ask_user interrupt semantics + fault tolerance in the graph
engine (AI_AGENT_ENGINE=graph).

Ruling: ask_user PAUSES the turn at the question card (terminal-card SSE) and
/resume delivers the resolution summary as Command(resume=...) — the model
continues the SAME conversation. propose_* keeps continue-after-propose.
Fault tolerance: agent_step retries transient errors; tool_exec is pinned to
a single attempt (clinical tools stay non-idempotent even checkpointed) and
errors bubble to the SSE error classifier exactly like the loop engine.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.ai.graphs.chat_agent import (
    chat_engine_iter,
    has_pending_interrupt,
    resume_interrupted_chat_graph,
)
from app.ai.graphs.checkpointer import CheckpointStore, bind_runtime_store

SESSION_ID = "44444444-4444-4444-4444-444444444444"

ASK_USER_CALL = {
    "name": "ask_user",
    "args": {"questions": [{"id": "q_dose", "kind": "freetext", "prompt": "Dose?"}]},
    "id": "call_ask_1",
    "type": "tool_call",
}


class ScriptedStreamingLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    async def astream(self, history):
        for piece in self.responses.pop(0):
            yield piece


def _ask_user_tool(observation: str):
    tool = MagicMock()
    tool.name = "ask_user"
    tool.ainvoke = AsyncMock(return_value=observation)
    return tool


def _service():
    svc = MagicMock()
    svc.save_message = AsyncMock(side_effect=lambda **kw: MagicMock(id="msg-card"))
    svc.update_message_fields = AsyncMock(return_value=None)
    svc.find_message_by_proposal = AsyncMock(return_value=MagicMock(id="msg-card"))
    return svc


USER_ID = "55555555-5555-5555-5555-555555555555"
TENANT_ID = "66666666-6666-6666-6666-666666666666"


def _ask_observation() -> str:
    return json.dumps(
        {
            "__hitl__": True,
            "task": {
                "schema_version": 2,
                "proposal_id": "prop-1",
                "task_type": "ask_user",
                "title": "Quick questions",
                "status": "proposed",
                "proposed_payload": {},
                "context": {},
                "created_at": "2026-09-04T00:00:00+00:00",
                "resolved": None,
            },
        }
    )


@pytest.fixture
def graph_engine(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AI_AGENT_ENGINE", "graph")


async def _collect(gen):
    return [event async for event in gen]


@pytest.mark.asyncio
async def test_ask_user_interrupt_resume_roundtrip(graph_engine, monkeypatch):
    """The turn pauses at the card; /resume continues the SAME conversation."""
    ask_tool = _ask_user_tool(_ask_observation())
    svc = _service()

    def make_llm():
        return ScriptedStreamingLLM(
            [
                [
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": "ask_user",
                                "args": "",
                                "id": "call_ask_1",
                                "index": None,
                            }
                        ],
                    )
                ],
                [AIMessageChunk(content="Thanks! Dose noted.")],
            ]
        )

    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    bind_runtime_store(store)
    try:
        llm = make_llm()
        events1 = await _collect(
            chat_engine_iter(
                llm,
                tools=[ask_tool],
                history=[HumanMessage("what dose?")],
                max_iterations=5,
                streaming=True,
                chat_session_service=svc,
                session_id=SESSION_ID,
                user_id=USER_ID,
                tenant_id=TENANT_ID,
            )
        )

        # Terminal-card stream: ends after the card, no done event.
        kinds1 = [
            k
            for k, _ in events1
            if k != "flow_event"
        ]
        assert kinds1[-2:] == ["hitl_task", "tool_call_finished"]
        assert "done" not in kinds1
        # Paused at an interrupt on the session's thread.
        assert await has_pending_interrupt(SESSION_ID)

        # /resume: the resolution summary arrives via Command(resume=...).
        # The runtime handles are supplied fresh (never checkpointed) — the
        # same llm instance continues with its queued second response.
        summary = "[HITL RESOLUTION FEEDBACK] q_dose answered: 500mg"
        resumed = await resume_interrupted_chat_graph(
            SESSION_ID,
            summary,
            llm_with_tools=llm,
            tools=[ask_tool],
            chat_session_service=svc,
            log_label="AI Assistance (resume)",
            user_id=USER_ID,
            tenant_id=TENANT_ID,
        )
        events2 = await _collect(resumed)

        kinds2 = [k for k, _ in events2 if k != "flow_event"]
        assert kinds2 == ["content", "done"]
        assert [e for e in events2 if e[0] != "flow_event"][-1] == ("done", False)
        assert "Thanks! Dose noted." in [d for k, d in events2 if k == "content"]

        # Side-effect safety: the ask_user tool executed exactly ONCE across
        # both runs (await_user is side-effect-free; tool_exec committed via
        # the superstep checkpoint before the pause).
        assert ask_tool.ainvoke.await_count == 1
        # Proactive save once (the card), then the final save updated it.
        assert svc.save_message.await_count == 1
        assert svc.update_message_fields.await_count == 1
        # The interrupt is consumed.
        assert not await has_pending_interrupt(SESSION_ID)
    finally:
        bind_runtime_store(None)
        await store.close()


@pytest.mark.asyncio
async def test_ask_user_sse_terminal_card(graph_engine):
    """stream_loop_as_sse ends right after the card — no content after it."""
    from app.ai.agents.chat_agent import stream_loop_as_sse

    ask_tool = _ask_user_tool(_ask_observation())
    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    bind_runtime_store(store)
    try:
        llm = ScriptedStreamingLLM(
            [
                [
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "name": "ask_user",
                                "args": "",
                                "id": "call_ask_1",
                                "index": None,
                            }
                        ],
                    )
                ],
                [AIMessageChunk(content="SHOULD NEVER STREAM")],
            ]
        )
        sse = [
            chunk
            async for chunk in stream_loop_as_sse(
                chat_engine_iter(
                    llm,
                    tools=[ask_tool],
                    history=[HumanMessage("what dose?")],
                    max_iterations=5,
                    streaming=True,
                    chat_session_service=_service(),
                    session_id=SESSION_ID,
                )
            )
        ]
        assert sse[-2].startswith("[HITL_TASK] ")
        assert sse[-1] == "[TOOL_CALL_FINISHED]"
        assert all("SHOULD NEVER STREAM" not in c for c in sse)
    finally:
        bind_runtime_store(None)
        await store.close()


@pytest.mark.asyncio
async def test_agent_step_retries_transient_errors(graph_engine):
    """Graph-default retry: a transient ConnectionError inside agent_step is
    retried (LLM calls are read-only; state commits only on node success)."""
    calls = {"n": 0}

    class FlakyThenGoodLLM:
        async def astream(self, history):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("transient")
            yield AIMessageChunk(content="ok")

    events = await _collect(
        chat_engine_iter(
            FlakyThenGoodLLM(),
            tools=[],
            history=[HumanMessage("hi")],
            max_iterations=3,
            streaming=True,
            chat_session_service=None,
            session_id=None,
        )
    )
    legacy = [e for e in events if e[0] != "flow_event"]
    assert legacy == [("content", "ok"), ("done", False)]
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_tool_exec_never_retries_and_bubbles(graph_engine):
    """tool_exec is pinned to one attempt (clinical tools are not idempotent)
    and its errors bubble exactly like the loop engine (SSE classifier)."""
    boom = MagicMock()
    boom.name = "create_medication"
    boom.ainvoke = AsyncMock(side_effect=RuntimeError("clinical write failed"))

    llm = ScriptedStreamingLLM(
        [
            [
                AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "create_medication",
                            "args": "",
                            "id": "c1",
                            "index": None,
                        }
                    ],
                )
            ],
        ]
    )
    with pytest.raises(RuntimeError, match="clinical write failed"):
        await _collect(
            chat_engine_iter(
                llm,
                tools=[boom],
                history=[HumanMessage("add med")],
                max_iterations=3,
                streaming=True,
                chat_session_service=None,
                session_id=None,
            )
        )
    assert boom.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_ask_user_nonstreaming_degrades_to_continue_mode(graph_engine):
    """Without a stream there is no terminal card to pause on — ask_user
    degrades to the propose-style continue path (no interrupt)."""
    ask_tool = _ask_user_tool(_ask_observation())
    llm = ScriptedStreamingLLM.__new__(ScriptedStreamingLLM)

    class NonStreamingLLM:
        def __init__(self):
            self.responses = [
                AIMessage(content="", tool_calls=[ASK_USER_CALL]),
                AIMessage(content="Asked the user."),
            ]

        async def ainvoke(self, history):
            return self.responses.pop(0)

    llm = NonStreamingLLM()
    events = await _collect(
        chat_engine_iter(
            llm,
            tools=[ask_tool],
            history=[HumanMessage("what dose?")],
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
        "hitl_task",
        "tool_call_finished",
        "content",
        "done",
    ]
    assert ask_tool.ainvoke.await_count == 1
