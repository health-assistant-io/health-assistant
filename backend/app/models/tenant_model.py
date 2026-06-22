from sqlalchemy import Boolean, Column, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import (
    AuditMixin,
    Base,
    TimestampMixin,
    UUIDMixin,
    VersionedMixin,
)


class TenantModel(Base, UUIDMixin, AuditMixin, VersionedMixin, TimestampMixin):
    """A tenant is the absolute data-isolation boundary.

    Each tenant owns its users, patients, clinical data and catalog
    overrides. Tenant rows are managed by ``SYSTEM_ADMIN`` via the
    ``/admin/tenants`` surface; tenant admins only see their own tenant
    through ``GET /tenants/{id}``.
    """

    __tablename__ = "tenants"

    name = Column(String(255), nullable=False)
    slug = Column(String(80), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(
        Boolean,
        nullable=False,
        server_default="true",
        index=True,
    )
    owner_id = Column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    settings = Column(JSONB, default=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "is_active": self.is_active,
            "owner_id": self.owner_id,
            "settings": self.settings,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
