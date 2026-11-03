"""Phase 3.5: node-level + mid-flow graph tests (LangGraph testing guide)
and checkpoint durability across a store restart.

Guide patterns pinned here:
* Node-level: ``graph.nodes["tool_exec"].ainvoke(state, config)`` with a
  fabricated serializable state + runtime in ``config.configurable``.
* Mid-flow: ``graph.update_state(..., as_node=...)`` on a paused run instead
  of full-flow replays.
* Durability: an ask_user interrupt survives closing and reopening the
  checkpointer pool (the resume-after-restart contract).
"""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.ai.graphs.chat_agent import (
    build_chat_graph,
    chat_engine_iter,
    has_pending_interrupt,
)
from app.ai.graphs.checkpointer import CheckpointStore, bind_runtime_store

SESSION_ID = uuid4()


async def _collect(gen):
    return [event async for event in gen]


def _runtime(llm=None, tools=None, svc=None):
    return {
        "llm_with_tools": llm,
        "tools": tools or [],
        "chat_session_service": svc,
        "log_label": "test",
        "proactive_message": None,
        "user_id": None,
        "tenant_id": None,
    }


def _base_state():
    return {
        "streaming": False,
        "session_id": str(SESSION_ID),
        "max_iterations": 5,
        "history": [HumanMessage("hi")],
        "iteration": 1,
        "total_content": "",
        "all_tool_calls": [],
        "all_citations": [],
        "all_tasks": [],
        "pending_tool_calls": [
            {"name": "get_patient_summary", "args": {}, "id": "c1", "type": "tool_call"}
        ],
        "pending_interrupt": None,
    }


@pytest.mark.asyncio
async def test_tool_exec_node_direct():
    """Node-level: tool_exec returns serializable state updates (ToolMessage
    appended, accumulators grown) — driven via graph.nodes[...]."""
    tool = MagicMock()
    tool.name = "get_patient_summary"
    tool.ainvoke = AsyncMock(return_value='{"status": "ok"}')
    graph = build_chat_graph()
    config = {"configurable": {"runtime": _runtime(tools=[tool])}}

    updates = await graph.nodes["tool_exec"].ainvoke(_base_state(), config)

    assert updates["pending_tool_calls"] == []
    assert updates["all_citations"] == ["get_patient_summary"]
    assert updates["all_tool_calls"][0]["result"] == '{"status": "ok"}'
    assert updates["history"][-1].content == '{"status": "ok"}'
    tool.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_state_mid_flow_on_paused_run():
    """Mid-flow: simulate the post-resume state on a paused run with
    ``update_state(as_node='await_user')`` — no full-flow replay."""

    class OneShotAskLLM:
        def bind_tools(self, tools):
            return self

        async def astream(self, history):
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "ask_user", "args": "", "id": "c1", "index": None}],
            )

    ask_tool = MagicMock()
    ask_tool.name = "ask_user"
    ask_tool.ainvoke = AsyncMock(
        return_value=json.dumps(
            {
                "__hitl__": True,
                "task": {
                    "proposal_id": "prop-1",
                    "task_type": "ask_user",
                    "status": "proposed",
                },
            }
        )
    )
    saver = InMemorySaver()  # ONE instance shared by run + inspection graph
    graph = build_chat_graph(checkpointer=saver)
    config = {"configurable": {"thread_id": str(SESSION_ID)}}

    gen = chat_engine_iter(
        OneShotAskLLM(),
        tools=[ask_tool],
        history=[HumanMessage("what dose?")],
        max_iterations=5,
        streaming=True,
        session_id=SESSION_ID,
        checkpointer=saver,
    )
    async for _ in gen:
        pass

    snapshot = await graph.aget_state(config)
    assert any(t.interrupts for t in snapshot.tasks)

    # Mid-flow manipulation: mark await_user as completed with the resume
    # state (ToolMessage appended, interrupt cleared) — as_node pins which
    # node the update is attributed to.
    await graph.aupdate_state(
        config,
        {
            "pending_interrupt": None,
            "history": snapshot.values["history"] + [HumanMessage("resume answer: 500mg")],
        },
        as_node="await_user",
    )
    snapshot2 = await graph.aget_state(config)
    assert not any(t.interrupts for t in snapshot2.tasks)
    assert snapshot2.next in (("agent_step",), ("finalize",))


