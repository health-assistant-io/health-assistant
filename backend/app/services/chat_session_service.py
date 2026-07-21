from typing import List, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, desc, and_, update, func, case, literal
from sqlalchemy.orm.attributes import flag_modified
from app.models.chat_model import ChatSession, ChatMessage


def _has_tasks_filter():
    """SQL fragment that matches ChatMessage rows whose ``tasks`` JSONB column
    is a NON-EMPTY ARRAY.

    A naive ``tasks IS NOT NULL`` matches too much: it includes JSON ``'null'``
    (which SQLAlchemy emits on some code paths for Python ``None``) and JSON
    ``'[]'``. Both then deserialize to Python ``None`` / empty list, which is
    falsy — silently breaking the resume continuation turn (the agent would
    see the graceful-fallback message instead of the structured HITL feedback).

    ``jsonb_array_length`` RAISES ``cannot get array length of a scalar`` on
    JSON ``'null'``, and Postgres does not short-circuit ``AND``, so we wrap
    it in a ``CASE`` so the function only runs when ``jsonb_typeof = 'array'``.
    """
    return case(
        (
            func.jsonb_typeof(ChatMessage.tasks) == "array",
            func.jsonb_array_length(ChatMessage.tasks) > 0,
        ),
        else_=literal(False),
    )


class ChatSessionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_sessions(
        self, user_id: UUID, tenant_id: UUID, patient_id: Optional[UUID] = None
    ) -> List[ChatSession]:
        query = select(ChatSession).where(
            and_(ChatSession.user_id == user_id, ChatSession.tenant_id == tenant_id)
        )
        if patient_id:
            query = query.where(ChatSession.patient_id == patient_id)

        query = query.order_by(desc(ChatSession.updated_at))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_session_messages(
        self, session_id: UUID, user_id: UUID, tenant_id: UUID
    ) -> List[ChatMessage]:
        # Verify ownership
        session_result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        if not session_result.scalars().first():
            return []

        result = await self.db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        return list(result.scalars().all())

    async def create_session(
        self,
        user_id: UUID,
        tenant_id: UUID,
        patient_id: Optional[UUID] = None,
        title: str = "New Chat",
    ) -> ChatSession:
        session = ChatSession(
            user_id=user_id,
            tenant_id=tenant_id,
            patient_id=patient_id,
            title=title,
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def save_message(
        self,
        session_id: UUID,
        role: str,
        content: dict,
        tool_calls: Optional[list] = None,
        citations: Optional[list] = None,
        tasks: Optional[list] = None,
    ) -> ChatMessage:
        message = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            citations=citations,
            tasks=tasks,
        )
        self.db.add(message)
        # Update session timestamp
        await self.db.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(updated_at=func.now())
        )
        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def get_message(
        self,
        message_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> Optional[ChatMessage]:
        """Load a single message, verifying ownership via its session."""
        result = await self.db.execute(
            select(ChatMessage)
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .where(
                and_(
                    ChatMessage.id == message_id,
                    ChatSession.user_id == user_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        return result.scalars().first()

    async def find_message_by_proposal(
        self,
        session_id: UUID,
        proposal_id: str,
        user_id: UUID,
        tenant_id: UUID,
    ) -> Optional[ChatMessage]:
        """Find the message in a session whose `tasks` JSONB contains a given
        proposal_id. Verifies session ownership first. Used by the HITL resolve
        endpoint (proposal_ids are unique within a session)."""
        session_result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        if not session_result.scalars().first():
            return None

        # See ``_has_tasks_filter`` for why we use a CASE expression instead
        # of the naive ``tasks IS NOT NULL`` (TL;DR: it excludes JSON 'null'
        # AND empty arrays without erroring on non-array scalars).
        result = await self.db.execute(
            select(ChatMessage)
            .where(
                and_(
                    ChatMessage.session_id == session_id,
                    _has_tasks_filter(),
                )
            )
            .order_by(desc(ChatMessage.created_at))
        )
        for msg in result.scalars().all():
            for task in msg.tasks or []:
                if isinstance(task, dict) and task.get("proposal_id") == proposal_id:
                    return msg
        return None

    async def find_resumable_message(
        self,
        session_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        message_id: Optional[UUID] = None,
    ) -> Optional[ChatMessage]:
        """Locate the assistant message whose HITL tasks should drive a resume
        continuation turn. If `message_id` is provided, load it directly;
        otherwise pick the most recent message in the session that has tasks.
        Always verifies session ownership."""
        session_result = await self.db.execute(
            select(ChatSession).where(
                and_(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id,
                    ChatSession.tenant_id == tenant_id,
                )
            )
        )
        if not session_result.scalars().first():
            return None

        # See find_message_by_proposal for why we use the CASE expression.
        has_tasks = _has_tasks_filter()

        if message_id:
            result = await self.db.execute(
                select(ChatMessage).where(
                    and_(
                        ChatMessage.id == message_id,
                        ChatMessage.session_id == session_id,
                        has_tasks,
                    )
                )
            )
            return result.scalars().first()

        result = await self.db.execute(
            select(ChatMessage)
            .where(
                and_(
                    ChatMessage.session_id == session_id,
                    has_tasks,
                )
            )
            .order_by(desc(ChatMessage.created_at))
            .limit(1)
        )
        return result.scalars().first()

    async def update_message_tasks(
        self,
        message: ChatMessage,
        tasks: list,
    ) -> ChatMessage:
        """Persist a mutation to a message's tasks JSONB (in-place change)."""
        message.tasks = tasks
        flag_modified(message, "tasks")
        await self.db.commit()
        await self.db.refresh(message)
        return message

    async def update_message_fields(
        self,
        message: ChatMessage,
        content: Optional[dict] = None,
        tool_calls: Optional[list] = None,
        citations: Optional[list] = None,
        tasks: Optional[list] = None,
    ) -> ChatMessage:
        """Update one or more JSONB fields on an existing message in place.
        Used by the chat stream to proactively persist a HITL task as soon as
        it is emitted, then patch the message with the final content once the
        reasoning loop completes. Only fields explicitly passed are changed."""
        changed = False
        if content is not None:
            message.content = content
            flag_modified(message, "content")
            changed = True
        if tool_calls is not None:
            message.tool_calls = tool_calls
            flag_modified(message, "tool_calls")
            changed = True
        if citations is not None:
            message.citations = citations
            flag_modified(message, "citations")
            changed = True
        if tasks is not None:
            message.tasks = tasks
            flag_modified(message, "tasks")
            changed = True
        if changed:
            await self.db.commit()
            await self.db.refresh(message)
        return message

    async def update_session_title(self, session_id: UUID, user_id: UUID, title: str):
        await self.db.execute(
            update(ChatSession)
            .where(and_(ChatSession.id == session_id, ChatSession.user_id == user_id))
            .values(title=title)
        )
        await self.db.commit()

    async def delete_session(self, session_id: UUID, user_id: UUID):
        await self.db.execute(
            delete(ChatSession).where(
                and_(ChatSession.id == session_id, ChatSession.user_id == user_id)
            )
        )
        await self.db.commit()
