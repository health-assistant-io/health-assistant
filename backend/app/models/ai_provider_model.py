import enum
from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Integer,
    Float,
    UUID,
    Index,
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql.expression import text
from typing import Optional
from uuid import uuid4

from .base import Base, UUIDMixin, TenantMixin, TimestampMixin, UserMixin


from app.models.enums import AIScope


class AIProviderModel(Base, UUIDMixin, TenantMixin, UserMixin, TimestampMixin):
    """Stores configuration for AI/LLM providers"""

    __tablename__ = "ai_providers"

    name = Column(String(100), nullable=False, index=True)
    scope = Column(SQLEnum(AIScope), nullable=False, default=AIScope.SYSTEM, index=True)
    provider_type = Column(String(50), nullable=False)  # "openai", "tesseract"
    api_base = Column(String(500), nullable=False)
    api_key = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    settings = Column(JSONB, nullable=True, default=dict)

    # Provider Transparency Info
    is_local = Column(Boolean, default=False, server_default=text('false'), nullable=False)
    company_name = Column(String(200), nullable=True)
    company_website = Column(String(500), nullable=True)
    company_country = Column(String(100), nullable=True)

    # Relationship to models
    models = relationship(
        "AIModel", back_populates="provider", cascade="all, delete-orphan"
    )

    # Relationship to task assignments
    task_assignments = relationship(
        "AITaskAssignment", back_populates="provider", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_ai_providers_tenant_active", "tenant_id", "is_active"),
        Index("idx_ai_providers_user", "user_id"),
        Index("idx_ai_providers_scope", "scope"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "scope": self.scope,
            "provider_type": self.provider_type,
            "api_base": self.api_base,
            "api_key": self.api_key,
            "is_active": self.is_active,
            "settings": self.settings,
            "is_local": self.is_local,
            "company_name": self.company_name,
            "company_website": self.company_website,
            "company_country": self.company_country,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "created_at": str(self.created_at) if self.created_at else None,
            "updated_at": str(self.updated_at) if self.updated_at else None,
        }


class AIModel(Base, UUIDMixin, TimestampMixin):
    """Stores model configurations for each provider"""

    __tablename__ = "ai_models"

    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(200), nullable=False, index=True)
    model_name = Column(String(200), nullable=False)  # Actual API model name
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    max_tokens = Column(Integer, default=65536)
    temperature = Column(Float, default=0.7)
    is_local = Column(Boolean, nullable=True) # Override provider's is_local
    settings = Column(JSONB, nullable=True, default=dict)

    # Relationship to provider
    provider = relationship("AIProviderModel", back_populates="models")

    __table_args = (Index("idx_ai_models_provider_active", "provider_id", "is_active"),)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "provider_id": str(self.provider_id),
            "name": self.name,
            "model_name": self.model_name,
            "description": self.description,
            "is_active": self.is_active,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "is_local": self.is_local,
            "settings": self.settings,
            "created_at": str(self.created_at) if self.created_at else None,
            "updated_at": str(self.updated_at) if self.updated_at else None,
        }


class AITaskAssignment(Base, UUIDMixin, TenantMixin, UserMixin, TimestampMixin):
    """Assigns specific provider/model combinations to task types"""

    __tablename__ = "ai_task_assignments"

    task_type = Column(
        String(50), nullable=False, index=True
    )  # e.g. "ocr", "nlp", "chat", "magic_fill_examination", etc.
    scope = Column(SQLEnum(AIScope), nullable=False, default=AIScope.SYSTEM, index=True)
    provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active = Column(Boolean, default=True, index=True)
    priority = Column(Integer, default=0)

    # Relationships
    provider = relationship("AIProviderModel", back_populates="task_assignments")

    __table_args__ = (
        Index(
            "idx_ai_task_assignments_tenant_task", "tenant_id", "task_type", "is_active"
        ),
        Index("idx_ai_task_assignments_priority", "priority"),
        Index("idx_ai_task_assignments_user", "user_id"),
        Index("idx_ai_task_assignments_scope", "scope"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "task_type": self.task_type,
            "scope": self.scope,
            "provider_id": str(self.provider_id) if self.provider_id else None,
            "model_id": str(self.model_id) if self.model_id else None,
            "is_active": self.is_active,
            "priority": self.priority,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "created_at": str(self.created_at) if self.created_at else None,
            "updated_at": str(self.updated_at) if self.updated_at else None,
        }
