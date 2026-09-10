"""Audit 2026-09-11 S-1 — chat session ownership.

A client-supplied ``session_id`` must belong to the caller before any write
or checkpointer attach against it:

- ``ChatSessionService.get_owned_session`` returns None for foreign sessions.
- ``save_message(owner_user_id=...)`` raises ``LookupError`` instead of
  writing attacker-controlled content into another member's session.
- ``/ai-assistance/stream`` rejects a foreign ``session_id`` with a ``guard``
  error payload and persists no message into the victim's session.
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio

from app.core.database import AsyncSessionLocal
from app.models.chat_model import ChatMessage, ChatSession
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services.chat_session_service import ChatSessionService


@pytest_asyncio.fixture
async def chat_ownership_setup() -> AsyncIterator[dict]:
    """Two users in one tenant: an owner session and a victim session."""
    tenant_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    victim_id = uuid.uuid4()
    owner_session_id = uuid.uuid4()
    victim_session_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(TenantModel(id=tenant_id, name=f"T-{tenant_id}", slug=f"t-{tenant_id}"))
        await db.flush()
        db.add(
            UserModel(
                id=owner_id,
                email=f"owner-{owner_id}@test.local",
                hashed_password="x",
                tenant_id=tenant_id,
                role="USER",
            )
        )
        db.add(
            UserModel(
                id=victim_id,
                email=f"victim-{victim_id}@test.local",
                hashed_password="x",
                tenant_id=tenant_id,
                role="USER",
            )
        )
        await db.flush()
        db.add(
            ChatSession(
                id=owner_session_id,
                tenant_id=tenant_id,
                user_id=owner_id,
                title="owner",
            )
        )
        db.add(
            ChatSession(
                id=victim_session_id,
                tenant_id=tenant_id,
                user_id=victim_id,
                title="victim",
            )
        )
        await db.commit()

    try:
        yield {
            "tenant_id": tenant_id,
            "owner_id": owner_id,
            "victim_id": victim_id,
            "owner_session_id": str(owner_session_id),
            "victim_session_id": str(victim_session_id),
        }
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                ChatMessage.__table__.delete().where(
                    ChatMessage.__table__.c.session_id.in_(
                        [owner_session_id, victim_session_id]
                    )
                )
            )
            await db.execute(
                ChatSession.__table__.delete().where(
                    ChatSession.__table__.c.tenant_id == tenant_id
                )
            )
            await db.execute(
                UserModel.__table__.delete().where(
                    UserModel.__table__.c.tenant_id == tenant_id
                )
            )
            await db.execute(
                TenantModel.__table__.delete().where(
                    TenantModel.__table__.c.id == tenant_id
                )
            )
            await db.commit()


async def _message_count(session_id: str) -> int:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import func, select

        result = await db.execute(
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.session_id == uuid.UUID(session_id))
        )
        return result.scalar_one()


async def test_get_owned_session_rejects_foreign(chat_ownership_setup):
    svc = ChatSessionService(AsyncSessionLocal())
    victim_session = chat_ownership_setup["victim_session_id"]
    owned = await svc.get_owned_session(
        uuid.UUID(victim_session),
        chat_ownership_setup["owner_id"],
        chat_ownership_setup["tenant_id"],
    )
    assert owned is None


async def test_get_owned_session_accepts_owner(chat_ownership_setup):
    svc = ChatSessionService(AsyncSessionLocal())
    owned = await svc.get_owned_session(
        uuid.UUID(chat_ownership_setup["owner_session_id"]),
        chat_ownership_setup["owner_id"],
        chat_ownership_setup["tenant_id"],
    )
    assert owned is not None


async def test_save_message_owner_match_saves(chat_ownership_setup):
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        msg = await svc.save_message(
            session_id=uuid.UUID(chat_ownership_setup["owner_session_id"]),
            role="user",
            content={"text": "hello"},
            owner_user_id=chat_ownership_setup["owner_id"],
        )
        assert msg.id is not None


async def test_save_message_foreign_owner_raises(chat_ownership_setup):
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        with pytest.raises(LookupError):
            await svc.save_message(
                session_id=uuid.UUID(chat_ownership_setup["victim_session_id"]),
                role="user",
                content={"text": "poison"},
                owner_user_id=chat_ownership_setup["owner_id"],
            )
    assert await _message_count(chat_ownership_setup["victim_session_id"]) == 0


async def test_save_message_unknown_session_raises(chat_ownership_setup):
    async with AsyncSessionLocal() as db:
        svc = ChatSessionService(db)
        with pytest.raises(LookupError):
            await svc.save_message(
                session_id=uuid.uuid4(),
                role="user",
                content={"text": "x"},
                owner_user_id=chat_ownership_setup["owner_id"],
            )


# ---------------------------------------------------------------------------
# Endpoint-level guard (_validate_owned_session) — 404 on foreign session
# ---------------------------------------------------------------------------


async def test_validate_owned_session_404_on_foreign(chat_ownership_setup):
    from fastapi import HTTPException

    from app.api.v1.endpoints.ai_assistance import _validate_owned_session
    from app.schemas.user import TokenData

    current_user = TokenData(
        user_id=chat_ownership_setup["owner_id"],
        tenant_id=chat_ownership_setup["tenant_id"],
        role="USER",
    )
    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            await _validate_owned_session(
                {"session_id": chat_ownership_setup["victim_session_id"]},
                current_user,
                db,
            )
        assert exc_info.value.status_code == 404


async def test_validate_owned_session_ok_on_owner(chat_ownership_setup):
    from app.api.v1.endpoints.ai_assistance import _validate_owned_session
    from app.schemas.user import TokenData

    current_user = TokenData(
        user_id=chat_ownership_setup["owner_id"],
        tenant_id=chat_ownership_setup["tenant_id"],
        role="USER",
    )
    async with AsyncSessionLocal() as db:
        await _validate_owned_session(
            {"session_id": chat_ownership_setup["owner_session_id"]},
            current_user,
            db,
        )


async def test_validate_owned_session_noop_without_session_id(chat_ownership_setup):
    from app.api.v1.endpoints.ai_assistance import _validate_owned_session
    from app.schemas.user import TokenData

    current_user = TokenData(
        user_id=chat_ownership_setup["owner_id"],
        tenant_id=chat_ownership_setup["tenant_id"],
        role="USER",
    )
    async with AsyncSessionLocal() as db:
        await _validate_owned_session({}, current_user, db)