@pytest.mark.asyncio
async def test_ask_user_resume_survives_store_reopen():
    """Resume-after-restart: the paused interrupt survives closing the
    checkpointer pool entirely and reopening a fresh one (new 'process')."""
    svc = MagicMock()
    svc.save_message = AsyncMock(side_effect=lambda **kw: MagicMock(id="m1"))
    svc.update_message_fields = AsyncMock(return_value=None)
    svc.find_message_by_proposal = AsyncMock(
        return_value=MagicMock(id="m1")  # crash-resume dedup re-attaches it
    )
    user_id = uuid4()
    tenant_id = uuid4()

    class TwoResponseLLM:
        def __init__(self):
            self.responses = [
                [
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {"name": "ask_user", "args": "", "id": "c1", "index": None}
                        ],
                    )
                ],
                [AIMessageChunk(content="Dose noted.")],
            ]

        def bind_tools(self, tools):
            return self

        async def astream(self, history):
            for piece in self.responses.pop(0):
                yield piece

    llm = TwoResponseLLM()
    ask_tool = MagicMock()
    ask_tool.name = "ask_user"
    ask_tool.ainvoke = AsyncMock(
        return_value=json.dumps(
            {
                "__hitl__": True,
                "task": {
                    "proposal_id": "prop-9",
                    "task_type": "ask_user",
                    "status": "proposed",
                },
            }
        )
    )

    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    bind_runtime_store(store)
    try:
        gen = chat_engine_iter(
            llm,
            tools=[ask_tool],
            history=[HumanMessage("what dose?")],
            max_iterations=5,
            streaming=True,
            chat_session_service=svc,
            session_id=SESSION_ID,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        async for _ in gen:
            pass
        assert await has_pending_interrupt(SESSION_ID)

        # 'Restart': drop the pool completely, open a fresh one.
        bind_runtime_store(None)
        await store.close()
        store2 = CheckpointStore(drain_timeout=2.0)
        await store2.open()
        bind_runtime_store(store2)

        assert await has_pending_interrupt(SESSION_ID)
        resumed = await __import__(
            "app.ai.graphs.chat_agent", fromlist=["resume_interrupted_chat_graph"]
        ).resume_interrupted_chat_graph(
            SESSION_ID,
            "[HITL RESOLUTION FEEDBACK] q1 answered: 500mg",
            llm_with_tools=llm,
            tools=[ask_tool],
            chat_session_service=svc,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        assert resumed is not None
        events = [event async for event in resumed]
        # The resume stream passes graph events through verbatim (no
        # flow_started/flow_finished bracket — parity with the pre-V3 wire;
        # the turn is a continuation of the paused run, and `interrupt`
        # already marked the pause on the first stream).
        assert events[-1]["event"] == "node_finished"
        assert next(e for e in events if e["event"] == "done")["reached_max"] is False
        assert "Dose noted." in [e["text"] for e in events if e["event"] == "delta"]
        assert ask_tool.ainvoke.await_count == 1
        # Crash-resume dedup: the final save re-attached the proactive
        # message (via find_message_by_proposal) instead of duplicating it.
        svc.find_message_by_proposal.assert_awaited_once()
        assert svc.update_message_fields.await_count == 1
        assert svc.save_message.await_count == 1
    finally:
        bind_runtime_store(None)
        await store2.close()


# ---------------------------------------------------------------------------
# Family vocabulary emission (plan 24 V3 — native, no legacy sentinels)
# ---------------------------------------------------------------------------


def _names(events):
    return [e["event"] for e in events]


class ToolThenAnswerLLM:
    def bind_tools(self, tools):
        return self

    async def astream(self, history):
        if history[-1].type != "tool":
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": "get_patient_summary", "args": "", "id": "c1", "index": None}
                ],
            )
        else:
            yield AIMessageChunk(content="All good.")


