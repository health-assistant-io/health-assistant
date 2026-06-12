from sqlalchemy import Column, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import Base, UUIDMixin, TenantMixin, TimestampMixin


class ChatSession(Base, UUIDMixin, TenantMixin, TimestampMixin):
    __tablename__ = "chat_sessions"

    user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title = Column(String(255), nullable=True)
    metadata_json = Column(JSONB, nullable=True, default=dict)

    messages = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
        lazy="selectin",
    )


class ChatMessage(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "chat_messages"

    session_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(50), nullable=False)  # 'user', 'assistant', 'system'
    content = Column(JSONB, nullable=False)  # Supports text and future image blocks
    tool_calls = Column(JSONB, nullable=True)
    citations = Column(JSONB, nullable=True)

    session = relationship("ChatSession", back_populates="messages")
