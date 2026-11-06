from sqlalchemy import UUID, Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base, SystemScopeTenantMixin, UUIDMixin


class AuditEvent(Base, UUIDMixin, SystemScopeTenantMixin):
    """One immutable audit-stream event (identity-auth §17 / plan 16 H2).

    Normative shape: actor (``user_id``), action, resource
    (``resource_type``/``resource_id``), tenant, outcome, timestamp
    (``created_at``), plus the two sanctioned product additions holding the
    full write diff (before/after JSONB).

    Retention: append-only tombstone — rows are never updated or hard-deleted.
    ``tenant_id`` is nullable because system-level actions are recorded with
    no tenant.
    """

    __tablename__ = "audit_events"

    user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(100), nullable=False)
    resource_id = Column(UUID(as_uuid=True), nullable=True)
    outcome = Column(String(20), nullable=False, server_default="ok", index=True)
    old_value = Column(JSONB, nullable=True)  # gate-allow: *, audit diff
    new_value = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
