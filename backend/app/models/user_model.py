from sqlalchemy import Boolean, Column, String, Enum as SQLEnum, ForeignKey, UUID
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base, UUIDMixin, AuditMixin, TimestampMixin, VersionedMixin
from app.models.enums import Role


class UserModel(Base, UUIDMixin, AuditMixin, VersionedMixin, TimestampMixin):
    """SQLAlchemy model for users"""

    __tablename__ = "users"

    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=True)  # nullable for service accounts (F19)
    role = Column(SQLEnum(Role), nullable=False, default=Role.USER)  # type: ignore[assignment]
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_active = Column(
        Boolean,
        nullable=False,
        server_default="true",
        index=True,
    )
    is_service_account = Column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    settings = Column(JSONB, default=dict)

    def to_dict(self) -> dict:
        """Convert model to dictionary"""
        return {
            "id": self.id,
            "email": self.email,
            "role": self.role.value if self.role else None,  # type: ignore[union-attr]
            "tenant_id": self.tenant_id,
            "is_active": self.is_active,
            "settings": self.settings,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
