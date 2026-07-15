from sqlalchemy import Column, String, ForeignKey, Index
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
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
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
    # Human-in-the-loop task cards proposed by the assistant (see HITL_TASK protocol)
    tasks = Column(JSONB, nullable=True)

    session = relationship("ChatSession", back_populates="messages")

    # Composite for the chat-session load path: messages are always fetched
    # ``WHERE session_id = ? ORDER BY created_at`` (the relationship's
    # ``order_by``). The bare ``session_id`` index above serves point lookups;
    # this composite serves the ordered fan-out (audit B13).
    __table_args__ = (
        Index("ix_chat_messages_session_created_at", "session_id", "created_at"),
    )
