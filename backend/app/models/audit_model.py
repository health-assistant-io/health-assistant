from sqlalchemy import Column, String, UUID, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base, UUIDMixin, TenantMixin


class AuditLog(Base, UUIDMixin, TenantMixin):
    __tablename__ = "audit_logs"

    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(100), nullable=False)
    resource_id = Column(UUID(as_uuid=True), nullable=True)
    old_value = Column(JSONB, nullable=True)
    new_value = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
