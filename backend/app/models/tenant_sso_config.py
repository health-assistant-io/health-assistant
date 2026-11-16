"""Per-tenant OIDC SSO configuration (ADR 0054 D2 — program Phase 12B).

One row per (tenant, issuer): how this tenant's members sign in through an
external OpenID Connect provider. ``client_secret_ref`` names a **secret
slot** — never a value; the slot resolves through
:mod:`app.services.auth.sso_secrets` (OS keyring ``health-assistant/tenant-sso``
service, server env fallback) exactly like the seed-source slots.

Link-only law (D2): an SSO login never merges accounts. The identity link
lives in :class:`~app.models.user_identity.UserIdentity`; this table only
configures the flow:

- ``allowed_domains`` — JSONB array of lowercase email domains. JIT
  provisioning (and tenant resolution by email domain) is capped by it; a
  verified address outside the list never provisions or resolves here.
- ``default_role`` / ``group_role_map`` — the JIT role ceiling. Both are
  validated to the non-SYSTEM_ADMIN role vocabulary at the service boundary
  (SSO is never a path to SYSTEM_ADMIN — same posture as invites).
- ``jit_enabled`` — off turns first-login provisioning into a typed refusal;
  existing links keep signing in.

Retention: ``record`` — configuration erased with the tenant (cascade FK).
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Enum,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditMixin, Base, TenantMixin, TimestampMixin, UUIDMixin
from app.models.enums import Role


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


#: The roles SSO may ever assign (config + JIT + group map). SYSTEM_ADMIN is
#: out of the vocabulary by decision — invites are the only non-bootstrap
#: path and they refuse it too.
SSO_ASSIGNABLE_ROLES: tuple[str, ...] = (Role.USER.value, Role.MANAGER.value, Role.ADMIN.value)

_MAX_DOMAINS = 64
_MAX_GROUP_MAP_ENTRIES = 128


class TenantSSOConfig(Base, UUIDMixin, TenantMixin, TimestampMixin, AuditMixin):
    """One tenant's OIDC provider binding (issuer + client + policy)."""

    __tablename__ = "tenant_sso_configs"

    issuer = Column(String(500), nullable=False)
    client_id = Column(String(255), nullable=False)
    client_secret_ref = Column(String(256), nullable=False)
    allowed_domains = Column(JSONB, nullable=False, default=list)
    default_role = Column(
        Enum(*SSO_ASSIGNABLE_ROLES, native_enum=False, values_callable=_enum_values),
        nullable=False,
    )
    group_role_map = Column(JSONB, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    jit_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    scopes = Column(JSONB, nullable=False, default=list)

    sso_config_metadata = Column("metadata", JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "issuer"),
        CheckConstraint(
            f"default_role IN ({_in(SSO_ASSIGNABLE_ROLES)})",
            name="default_role_vocabulary",
        ),
        CheckConstraint(
            "client_secret_ref <> ''",
            name="client_secret_ref_required",
        ),
        Index("ix_tenant_sso_configs_tenant_enabled", "tenant_id", "enabled"),
    )

    @property
    def is_usable(self) -> bool:
        """True when the flow may run (enabled; secret resolution is separate)."""
        return bool(self.enabled)

    def role_for_groups(self, groups: list[str]) -> str | None:
        """The configured role for the first mapped group (or ``None``).

        Deliberately first-match in the IdP's own group order — the map is
        a tenant decision, not a precedence algorithm of ours.
        """
        group_map = self.group_role_map or {}
        for group in groups or []:
            role = group_map.get(group)
            if role:
                return role
        return None

    def domain_allowed(self, email: str | None) -> bool:
        """Whether ``email``'s domain is in ``allowed_domains`` (case-blind)."""
        if not email or "@" not in email:
            return False
        domain = email.rsplit("@", 1)[1].strip().lower()
        allowed = {str(d).strip().lower() for d in (self.allowed_domains or [])}
        return domain in allowed

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "issuer": self.issuer,
            "client_id": self.client_id,
            "client_secret_ref": self.client_secret_ref,
            "allowed_domains": list(self.allowed_domains or []),
            "default_role": Role(self.default_role).value,
            "group_role_map": dict(self.group_role_map or {}),
            "enabled": bool(self.enabled),
            "jit_enabled": bool(self.jit_enabled),
            "scopes": list(self.scopes or []),
            "created_by": str(self.created_by) if self.created_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