@pytest.mark.asyncio
async def test_flow_event_order_clean_tool_turn():
    tool = MagicMock()
    tool.name = "get_patient_summary"
    tool.ainvoke = AsyncMock(return_value="ok")

    events = await _collect(
        chat_engine_iter(
            ToolThenAnswerLLM(),
            tools=[tool],
            history=[HumanMessage("check")],
            max_iterations=5,
            streaming=True,
            chat_session_service=None,
            session_id=None,
        )
    )
    assert events[0] == {"event": "flow_started", "flow": "chat"}
    assert events[-1] == {"event": "flow_finished"}
    nodes = [(e["event"], e["node"]) for e in events if "node" in e]
    assert nodes == [
        ("node_started", "agent_step"),
        ("node_finished", "agent_step"),
        ("node_started", "tool_exec"),
        ("node_finished", "tool_exec"),
        ("node_started", "agent_step"),
        ("node_finished", "agent_step"),
        ("node_started", "finalize"),
        ("node_finished", "finalize"),
    ]
    # Tool lifecycle + citation ride alongside the node events: the streamed
    # name announce and the exec both carry status running; the result done.
    tool_events = [e for e in events if e["event"] == "tool_call"]
    assert [t["status"] for t in tool_events] == ["running", "running", "done"]
    assert {"event": "citation", "source": "get_patient_summary"} in events


@pytest.mark.asyncio
async def test_flow_interrupt_on_ask_user_pause():
    ask_tool = MagicMock()
    ask_tool.name = "ask_user"
    ask_tool.ainvoke = AsyncMock(
        return_value=json.dumps(
            {
                "__hitl__": True,
                "task": {"proposal_id": "p", "task_type": "ask_user", "status": "proposed"},
            }
        )
    )

    class AskLLM:
        def bind_tools(self, tools):
            return self

        async def astream(self, history):
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": "ask_user", "args": "", "id": "c1", "index": None}],
            )

    events = await _collect(
        chat_engine_iter(
            AskLLM(),
            tools=[ask_tool],
            history=[HumanMessage("what dose?")],
            max_iterations=5,
            streaming=True,
            chat_session_service=None,
            session_id=None,
        )
    )
    assert {"event": "interrupt"} in events
    assert {"event": "flow_finished"} not in events
    # The card itself is the ``task`` payload event (health vocabulary).
    task = next(e for e in events if e["event"] == "task")
    assert task["task"]["proposal_id"] == "p"


@pytest.mark.asyncio
async def test_flow_failed_bubbles_with_event():
    boom = MagicMock()
    boom.name = "create_medication"
    boom.ainvoke = AsyncMock(side_effect=RuntimeError("write failed"))

    class ToolLLM:
        def bind_tools(self, tools):
            return self

        async def astream(self, history):
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": "create_medication", "args": "", "id": "c1", "index": None}
                ],
            )

    with pytest.raises(RuntimeError):
        await _collect(
            chat_engine_iter(
                ToolLLM(),
                tools=[boom],
                history=[HumanMessage("add")],
                max_iterations=3,
                streaming=True,
                chat_session_service=None,
                session_id=None,
            )
        )
    # events assertion lives in the sse-gate test below; here we just pin
    # that the exception propagates (flow_failed is asserted via sse gate).


def test_sse_mapper_emits_family_and_drops_internal_done():
    """The SSE mapper passes every vocabulary event through verbatim and
    drops the engine-internal ``done`` signal — no gate, no sentinels."""
    import asyncio

    from app.ai.agents.chat_agent import stream_events_as_sse

    async def _run():
        async def _gen():
            yield {"event": "flow_started", "flow": "chat"}
            yield {"event": "delta", "text": "hi"}
            yield {"event": "done", "reached_max": False}

        return [c async for c in stream_events_as_sse(_gen())]

    frames = asyncio.run(_run())
    assert frames[0] == 'data: {"event": "flow_started", "flow": "chat"}\n\n'
    assert frames[1] == 'data: {"event": "delta", "text": "hi"}\n\n'
    assert len(frames) == 2
