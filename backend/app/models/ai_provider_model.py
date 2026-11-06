from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql.expression import text

from app.models.enums import AIModelCapability, AIScope

from .base import Base, SystemScopeTenantMixin, TimestampMixin, UserMixin, UUIDMixin


class AIProviderModel(Base, UUIDMixin, SystemScopeTenantMixin, UserMixin, TimestampMixin):
    """Stores configuration for AI/LLM providers"""

    __tablename__ = "ai_providers"

    name = Column(String(100), nullable=False, index=True)
    scope = Column(SQLEnum(AIScope), nullable=False, default=AIScope.SYSTEM, index=True)
    provider_type = Column(String(50), nullable=False)  # "openai", "tesseract"
    api_base = Column(String(500), nullable=False)
    api_key = Column(String(500), nullable=True)
    # BYOK setup stamp (§15): the preset this row was created/re-setup from.
    # NULL = a manual row (adoptable by a later setup with the same type+base).
    preset_key = Column(String(40), nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    settings = Column(JSONB, nullable=True, default=dict)

    # Provider Transparency Info
    is_local = Column(Boolean, default=False, server_default=text("false"), nullable=False)
    company_name = Column(String(200), nullable=True)
    company_website = Column(String(500), nullable=True)
    company_country = Column(String(100), nullable=True)

    # Relationship to models
    models = relationship("AIModel", back_populates="provider", cascade="all, delete-orphan")

    # Relationship to task assignments
    task_assignments = relationship(
        "AITaskAssignment",
        back_populates="provider",
        cascade="all, delete-orphan",
        foreign_keys="AITaskAssignment.provider_id",
    )

    __table_args__ = (
        Index("ix_ai_providers_tenant_active", "tenant_id", "is_active"),
        Index("ix_ai_providers_user", "user_id"),
        Index("ix_ai_providers_scope", "scope"),
    )

    def to_dict(self) -> dict:
        """Return a dict with the api_key in its at-rest encrypted form.

        Callers that need the plaintext key must use
        :meth:`get_api_key_plaintext` (defined on the service). The
        ``api_key`` field here keeps the at-rest encrypted form so that
        accidental serialization paths (logs, dumps) never leak the key.
        """
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
            "preset_key": self.preset_key,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "created_at": str(self.created_at) if self.created_at else None,
            "updated_at": str(self.updated_at) if self.updated_at else None,
        }

    def get_api_key_plaintext(self) -> str | None:
        """Return the decrypted api_key, or None if not set.

        This is the only sanctioned way to read the plaintext key for use
        with the LLM client. Handles both the encrypted envelope and plaintext
        values (returns the value verbatim when it does not carry the
        encrypted prefix).
        """
        from app.core.encryption import decrypt_secret

        try:
            return decrypt_secret(self.api_key)
        except ValueError:
            # Key rotated or corrupted — surface None so the caller can
            # prompt the user to re-enter the key. Do not raise from a
            # model method to avoid breaking list endpoints.
            return None


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
    # Feature flags — which modalities this model supports. A SET of
    # AIModelCapability values (text / vision / tools / stt / tts / embeddings).
    # Tasks require specific capabilities (chat→text, ocr→vision,
    # transcription→stt) so the task-assignment picker only offers eligible
    # models. JSONB array; defaults to ["text"] (the baseline modality).
    capabilities = Column(
        JSONB,
        nullable=False,
        default=lambda: [AIModelCapability.TEXT.value],
        server_default=text("'[\"text\"]'"),
    )
    # Decision transport seam (plan 26 / ADR-0032, D2): 'systemone' today.
    # NULL = not a decision model. Callers never branch on the value — the
    # decide funnel's registry resolves transport → engine.
    decision_transport = Column(String(20), nullable=True)
    # Chat transport seam (ADR-0039): 'responses' | 'completions'.
    # NULL/anything else = auto = chat completions. Honored only when the
    # base URL rides OpenAI's own API; the responses path is always
    # constructed store-less.
    chat_transport = Column(String(20), nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    max_tokens = Column(Integer, default=65536)
    temperature = Column(Float, nullable=True)
    is_local = Column(Boolean, nullable=True)  # Override provider's is_local
    settings = Column(JSONB, nullable=True, default=dict)

    # Relationship to provider
    provider = relationship("AIProviderModel", back_populates="models")

    __table_args__ = (
        Index("ix_ai_models_provider_active", "provider_id", "is_active"),
        CheckConstraint("max_tokens > 0", name="positive_max_tokens"),
        CheckConstraint("temperature BETWEEN 0 AND 2", name="temperature_bounds"),
    )

    def get_capabilities(self) -> list:
        """Return the capability list.

        ``text`` is the DEFAULT (every pre-existing model + new models start
        with it) but is NOT forced — an STT-only model like ``whisper-1``
        legitimately carries only ``stt``. An empty/null column falls back to
        ``["text"]`` for backward compatibility with rows predating the
        capabilities feature.
        """
        caps = self.capabilities if isinstance(self.capabilities, list) else []
        vals = [c for c in caps if isinstance(c, str)]
        if not vals:
            vals = [AIModelCapability.TEXT.value]
        return vals

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "provider_id": str(self.provider_id),
            "name": self.name,
            "model_name": self.model_name,
            "description": self.description,
            "capabilities": self.get_capabilities(),
            "decision_transport": self.decision_transport,
            "is_active": self.is_active,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "is_local": self.is_local,
            "settings": self.settings,
            "created_at": str(self.created_at) if self.created_at else None,
            "updated_at": str(self.updated_at) if self.updated_at else None,
        }


