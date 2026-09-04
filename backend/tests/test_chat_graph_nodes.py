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
from app.core.config import settings

SESSION_ID = uuid4()


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
async def test_update_state_mid_flow_on_paused_run(monkeypatch):
    """Mid-flow: simulate the post-resume state on a paused run with
    ``update_state(as_node='await_user')`` — no full-flow replay."""
    monkeypatch.setattr(settings, "AI_AGENT_ENGINE", "graph")

    class OneShotAskLLM:
        def bind_tools(self, tools):
            return self

        async def astream(self, history):
            yield AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": "ask_user", "args": "", "id": "c1", "index": None}
                ],
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
            "history": snapshot.values["history"]
            + [HumanMessage("resume answer: 500mg")],
        },
        as_node="await_user",
    )
    snapshot2 = await graph.aget_state(config)
    assert not any(t.interrupts for t in snapshot2.tasks)
    assert snapshot2.next in (("agent_step",), ("finalize",))


@pytest.mark.asyncio
async def test_ask_user_resume_survives_store_reopen(monkeypatch):
    """Resume-after-restart: the paused interrupt survives closing the
    checkpointer pool entirely and reopening a fresh one (new 'process')."""
    monkeypatch.setattr(settings, "AI_AGENT_ENGINE", "graph")
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
        assert events[-1] == ("done", False)
        assert "Dose noted." in [d for k, d in events if k == "content"]
        assert ask_tool.ainvoke.await_count == 1
        # Crash-resume dedup: the final save re-attached the proactive
        # message (via find_message_by_proposal) instead of duplicating it.
        svc.find_message_by_proposal.assert_awaited_once()
        assert svc.update_message_fields.await_count == 1
        assert svc.save_message.await_count == 1
    finally:
        bind_runtime_store(None)
        await store2.close()
