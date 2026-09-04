"""Tests for the checkpoint-prune beat task (Phase 3.1).

The checkpoint tables carry no timestamp columns, so pruning derives thread
age from the owning chat session (``thread_id`` == session id): stale sessions
(idle beyond ``AI_CHECKPOINT_RETENTION_DAYS``) and orphan threads (session row
gone) are pruned; live sessions are untouched.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.ai.graphs.checkpointer import CheckpointStore
from app.core.database import AsyncSessionLocal
from app.models.chat_model import ChatSession
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.workers.ai_tasks import prune_checkpoint_threads


async def _insert_checkpoint_rows(db, thread_id: str):
    await db.execute(
        text(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, "
            "checkpoint, metadata) VALUES (:t, '', :c, '{}'::jsonb, '{}'::jsonb)"
        ),
        {"t": thread_id, "c": uuid.uuid4().hex},
    )
    await db.execute(
        text(
            "INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, "
            "version, type, blob) VALUES (:t, '', 'ch', '1', 'json', :b)"
        ),
        {"t": thread_id, "b": b""},
    )
    await db.execute(
        text(
            "INSERT INTO checkpoint_writes (thread_id, checkpoint_ns, "
            "checkpoint_id, task_id, idx, channel, blob) "
            "VALUES (:t, '', :c, 'task', 0, 'ch', :b)"
        ),
        {"t": thread_id, "c": uuid.uuid4().hex, "b": b""},
    )


async def _checkpoint_thread_ids(db) -> set[str]:
    rows = await db.execute(text("SELECT DISTINCT thread_id FROM checkpoints"))
    return {r[0] for r in rows.fetchall()}


@pytest_asyncio.fixture
async def prune_setup() -> AsyncIterator[dict]:
    """Ensure checkpoint tables exist, then tenant + user + one FRESH and one
    STALE session, checkpoint rows for both plus an ORPHAN thread."""
    store = CheckpointStore(drain_timeout=2.0)
    await store.open()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    fresh_session_id = uuid.uuid4()
    stale_session_id = uuid.uuid4()
    orphan_thread = uuid.uuid4().hex

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name="T", slug=f"t-{tenant_id}"))
        await db.flush()
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
                id=fresh_session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                title="fresh",
            )
        )
        db.add(
            ChatSession(
                id=stale_session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                title="stale",
            )
        )
        await db.commit()

        # Make one session stale (TimestampMixin owns updated_at on writes,
        # so age is injected after the commit).
        await db.execute(
            ChatSession.__table__.update()
            .where(ChatSession.__table__.c.id == stale_session_id)
            .values(updated_at=datetime.now(timezone.utc) - timedelta(days=10_000))
        )
        await db.commit()

        await _insert_checkpoint_rows(db, str(fresh_session_id))
        await _insert_checkpoint_rows(db, str(stale_session_id))
        await _insert_checkpoint_rows(db, orphan_thread)
        await db.commit()

    try:
        yield {
            "fresh": str(fresh_session_id),
            "stale": str(stale_session_id),
            "orphan": orphan_thread,
        }
    finally:
        await store.close()
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("DELETE FROM checkpoint_writes WHERE thread_id = ANY(:t)"),
                {"t": [str(fresh_session_id)]},
            )
            await db.execute(
                text("DELETE FROM checkpoint_blobs WHERE thread_id = ANY(:t)"),
                {"t": [str(fresh_session_id)]},
            )
            await db.execute(
                text("DELETE FROM checkpoints WHERE thread_id = ANY(:t)"),
                {"t": [str(fresh_session_id)]},
            )
            await db.execute(
                ChatSession.__table__.delete().where(
                    ChatSession.__table__.c.id.in_([fresh_session_id, stale_session_id])
                )
            )
            await db.execute(
                UserModel.__table__.delete().where(UserModel.__table__.c.id == user_id)
            )
            await db.execute(
                TenantModel.__table__.delete().where(
                    TenantModel.__table__.c.id == tenant_id
                )
            )
            await db.commit()


@pytest.mark.asyncio
async def test_prune_removes_stale_and_orphan_threads_keeps_fresh(prune_setup):
    data = prune_setup

    async with AsyncSessionLocal() as db:
        before = await _checkpoint_thread_ids(db)
    assert {data["fresh"], data["stale"], data["orphan"]} <= before

    # The Celery task runs its own event loop in a thread (async_task);
    # offload so it doesn't collide with the test's running loop.
    result = await asyncio.to_thread(prune_checkpoint_threads)

    assert result["threads"] >= 2
    async with AsyncSessionLocal() as db:
        after = await _checkpoint_thread_ids(db)
    assert data["fresh"] in after, "live session's checkpoints must survive"
    assert data["stale"] not in after, "stale session's checkpoints must go"
    assert data["orphan"] not in after, "orphan threads must go"


@pytest.mark.asyncio
async def test_prune_is_idempotent(prune_setup):
    await asyncio.to_thread(prune_checkpoint_threads)
    result = await asyncio.to_thread(prune_checkpoint_threads)
    assert result["threads"] >= 0
    async with AsyncSessionLocal() as db:
        after = await _checkpoint_thread_ids(db)
    assert prune_setup["stale"] not in after
    assert prune_setup["orphan"] not in after
