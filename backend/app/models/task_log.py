from sqlalchemy import Column, String, JSON, UUID, DateTime, func, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base, UUIDMixin, TenantMixin


class TaskLog(Base, UUIDMixin, TenantMixin):
    __tablename__ = "task_logs"

    task_name = Column(String(100), nullable=False, index=True)
    task_id = Column(String(100), nullable=False, index=True)
    resource_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    level = Column(String(20), nullable=False, index=True)
    stage = Column(String(50), nullable=True)
    message = Column(Text, nullable=False)
    data = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
