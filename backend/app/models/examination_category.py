from sqlalchemy import Column, String, Text, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import Base, UUIDMixin, TimestampMixin


class ExaminationCategory(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "examination_categories"

    name = Column(String(100), nullable=False, unique=True)
    slug = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    color = Column(String(20), nullable=True)  # e.g., "#3b82f6" or "blue-500"
    icon = Column(JSONB, nullable=True)  # { "type": "lucide", "value": "Activity" }

    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,  # Nullable for global categories
        index=True,
    )

    # Relationships
    examinations = relationship("ExaminationModel", back_populates="category_entity")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id) if self.id else None,
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "color": self.color,
            "icon": self.icon,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
        }