class AITaskAssignment(Base, UUIDMixin, SystemScopeTenantMixin, UserMixin, TimestampMixin):
    """Binds a provider/model to a task slot at one configuration scope.

    Assignment law (plan 4 W2, ADR-0037 single-slot):

    * the task is a seeded lookup row (``task_type_id`` → ``ai_task_defs``), not
      a free-form string — a new task is a registry row, never a migration;
    * rows are **revoked, never deleted** (``revoked_at``); "active" means
      exactly ``revoked_at IS NULL``, and the unique index below makes a second
      active row for the same ``(tenant_id, user_id, task_type_id)`` fail at the
      database;
    * ``NULLS NOT DISTINCT`` is required because system-scope rows carry
      ``tenant_id IS NULL AND user_id IS NULL`` and tenant-scope rows carry
      ``user_id IS NULL`` — Postgres would otherwise treat every such row as
      distinct and let duplicates through (PG15+; COALESCE expression unique
      otherwise);
    * ``priority`` is gone: precedence is fixed (specialist primary →
      specialist fallback → capability default → unavailable) and lives once, in
      ``app.ai.providers.resolution``;
    * a fallback is a pair of columns on the specialist row, never a second row,
      and only specialist rows may carry one (a capability default IS the
      fallback — ADR-0037).
    """

    __tablename__ = "ai_task_assignments"

    task_type_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_task_defs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
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
    fallback_provider_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    fallback_model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_at = Column(DateTime(timezone=True), nullable=True, index=True)
    # PHI scope guard (plan 26 D17, health-specific): decision_* assignments
    # are PHI-excluded by default — a PHI-adjacent dispatch never runs on the
    # slot unless this flag was explicitly set (ADR-0020 §6 sensitive-scope
    # exclusion; default False, gap-fill never sets it, only the explicit
    # task-assignment surface can).
    phi_allowed = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    # Relationships
    provider = relationship(
        "AIProviderModel",
        back_populates="task_assignments",
        foreign_keys=[provider_id],
    )
    task_def = relationship("AITaskDef", lazy="joined")

    __table_args__ = (
        Index(
            "uq_ai_task_assignments_scope_task",
            "tenant_id",
            "user_id",
            "task_type_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_ai_task_assignments_tenant_task", "tenant_id", "task_type_id"),
        Index("ix_ai_task_assignments_user", "user_id"),
        Index("ix_ai_task_assignments_scope", "scope"),
    )

    @property
    def task_type(self) -> str | None:
        """The registry task id (``ai_task_defs.code``) for this row."""
        task_def = self.task_def
        return task_def.code if task_def is not None else None

    @property
    def is_active(self) -> bool:
        """Derived: "active" is exactly ``revoked_at IS NULL`` (never stored)."""
        return self.revoked_at is None

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "task_type": self.task_type,
            "scope": self.scope,
            "provider_id": str(self.provider_id) if self.provider_id else None,
            "model_id": str(self.model_id) if self.model_id else None,
            "fallback_provider_id": (
                str(self.fallback_provider_id) if self.fallback_provider_id else None
            ),
            "fallback_model_id": str(self.fallback_model_id) if self.fallback_model_id else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "phi_allowed": self.phi_allowed,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "created_at": str(self.created_at) if self.created_at else None,
            "updated_at": str(self.updated_at) if self.updated_at else None,
        }
