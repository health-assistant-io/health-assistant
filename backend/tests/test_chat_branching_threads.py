"""Branch threads x ask_user interrupts — the plan 13 H2 gate bundle on the
LangGraph thread strategy (plan 11 §6 health ruling: new branch ⇒ new
``thread_id = f"{session_id}:{branch_tip_id}"``).

Gate (a) interrupt-survives-branch: a paused ask_user on branch A stays
resumable after branch B is created and selected.

Gate (c) edit during an interrupted turn — the H2 ruling is
COMPLETE-AND-BRANCH: the edit runs on a NEW thread (the old pause's thread
is never touched), and a stray send on the SAME branch supersedes the
abandoned pause (thread deleted; the still-visible card degrades to the
fresh-continuation resume path).
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage

from app.ai.assistance.service import AIAssistanceService
from app.ai.graphs.chat_agent import (
    chat_engine_iter,
    has_pending_interrupt,
    resume_interrupted_chat_graph,
)
from app.ai.graphs.checkpointer import CheckpointStore, bind_runtime_store
from app.models.enums import Role
from app.services.chat_branching import (
    branch_thread_key,
    card_thread_key,
    regenerate_thread_key,
)
from app.services.chat_session_service import ChatSessionService
from tests._auth_helpers import create_tenant, create_user

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


def _ask_observation(proposal_id: str = "prop-1") -> str:
    return json.dumps(
        {
            "__hitl__": True,
            "task": {
                "schema_version": 2,
                "proposal_id": proposal_id,
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


async def _collect(gen):
    return [event async for event in gen]


def _names(events):
    return [e["event"] for e in events]


def _ask_chunk():
    return [
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": "ask_user", "args": "", "id": "call_ask_1", "index": None}],
        )
    ]


def _text_chunk(text):
    return [AIMessageChunk(content=text)]


async def _setup_real_session():
    """A real (session, user, tenant, service) triple over the test DB."""
    from app.core.database import AsyncSessionLocal

    tenant_id = await create_tenant()
    user = await create_user(role=Role.USER, tenant_id=tenant_id)
    db = AsyncSessionLocal()
    svc = ChatSessionService(db)
    session = await svc.create_session(user.id, tenant_id)
    return db, svc, session, user, tenant_id


@pytest.mark.asyncio
async def test_interrupt_survives_branch_and_stays_resumable():
    """Gate (a): branch A pauses at ask_user; branch B is created and
    selected; branch A's pause is still resumable through the card-derived
    thread."""
    db, svc, session, user, tenant_id = await _setup_real_session()
    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    bind_runtime_store(store)
    try:
        ask_tool = _ask_user_tool(_ask_observation("prop-a"))
        llm_a = ScriptedStreamingLLM([_ask_chunk(), _text_chunk("Thanks! Dose noted.")])

        # Branch A = the original spine: a pausing ask_user turn on the
        # legacy thread (the chain key of an un-branched session).
        u1 = await svc.save_message(
            session_id=session.id,
            role="user",
            content={"text": "what dose?"},
            owner_user_id=user.id,
        )
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        thread_a = branch_thread_key(session.id, all_messages, u1)
        assert thread_a == str(session.id)

        events1 = await _collect(
            chat_engine_iter(
                llm_a,
                tools=[ask_tool],
                history=[SystemMessage(content="s"), HumanMessage(content="what dose?")],
                max_iterations=5,
                streaming=True,
                chat_session_service=svc,
                session_id=session.id,
                user_id=user.id,
                tenant_id=tenant_id,
                thread_id=thread_a,
            )
        )
        assert _names(events1)[-1] == "interrupt"
        assert await has_pending_interrupt(thread_a)

        # Branch B: edit the question. New sibling ⇒ new thread; the edit
        # turn runs AND completes on it (gate c: complete-and-branch).
        _session_row, edited = await svc.edit_message(
            u1.id, user.id, tenant_id, {"text": "what dose, for metformin?"}
        )
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        thread_b = branch_thread_key(session.id, all_messages, edited)
        assert thread_b == f"{session.id}:{edited.id}"
        assert thread_b != thread_a

        llm_b = ScriptedStreamingLLM([_text_chunk("500mg is typical.")])
        events2 = await _collect(
            chat_engine_iter(
                llm_b,
                tools=[],
                history=[SystemMessage(content="s"), HumanMessage(content="edited")],
                max_iterations=5,
                streaming=True,
                chat_session_service=svc,
                session_id=session.id,
                user_id=user.id,
                tenant_id=tenant_id,
                thread_id=thread_b,
            )
        )
        assert _names(events2)[-1] == "flow_finished"

        # Branch A's pause survived branch B's turn untouched.
        assert await has_pending_interrupt(thread_a)
        assert not await has_pending_interrupt(thread_b)

        # Select branch A (level-flip the root) — the pause is still there.
        await svc.select_message(u1.id, user.id, tenant_id)
        assert await has_pending_interrupt(thread_a)

        # The card row was persisted proactively on branch A; the card-
        # derived thread IS branch A's thread (cards attach to messages).
        card = await svc.find_message_by_proposal(session.id, "prop-a", user.id, tenant_id)
        assert card is not None
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        assert card_thread_key(session.id, all_messages, card) == thread_a

        # /resume on branch A continues the SAME conversation.
        summary = "[HITL RESOLUTION FEEDBACK] q_dose answered: 500mg"
        resumed = await resume_interrupted_chat_graph(
            session.id,
            summary,
            llm_with_tools=llm_a,
            tools=[ask_tool],
            chat_session_service=svc,
            log_label="AI Assistance (resume)",
            user_id=user.id,
            tenant_id=tenant_id,
            thread_id=thread_a,
        )
        assert resumed is not None
        events3 = await _collect(resumed)
        assert "Thanks! Dose noted." in [e["text"] for e in events3 if e["event"] == "delta"]
        assert not await has_pending_interrupt(thread_a)
        assert ask_tool.ainvoke.await_count == 1
    finally:
        bind_runtime_store(None)
        await store.close()
        await db.close()


@pytest.mark.asyncio
async def test_regenerated_variant_pause_keys_on_reroll_anchor():
    """A re-roll under U pauses on the ``{session}:{U}`` thread, and a card
    on the regenerated sibling derives the SAME thread (turn-side and
    resume-side key rules agree)."""
    db, svc, session, user, tenant_id = await _setup_real_session()
    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    bind_runtime_store(store)
    try:
        ask_tool = _ask_user_tool(_ask_observation("prop-r"))
        llm = ScriptedStreamingLLM([_ask_chunk(), _text_chunk("Resumed.")])

        u1 = await svc.save_message(
            session_id=session.id,
            role="user",
            content={"text": "what dose?"},
            owner_user_id=user.id,
        )
        a1 = await svc.save_message(
            session_id=session.id, role="assistant", content={"text": "old reply"}
        )
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)

        # A regenerated turn keys on the re-roll anchor — NOT the legacy
        # thread, so it never touches a paused predecessor on the spine.
        reroll_thread = regenerate_thread_key(session.id, all_messages, u1)
        assert reroll_thread != str(session.id)

        events = await _collect(
            chat_engine_iter(
                llm,
                tools=[ask_tool],
                history=[SystemMessage(content="s"), HumanMessage(content="what dose?")],
                max_iterations=5,
                streaming=True,
                chat_session_service=svc,
                session_id=session.id,
                user_id=user.id,
                tenant_id=tenant_id,
                thread_id=reroll_thread,
                turn_parent_id=u1.id,
            )
        )
        assert _names(events)[-1] == "interrupt"
        assert await has_pending_interrupt(reroll_thread)
        assert not await has_pending_interrupt(str(session.id))

        # The regenerated card row is a NEW sibling under u1 (a1 kept), and
        # the card-derived thread matches the turn's thread.
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        card = await svc.find_message_by_proposal(session.id, "prop-r", user.id, tenant_id)
        assert card is not None
        assert card.parent_id == u1.id
        assert card_thread_key(session.id, all_messages, card) == reroll_thread

        # And the re-roll's own pause supersedes cleanly when a later re-roll
        # under the same anchor arrives (documented H2 ruling).
        resumed = await resume_interrupted_chat_graph(
            session.id,
            "answered",
            llm_with_tools=llm,
            tools=[ask_tool],
            chat_session_service=svc,
            user_id=user.id,
            tenant_id=tenant_id,
            thread_id=reroll_thread,
        )
        assert resumed is not None
        await _collect(resumed)
        assert not await has_pending_interrupt(reroll_thread)
        _ = a1  # the old reply stays a switchable variant
    finally:
        bind_runtime_store(None)
        await store.close()
        await db.close()


@pytest.mark.asyncio
async def test_stray_send_supersedes_abandoned_pause():
    """Gate (c) companion ruling: a NEW turn on a branch whose thread is
    paused supersedes the abandoned pause (thread deleted) — the fresh turn
    actually runs instead of being swallowed by the stale interrupt, and the
    still-visible card degrades to the fresh-continuation resume path."""
    db, svc, session, user, tenant_id = await _setup_real_session()
    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    bind_runtime_store(store)
    try:
        ask_tool = _ask_user_tool(_ask_observation("prop-s"))
        llm = ScriptedStreamingLLM([_ask_chunk()])

        u1 = await svc.save_message(
            session_id=session.id,
            role="user",
            content={"text": "what dose?"},
            owner_user_id=user.id,
        )
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        thread = branch_thread_key(session.id, all_messages, u1)
        events = await _collect(
            chat_engine_iter(
                llm,
                tools=[ask_tool],
                history=[SystemMessage(content="s"), HumanMessage(content="what dose?")],
                max_iterations=5,
                streaming=True,
                chat_session_service=svc,
                session_id=session.id,
                user_id=user.id,
                tenant_id=tenant_id,
                thread_id=thread,
            )
        )
        assert _names(events)[-1] == "interrupt"
        assert await has_pending_interrupt(thread)

        # The user ignores the card and sends a new message on the SAME
        # branch: _chat_stream's supersede step deletes the paused thread
        # before the fresh turn runs.
        u2 = await svc.save_message(
            session_id=session.id,
            role="user",
            content={"text": "never mind, tell me about metformin"},
            owner_user_id=user.id,
        )
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        thread_now = branch_thread_key(session.id, all_messages, u2)
        assert thread_now == thread  # same branch ⇒ same thread

        service = AIAssistanceService(db)
        await service._supersede_abandoned_pause(thread_now)
        assert not await has_pending_interrupt(thread_now)

        llm2 = ScriptedStreamingLLM([_text_chunk("Metformin overview…")])
        events2 = await _collect(
            chat_engine_iter(
                llm2,
                tools=[],
                history=[SystemMessage(content="s"), HumanMessage(content="metformin?")],
                max_iterations=5,
                streaming=True,
                chat_session_service=svc,
                session_id=session.id,
                user_id=user.id,
                tenant_id=tenant_id,
                thread_id=thread_now,
            )
        )
        # The turn ran to completion — no swallow by a stale interrupt.
        assert _names(events2)[-1] == "flow_finished"
        assert "Metformin overview…" in [e["text"] for e in events2 if e["event"] == "delta"]
    finally:
        bind_runtime_store(None)
        await store.close()
        await db.close()


@pytest.mark.asyncio
async def test_resume_falls_back_when_pause_was_superseded():
    """The superseded card stays answerable: with the pause gone, the resume
    path degrades to a fresh continuation turn (existing graceful fallback —
    resume_interrupted_chat_graph returns None)."""
    db, svc, session, user, tenant_id = await _setup_real_session()
    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    bind_runtime_store(store)
    try:
        u1 = await svc.save_message(
            session_id=session.id,
            role="user",
            content={"text": "hi"},
            owner_user_id=user.id,
        )
        _ = u1
        all_messages = await svc.get_session_messages(session.id, user.id, tenant_id)
        thread = branch_thread_key(session.id, all_messages, u1)
        resumed = await resume_interrupted_chat_graph(
            session.id,
            "summary",
            llm_with_tools=ScriptedStreamingLLM([]),
            tools=[],
            chat_session_service=svc,
            user_id=user.id,
            tenant_id=tenant_id,
            thread_id=thread,
        )
        assert resumed is None
    finally:
        bind_runtime_store(None)
        await store.close()
        await db.close()


@pytest.mark.asyncio
async def test_turn_and_card_keys_agree_across_shapes():
    """Exhaustive key agreement: for every turn shape (send / edit /
    regenerate / resume-fallback), the thread the TURN ran on equals the
    thread derived from the CARD that turn would pause."""
    db, svc, session, user, tenant_id = await _setup_real_session()
    try:
        u1 = await svc.save_message(
            session_id=session.id,
            role="user",
            content={"text": "Q1"},
            owner_user_id=user.id,
        )
        a1 = await svc.save_message(session_id=session.id, role="assistant", content={"text": "A1"})
        _session2, edited = await svc.edit_message(u1.id, user.id, tenant_id, {"text": "Q1'"})
        a2 = await svc.save_message(
            session_id=session.id, role="assistant", content={"text": "A1'"}
        )

        def reply_under(parent, text):
            return svc.save_message(
                session_id=session.id, role="assistant", content={"text": text}, parent_id=parent.id
            )

        all = await svc.get_session_messages(session.id, user.id, tenant_id)

        # Plain send on the spine.
        assert card_thread_key(session.id, all, a1) == branch_thread_key(session.id, all, u1)
        # Edit turn: reply under the edited sibling.
        assert card_thread_key(session.id, all, a2) == branch_thread_key(session.id, all, edited)
        # Regenerate under u1: reply is a second sibling.
        a1b = await reply_under(u1, "A1 rerolled")
        all = await svc.get_session_messages(session.id, user.id, tenant_id)
        assert card_thread_key(session.id, all, a1b) == regenerate_thread_key(session.id, all, u1)
        # Resume continuation rows under the card message keep its thread.
        feedback = await svc.save_message(
            session_id=session.id,
            role="user",
            content={"text": "[HITL RESOLUTION FEEDBACK] …", "kind": "hitl_feedback"},
            parent_id=a1b.id,
        )
        a1c = await reply_under(feedback, "continued")
        all = await svc.get_session_messages(session.id, user.id, tenant_id)
        assert card_thread_key(session.id, all, a1c) == regenerate_thread_key(session.id, all, u1)
        assert feedback.parent_id == a1b.id
    finally:
        await db.close()
