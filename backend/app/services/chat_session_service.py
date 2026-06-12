from typing import List, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, desc, and_, update, func
from app.models.chat_model import ChatSession, ChatMessage


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
    ) -> ChatMessage:
        message = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            citations=citations,
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
