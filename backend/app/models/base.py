from sqlalchemy import Column, DateTime, func, text, Integer, Boolean, UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        index=True,
    )


class TenantMixin:
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)


class UserMixin:
    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)


class AuditMixin:
    created_by = Column(UUID(as_uuid=True), nullable=True)
    updated_by = Column(UUID(as_uuid=True), nullable=True)


class VersionedMixin:
    version = Column(Integer, default=1)
    is_current = Column(Boolean, default=True)


class UUIDMixin:
    id = Column(UUID(as_uuid=True), primary_key=True, default=text("gen_random_uuid()"))
