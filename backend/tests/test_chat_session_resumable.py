"""Integration tests for the chat HITL lookup paths against real Postgres.

Plan 5 moved chat cards out of ``chat_messages.tasks`` into the one
``proposals`` table (source='chat', source_ref=message id, ordinal=card order).
These tests pin the two lookups the resolve/resume paths depend on:

* ``find_resumable_message`` returns the message whose proposals drive a
  resume turn — never a card-less audit note, never a plain reply;
* ``find_message_by_proposal`` resolves a proposal id back to its emitting
  message, session- and tenant-scoped.

They also pin the wire projection: ``get_session_messages`` still attaches the
ADR-0015 ``tasks`` array (ordered by ``ordinal``) even though the column is
gone.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.models.chat_model import ChatSession
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.chat.chat_session_service import ChatSessionService


@pytest_asyncio.fixture
async def chat_setup() -> AsyncIterator[dict]:
    """Spin up a tenant + user + chat session for the test, then tear down."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"u-{user_id}@test.local",
                password_hash="x",
                tenant_id=tenant_id,
                role="USER",
            )
        )
        await db.flush()
        db.add(
            ChatSession(
                id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                title="test",
            )
        )
        await db.commit()

    try:
        yield {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_id": session_id,
        }
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                ChatSession.__table__.delete().where(ChatSession.__table__.c.id == session_id)
            )
            await db.execute(
                UserModel.__table__.delete().where(UserModel.__table__.c.id == user_id)
            )
            await db.execute(
                TenantModel.__table__.delete().where(TenantModel.__table__.c.id == tenant_id)
            )
            await db.commit()


def _card(task_type: str = "ask_user", title: str = "Question") -> dict:
    return {
        "schema_version": 1,
        "proposal_id": str(uuid.uuid4()),
        "task_type": task_type,
        "title": title,
        "status": "proposed",
        "proposed_payload": {"questions": []},
        "context": {},
    }


async def _save(session_id, *, tasks=None, text="...") -> tuple[uuid.UUID, list[dict]]:
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        message = await svc.save_message(
            session_id=session_id,
            role="assistant",
            content={"text": text},
            tasks=tasks,
        )
        return message.id, list(tasks or [])


# ---------------------------------------------------------------------------
# find_resumable_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_resumable_returns_card_message(chat_setup):
    card = _card()
    message_id, _ = await _save(chat_setup["session_id"], tasks=[card])
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_resumable_message(
            chat_setup["session_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is not None
    assert msg.id == message_id
    assert msg.tasks[0]["proposal_id"] == card["proposal_id"]


@pytest.mark.asyncio
async def test_find_resumable_skips_audit_note(chat_setup):
    """The resolve path appends a card-less assistant note after the card
    message; the resume must still find the card-bearing message."""
    original_id, _ = await _save(chat_setup["session_id"], tasks=[_card()])
    await _save(chat_setup["session_id"], text="✓ The user answered: ...")

    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_resumable_message(
            chat_setup["session_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is not None
    assert msg.id == original_id


@pytest.mark.asyncio
async def test_find_resumable_none_when_no_cards(chat_setup):
    await _save(chat_setup["session_id"])
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_resumable_message(
            chat_setup["session_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is None


@pytest.mark.asyncio
async def test_find_resumable_returns_newest_card_message(chat_setup):
    await _save(chat_setup["session_id"], tasks=[_card()])
    newest_id, _ = await _save(chat_setup["session_id"], tasks=[_card()])
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_resumable_message(
            chat_setup["session_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is not None
    assert msg.id == newest_id


# ---------------------------------------------------------------------------
# find_message_by_proposal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_by_proposal_returns_emitting_message(chat_setup):
    card = _card()
    message_id, _ = await _save(chat_setup["session_id"], tasks=[card])
    await _save(chat_setup["session_id"], text="✓ ...")

    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_message_by_proposal(
            chat_setup["session_id"],
            card["proposal_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is not None
    assert msg.id == message_id
    assert msg.tasks[0]["proposal_id"] == card["proposal_id"]


@pytest.mark.asyncio
async def test_find_by_proposal_unknown_returns_none(chat_setup):
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_message_by_proposal(
            chat_setup["session_id"],
            str(uuid.uuid4()),
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is None


@pytest.mark.asyncio
async def test_find_by_proposal_wrong_session_returns_none(chat_setup):
    card = _card()
    await _save(chat_setup["session_id"], tasks=[card])
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_message_by_proposal(
            uuid.uuid4(),
            card["proposal_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is None


# ---------------------------------------------------------------------------
# wire projection (tasks array is in-memory, ordered by ordinal)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_messages_carry_cards_in_ordinal_order(chat_setup):
    first, second = _card(title="First"), _card(title="Second")
    await _save(chat_setup["session_id"], tasks=[first, second])
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        messages = await svc.get_session_messages(
            chat_setup["session_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert len(messages) == 1
    cards = messages[0].tasks
    assert [card["title"] for card in cards] == ["First", "Second"]
    assert cards[0]["status"] == "proposed"
    assert cards[0]["task_type"] == "ask_user"
    assert cards[0]["resolved"] is None


@pytest.mark.asyncio
async def test_delete_session_removes_its_proposals(chat_setup):
    """Chat proposals use a polymorphic source_ref (no FK): deleting the
    session must clean them up explicitly, not leave orphans."""
    from app.models.proposal import Proposal

    card = _card()
    await _save(chat_setup["session_id"], tasks=[card])
    async with AsyncSessionLocal() as db:
        await ChatSessionService(db).delete_session(chat_setup["session_id"], chat_setup["user_id"])
        row = await db.get(Proposal, uuid.UUID(card["proposal_id"]))
    assert row is None


@pytest.mark.asyncio
async def test_plain_messages_carry_an_empty_tasks_list(chat_setup):
    await _save(chat_setup["session_id"])
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        messages = await svc.get_session_messages(
            chat_setup["session_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert messages[0].tasks == []
