from sqlalchemy import (
    UUID,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditMixin, Base, TimestampMixin, UUIDMixin, VersionedMixin
from app.models.enums import Role


class UserModel(Base, UUIDMixin, AuditMixin, VersionedMixin, TimestampMixin):
    """SQLAlchemy model for users.

    Family-normative ``users`` shape (identity-auth §5) plus the Class S
    extensions (§17): ``role`` (SYSTEM_ADMIN/ADMIN/MANAGER/USER) and
    ``tenant_id`` (hard tenant isolation) — these stay health-only and
    replace the Class D ``is_admin`` boolean, which is exposed to the
    contract as the derived :attr:`is_admin` property.

    Documented deviation from §5: the PK is generated server-side
    (``gen_random_uuid()``) instead of app-side ``uuid4`` — the shipped
    UUIDMixin, kept so every model shares one id strategy.
    """

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("oidc_issuer", "oidc_subject"),)

    email = Column(String(255), unique=True, nullable=False, index=True)
    # identity-auth §5: nullable — NULL refuses password login for the row.
    password_hash = Column(String(255), nullable=True)
    full_name = Column(String(200), nullable=False, default="", server_default="")
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
    # §7 lockout: 5 consecutive failures ⇒ locked_until = now + 15 min ⇒ 423.
    failed_login_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    locked_until = Column(DateTime(timezone=True), nullable=True)
    # §8: source of the ``ver`` claim; bump = global sign-out.
    token_version = Column(Integer, nullable=False, default=1, server_default="1")
    # §14 OIDC link columns — present from day one; unique as a pair.
    oidc_issuer = Column(String(500), nullable=True)
    oidc_subject = Column(String(500), nullable=True)
    settings = Column(JSONB, default=dict)
    # Plan 16 H5 (TOTP MFA). The shared secret is Fernet-encrypted under
    # the DATA_KEY family (enc:: prefix — never plaintext at rest); the
    # recovery codes are stored as a JSON array of bcrypt hashes (one per
    # remaining single-use code — consumption removes the hash); the
    # pending enrollment holds {"secret_enc", "recovery": [hashes]}
    # between /me/mfa/enroll and /me/mfa/confirm; mfa_enforced is the
    # admin-forced flag (next login then requires enrollment).
    mfa_secret_enc = Column(String(512), nullable=True)
    mfa_recovery_codes = Column(Text, nullable=True)
    mfa_pending = Column(JSONB, nullable=True)
    mfa_enforced = Column(Boolean, nullable=False, default=False, server_default="false")

    def __init__(self, **kwargs):
        # §5: email is lowercased on write — the login identifier is
        # case-insensitive everywhere (register, invite bind, admin create).
        if "email" in kwargs and kwargs["email"] is not None:
            kwargs["email"] = str(kwargs["email"]).strip().lower()
        super().__init__(**kwargs)

    @property
    def is_admin(self) -> bool:
        """Contract ``PublicUser.is_admin`` derived from the Class S role.

        ADMIN/SYSTEM_ADMIN carry admin surfaces; MANAGER/USER do not.
        """
        value = self.role.value if isinstance(self.role, Role) else self.role
        return value in (Role.ADMIN.value, Role.SYSTEM_ADMIN.value)

    @property
    def mfa_enabled(self) -> bool:
        """True once a confirmed TOTP secret is stored (plan 16 H5)."""
        return bool(self.mfa_secret_enc)

    def to_dict(self) -> dict:
        """Convert model to dictionary"""
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role.value if self.role else None,  # type: ignore[union-attr]
            "tenant_id": self.tenant_id,
            "is_active": self.is_active,
            "is_admin": self.is_admin,
            "mfa_enabled": self.mfa_enabled,
            "mfa_enforced": bool(self.mfa_enforced),
            "settings": self.settings,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
