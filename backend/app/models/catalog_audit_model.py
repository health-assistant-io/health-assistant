"""Append-only audit trail for every catalog CRUD operation (Phase B).

Each create / update / delete / promote on a catalog item appends a row here so
there is a durable record of who changed what, when, and (for promote/demote)
the scope transition. Item identity (``item_name``) is denormalized so the
trail survives deletion of the catalog row itself; ``user_email`` is
denormalized so the trail survives user deletion.

The table is deliberately write-once: nothing in the app mutates or deletes
these rows. Recording is best-effort (a failure never aborts the parent
catalog write — see ``catalog_audit_service.record``).
"""

from typing import Any

from sqlalchemy import Column, String, Text, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

from app.models.base import Base, UUIDMixin, TimestampMixin


class CatalogAuditLog(Base, UUIDMixin, TimestampMixin):
    """One immutable audit entry for one catalog operation."""

    __tablename__ = "catalog_audit_log"

    # Actor context (denormalized so the trail survives user deletion).
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)
    user_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)
    user_email = Column(Text, nullable=False, default="")

    # What was touched.
    catalog_type = Column(String(50), nullable=False, index=True)
    item_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    item_name = Column(Text, nullable=False, default="")

    # The operation: create | update | delete | promote | demote.
    operation = Column(String(20), nullable=False, index=True)

    # Scope transition (only set for promote/demote).
    from_scope = Column(String(20), nullable=True)
    to_scope = Column(String(20), nullable=True)

    # Field-level diff (updates) or extra context (JSONB).
    details = Column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_catalog_audit_type_item", "catalog_type", "item_id"),
        Index("ix_catalog_audit_created_at", "created_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "user_email": self.user_email or "",
            "catalog_type": self.catalog_type,
            "item_id": str(self.item_id),
            "item_name": self.item_name or "",
            "operation": self.operation,
            "from_scope": self.from_scope,
            "to_scope": self.to_scope,
            "details": self.details,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
