from sqlalchemy import Column, String, Boolean, Text
from sqlalchemy.orm import relationship
from app.models.base import Base, UUIDMixin, TenantMixin, TimestampMixin


class BodyPartModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    __tablename__ = "body_parts"

    name = Column(String(100), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    snomed_code = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    is_custom = Column(Boolean, default=False, nullable=False)

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "slug": self.slug,
            "snomed_code": self.snomed_code,
            "description": self.description,
            "is_custom": self.is_custom,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
        }
