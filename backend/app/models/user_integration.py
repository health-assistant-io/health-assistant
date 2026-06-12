from sqlalchemy import Column, String, ForeignKey, DateTime, Text, Enum, UniqueConstraint, Integer
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin
from app.models.enums import IntegrationStatus
from sqlalchemy.sql import func

class UserIntegration(Base, UUIDMixin, TenantMixin, AuditMixin, TimestampMixin):
    __tablename__ = "user_integrations"

    user_id = Column(
        PG_UUID(as_uuid=True), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=False, 
        index=True
    )
    patient_id = Column(
        PG_UUID(as_uuid=True), 
        ForeignKey("fhir_patients.id", ondelete="CASCADE"), 
        nullable=False
    )
    
    provider = Column(String(50), nullable=False) # e.g., 'google_fit'
    status = Column(Enum(IntegrationStatus), default=IntegrationStatus.PENDING)
    
    # OAuth Credentials
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    scopes = Column(String(1000), nullable=True)
    
    provider_account_id = Column(String(255), nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    # Added user_config for integration specific configurations
    user_config = Column(JSONB, nullable=True)
    
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uix_user_provider"),
    )

    # Relationships
    user = relationship("UserModel")
    patient = relationship("Patient")
    sync_logs = relationship("IntegrationSyncLog", back_populates="integration", cascade="all, delete-orphan")

class IntegrationSyncLog(Base, UUIDMixin, TenantMixin):
    __tablename__ = "integration_sync_logs"

    integration_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    status = Column(String(50), nullable=False) # "success", "failed"
    records_synced = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    integration = relationship("UserIntegration", back_populates="sync_logs")

