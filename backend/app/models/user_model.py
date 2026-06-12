from sqlalchemy import Column, String, Enum as SQLEnum, ForeignKey, UUID
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base, UUIDMixin, AuditMixin, VersionedMixin
from app.models.enums import Role


class UserModel(Base, UUIDMixin, AuditMixin, VersionedMixin):
    """SQLAlchemy model for users"""

    __tablename__ = "users"

    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(SQLEnum(Role), nullable=False, default=Role.USER)  # type: ignore[assignment]
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    settings = Column(JSONB, default=dict)

    def to_dict(self) -> dict:
        """Convert model to dictionary"""
        return {
            "id": self.id,
            "email": self.email,
            "role": self.role.value if self.role else None,  # type: ignore[union-attr]
            "tenant_id": self.tenant_id,
            "settings": self.settings,
        }
