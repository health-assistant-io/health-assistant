from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import AuditMixin, Base, TenantMixin, TimestampMixin, UUIDMixin
from app.models.enums import (
    IntegrationCredentialKind,
    IntegrationCursorKind,
    IntegrationStatus,
    IntegrationSyncErrorClass,
    IntegrationSyncStream,
)


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


class UserIntegration(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    """One connected instance of an extension (config only — no state).

    ``user_config`` holds **configuration** (the extension-owned settings map
    the config flow validated). It carries no sync cursors, no credential
    material and no sync health: cursors/health live in
    ``integration_sync_state`` rows and secrets in ``integration_credentials``
    (plan 8 W1 — one truth per concern).

    Retention: ``record`` — configuration is erased with the owning user via
    the ownership cascade.
    """

    __tablename__ = "user_integrations"

    user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
    )

    provider = Column(String(50), nullable=False)  # e.g., 'google_fit'
    status = Column(Enum(IntegrationStatus), default=IntegrationStatus.PENDING)

    # OAuth Credentials
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    scopes = Column(String(1000), nullable=True)

    provider_account_id = Column(String(255), nullable=True)
    instance_name = Column(String(255), nullable=True)  # E.g., "My Phone Webhook"
    is_debug_enabled = Column(Boolean, default=False, nullable=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    # Added user_config for integration specific configurations
    user_config = Column(JSONB, nullable=True)

    # Relationships
    user = relationship("UserModel")
    patient = relationship("Patient")
    sync_logs = relationship(
        "IntegrationSyncLog", back_populates="integration", cascade="all, delete-orphan"
    )
    sync_states = relationship(
        "IntegrationSyncState", back_populates="integration", cascade="all, delete-orphan"
    )
    credentials = relationship(
        "IntegrationCredential", back_populates="integration", cascade="all, delete-orphan"
    )


class IntegrationCredential(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    """One sealed credential slot per ``(integration, kind)``.

    ``sealed_blob`` is the at-rest cipher wrapper
    (``{"_encrypted": token, "_kid": fingerprint}``) produced by
    ``nx_auth.atrest`` with the integration id as cipher context, so a copied
    blob cannot be replayed into another instance. ``kid`` mirrors the
    wrapper's key fingerprint as a typed column (rotation census: find every
    row still sealed under a retired key). ``scopes`` is the provider-declared
    scope grant; the expiry columns drive refresh scheduling.

    Rotation replaces the sealed blob in place and stamps ``rotated_at``;
    revocation stamps ``revoked_at`` and every read then raises the typed
    ``credential`` error — the sync fails closed instead of running
    unauthenticated. Nothing ever reads a credential without the owning
    integration's context (`app.services.integration.integration_credentials`).

    Retention: ``record`` — erased with the owning integration/subject.
    """

    __tablename__ = "integration_credentials"

    user_integration_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(
        SQLEnum(
            IntegrationCredentialKind,
            name="integration_credential_kind",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
    )
    sealed_blob = Column(Text, nullable=False)
    kid = Column(String(64), nullable=False)
    scopes = Column(ARRAY(String(255)), nullable=False, server_default=text("'{}'::varchar[]"))
    access_expires_at = Column(DateTime(timezone=True), nullable=True)
    refresh_expires_at = Column(DateTime(timezone=True), nullable=True)
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    integration = relationship("UserIntegration", back_populates="credentials")

    __table_args__ = (
        UniqueConstraint(
            "user_integration_id",
            "kind",
            name="uq_integration_credentials_integration_kind",
        ),
        Index("ix_integration_credentials_tenant_kid", "tenant_id", "kid"),
    )


class IntegrationSyncState(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Per-stream cursor + health row for one integration instance.

    Replaces the ``user_config["_sync_state"]`` JSONB hiding: a cursor is a
    typed row keyed ``(integration, stream, cursor_key)``, so the sync engine
    reads *data* — never code-embedded state — to decide what to pull and
    when to retry. ``stream`` is the closed data-class vocabulary;
    ``cursor_key`` names a provider-scoped sub-cursor inside a stream (a FHIR
    server pulls several resource types under ``observations``); the empty
    string is the stream's primary cursor.

    Health columns drive the beat: ``next_retry_at`` is the backoff the
    scheduler honors (provider ``Retry-After`` or exponential backoff,
    whichever is later), ``consecutive_failures`` counts until a success
    resets it, and ``last_error_class`` records the typed failure class.

    Retention: ``operational`` — runtime state bounded by the integration
    lifecycle and plan 6's retention job.
    """

    __tablename__ = "integration_sync_state"

    user_integration_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stream = Column(
        SQLEnum(
            IntegrationSyncStream,
            name="integration_sync_stream",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
    )
    cursor_key = Column(String(96), nullable=False, server_default=text("''"))
    cursor_kind = Column(
        SQLEnum(
            IntegrationCursorKind,
            name="integration_cursor_kind",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
        server_default=text("'timestamp'"),
    )
    cursor_value = Column(Text, nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_error_at = Column(DateTime(timezone=True), nullable=True)
    last_error_class = Column(
        SQLEnum(
            IntegrationSyncErrorClass,
            name="integration_sync_error_class",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=24,
        ),
        nullable=True,
    )
    consecutive_failures = Column(Integer, nullable=False, server_default=text("0"))
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)

    integration = relationship("UserIntegration", back_populates="sync_states")

    __table_args__ = (
        UniqueConstraint(
            "user_integration_id",
            "stream",
            "cursor_key",
            name="uq_integration_sync_state_stream_cursor",
        ),
        CheckConstraint("consecutive_failures >= 0", name="consecutive_failures_non_negative"),
        Index(
            "ix_integration_sync_state_tenant_retry",
            "tenant_id",
            "next_retry_at",
        ),
    )


class IntegrationSyncLog(Base, UUIDMixin, TenantMixin):
    """One sync run's outcome (append-only history).

    ``error_class`` is the typed failure class (charter error code or the
    engine's ``mapping_gap``/``credential`` classes); ``error_message`` keeps
    the capped human-readable detail. ``status`` is ``success``, ``partial``
    or ``failed``.

    Retention: ``operational`` — bounded by plan 6's retention job.
    """

    __tablename__ = "integration_sync_logs"

    integration_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(50), nullable=False)  # "success", "partial", "failed"
    records_synced = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    error_class = Column(
        SQLEnum(
            IntegrationSyncErrorClass,
            name="integration_sync_error_class",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=24,
        ),
        nullable=True,
    )
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    integration = relationship("UserIntegration", back_populates="sync_logs")


class IntegrationDebugLog(Base, UUIDMixin, TenantMixin):
    """Debug-mode payload capture for one integration instance.

    Retention: ``operational`` — bounded by plan 6's retention job.
    """

    __tablename__ = "integration_debug_logs"

    integration_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    level = Column(String(20), default="info")  # e.g. 'info', 'error'
    title = Column(String(255), nullable=False)
    payload = Column(JSONB, nullable=True)

    integration = relationship("UserIntegration")
