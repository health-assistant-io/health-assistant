from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base, UUIDMixin, AuditMixin, VersionedMixin


class TenantModel(Base, UUIDMixin, AuditMixin, VersionedMixin):
    __tablename__ = "tenants"
    
    name = Column(String(255), nullable=False)
    settings = Column(JSONB, default=dict)
    # is_active column removed - not in database schema
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "settings": self.settings,
            # "is_active": self.is_active,  # Removed - not in database
        }
