"""SCIM 2.0 directory groups (ADR 0054 Phase 6 — program Phase 12B).

RFC 7644 ``/Groups`` state pushed by a directory client. A group carries a
``display_name`` (unique per tenant) and members (the join rows); it holds
**no role of its own** — the tenant SSO config's ``group_role_map`` maps a
display name to a role at login/apply time, so a map edit re-roles members
without touching directory state.

``external_id`` is the directory's own identifier (the SCIM ``externalId``
attribute) for idempotent upserts from the IdP side.

Retention: ``record`` — directory state erased with the tenant and its
members (cascade FKs).
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.models.base import AuditMixin, Base, TenantMixin, TimestampMixin, UUIDMixin


class ScimGroup(Base, UUIDMixin, TenantMixin, TimestampMixin, AuditMixin):
    """One tenant-local SCIM group (display name unique inside the tenant)."""

    __tablename__ = "scim_groups"

    display_name = Column(String(255), nullable=False)
    external_id = Column(String(255), nullable=True, index=True)
    active = Column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        UniqueConstraint("tenant_id", "display_name"),
        Index("ix_scim_groups_tenant_external_id", "tenant_id", "external_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "display_name": self.display_name,
            "external_id": self.external_id,
            "active": bool(self.active),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ScimGroupMember(Base, UUIDMixin):
    """One (group, user) membership row — unique per pair, cascade both ways."""

    __tablename__ = "scim_group_members"

    group_id = Column(
        ForeignKey("scim_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    added_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    added_by = Column(PG_UUID(as_uuid=True), nullable=True)

    __table_args__ = (UniqueConstraint("group_id", "user_id"),)
