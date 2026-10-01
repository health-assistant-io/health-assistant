from sqlalchemy import UUID, Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base, TenantMixin, UUIDMixin


class AuditEvent(Base, UUIDMixin, TenantMixin):
    """One immutable audit-stream event (identity-auth §17 / plan 16 H2).

    Normative shape: actor (``user_id``), action, resource
    (``resource_type``/``resource_id``), tenant, outcome, timestamp
    (``created_at``). ``old_value``/``new_value`` are the sanctioned
    product additions (full write diffs). The table was renamed from
    ``audit_logs`` (and the class from ``AuditLog``) in migration
    ``a1u2d3i4t5e6`` to match the family contract name.
    """

    __tablename__ = "audit_events"

    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(100), nullable=False)
    resource_id = Column(UUID(as_uuid=True), nullable=True)
    outcome = Column(String(20), nullable=False, server_default="ok", index=True)
    old_value = Column(JSONB, nullable=True)
    new_value = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
