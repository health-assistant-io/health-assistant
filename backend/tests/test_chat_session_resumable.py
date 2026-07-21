"""Integration tests for ``ChatSessionService.find_resumable_message`` and
``find_message_by_proposal`` against a real Postgres instance.

Pins the bug where the resume continuation turn fell back to a generic
message (instead of the structured HITL RESOLUTION FEEDBACK) because the
"has tasks?" filter was ``tasks IS NOT NULL`` — which incorrectly matched
rows with ``tasks = 'null'::jsonb`` or ``tasks = '[]'``. The fix uses
``jsonb_array_length(tasks) > 0`` which excludes SQL NULL, JSON null, AND
empty arrays.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.chat_model import ChatMessage, ChatSession
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.chat_session_service import ChatSessionService


@pytest_asyncio.fixture
async def chat_setup() -> AsyncIterator[dict]:
    """Spin up a tenant + user + chat session for the test, then tear down."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        # Tenant first (FK target).
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        await db.flush()
        # User row is referenced by ChatSession.user_id FK. Use a minimal stub.
        db.add(
            UserModel(
                id=user_id,
                email=f"u-{user_id}@test.local",
                hashed_password="x",
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
        # Cascade deletes clean up messages; the session row cleanup also
        # nukes the user + tenant we created.
        async with AsyncSessionLocal() as db:
            await db.execute(
                ChatSession.__table__.delete().where(
                    ChatSession.__table__.c.id == session_id
                )
            )
            await db.execute(
                UserModel.__table__.delete().where(
                    UserModel.__table__.c.id == user_id
                )
            )
            await db.execute(
                TenantModel.__table__.delete().where(
                    TenantModel.__table__.c.id == tenant_id
                )
            )
            await db.commit()


async def _add_message(
    session_id, role="assistant", content=None, tasks=None, tool_calls=None
) -> str:
    """Insert one ChatMessage and return its id."""
    msg_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            ChatMessage(
                id=msg_id,
                session_id=session_id,
                role=role,
                content=content or {"text": "..."},
                tool_calls=tool_calls,
                tasks=tasks,
            )
        )
        await db.commit()
    return str(msg_id)


# ---------------------------------------------------------------------------
# find_resumable_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_resumable_returns_message_with_tasks(chat_setup):
    """Baseline: a task-bearing message IS returned."""
    await _add_message(
        chat_setup["session_id"],
        tasks=[{"proposal_id": "p1", "task_type": "ask_user", "status": "confirmed"}],
    )
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_resumable_message(
            chat_setup["session_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is not None
    assert msg.tasks and len(msg.tasks) == 1
    assert msg.tasks[0]["proposal_id"] == "p1"


@pytest.mark.asyncio
async def test_find_resumable_skips_audit_note_with_null_tasks(chat_setup):
    """The /resolve endpoint saves an audit-note assistant message with no
    tasks AFTER updating the original ask_user message. find_resumable MUST
    skip the audit-note (SQL NULL tasks) and return the original message."""
    original_id = await _add_message(
        chat_setup["session_id"],
        tasks=[{"proposal_id": "p1", "task_type": "ask_user", "status": "confirmed"}],
    )
    # Audit-note: tasks default to None → SQL NULL.
    await _add_message(
        chat_setup["session_id"],
        role="assistant",
        content={"text": "✓ The user answered: ..."},
        tasks=None,
    )
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_resumable_message(
            chat_setup["session_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is not None
    assert str(msg.id) == original_id, (
        "find_resumable_message returned the audit-note instead of the "
        "task-bearing ask_user message — this is the root cause of the "
        "'I can't see which biomarker was selected' bug."
    )


@pytest.mark.asyncio
async def test_find_resumable_skips_empty_tasks_array(chat_setup):
    """An empty-tasks message (``tasks = []``) is NOT a resumable message."""
    await _add_message(
        chat_setup["session_id"],
        tasks=[],  # JSONB '[]'
    )
    await _add_message(
        chat_setup["session_id"],
        tasks=[{"proposal_id": "real", "task_type": "ask_user", "status": "confirmed"}],
    )
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_resumable_message(
            chat_setup["session_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is not None
    assert msg.tasks[0]["proposal_id"] == "real"


@pytest.mark.asyncio
async def test_find_resumable_skips_json_null_tasks(chat_setup):
    """A row with ``tasks = 'null'::jsonb`` (JSON null, distinct from SQL
    NULL) is NOT a resumable message. SQLAlchemy's ``isnot(None)`` matches
    JSON null (returns True), so the old query would erroneously return this
    row and ``target.tasks`` would deserialize to Python None, triggering
    the graceful-fallback branch in ``resume_after_hitl``."""
    # Insert via raw SQL so we can put the literal JSON 'null' in.
    from sqlalchemy import text

    msg_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO chat_messages (id, session_id, role, content, tasks) "
                "VALUES (:id, :sid, 'assistant', '{\"text\":\"x\"}'::jsonb, 'null'::jsonb)"
            ),
            {"id": msg_id, "sid": chat_setup["session_id"]},
        )
        await db.commit()

    # Add a REAL task-bearing message after the JSON-null one.
    real_id = await _add_message(
        chat_setup["session_id"],
        tasks=[{"proposal_id": "p1", "task_type": "ask_user", "status": "confirmed"}],
    )

    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_resumable_message(
            chat_setup["session_id"],
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is not None
    assert str(msg.id) == real_id, (
        "find_resumable_message returned the JSON-null-tasks row instead of "
        "the real task-bearing row. The query must use jsonb_array_length > 0."
    )


# ---------------------------------------------------------------------------
# find_message_by_proposal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_by_proposal_skips_null_tasks(chat_setup):
    """``/resolve`` looks up the task by proposal_id; it must NOT match
    audit-note messages with NULL tasks (which would crash with IndexError
    on ``tasks[task_idx]``)."""
    await _add_message(
        chat_setup["session_id"],
        tasks=[{"proposal_id": "p1", "task_type": "ask_user", "status": "confirmed"}],
    )
    # Audit-note after.
    await _add_message(
        chat_setup["session_id"],
        role="assistant",
        content={"text": "✓ ..."},
        tasks=None,
    )

    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.find_message_by_proposal(
            chat_setup["session_id"],
            "p1",
            chat_setup["user_id"],
            chat_setup["tenant_id"],
        )
    assert msg is not None
    assert any(t.get("proposal_id") == "p1" for t in (msg.tasks or []))
