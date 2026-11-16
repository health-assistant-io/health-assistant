"""SCIM 2.0 bearer tokens (ADR 0054 Phase 6 — program Phase 12B).

One row is one directory-client credential, tenant-bounded: the bearer
resolves by the sha256 of its value (``token_hash`` — unique, never the
plaintext) and carries **no other principal**. The plaintext is shown once
at creation (the admin API's one-time return) and never stored, logged or
echoed; rotation is "create a new row, revoke the old".

- ``scopes`` — JSONB array (v1: the closed ``["scim"]`` vocabulary; the
  open payload leaves room for narrower operation scopes later without a
  migration).
- ``active = false`` is the revocation (kept as the accountability ledger;
  the hash of a revoked token must never resolve again).
- ``expires_at`` — optional hard expiry; ``null`` = valid until revoked.

Retention: ``record`` — credentials live and die with the tenant (cascade
FK); deleting the tenant kills every token row, so every stale bearer
fails resolution (401) at once.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import AuditMixin, Base, TenantMixin, TimestampMixin, UUIDMixin

#: The closed v1 scope vocabulary of a SCIM token.
SCIM_TOKEN_SCOPES: tuple[str, ...] = ("scim",)


def _now() -> datetime:
    return datetime.now(UTC)


class ScimToken(Base, UUIDMixin, TenantMixin, TimestampMixin, AuditMixin):
    """One tenant-bounded SCIM bearer credential (hash at rest)."""

    __tablename__ = "scim_tokens"

    name = Column(String(200), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    scopes = Column(JSONB, nullable=False, default=list)
    active = Column(Boolean, nullable=False, default=True, server_default="true", index=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    scim_token_metadata = Column("metadata", JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint("token_hash <> ''", name="token_hash_required"),
        Index("ix_scim_tokens_tenant_active", "tenant_id", "active"),
    )

    @property
    def is_live(self) -> bool:
        """True when the bearer may resolve (active, not expired)."""
        if not self.active:
            return False
        if self.expires_at is None:
            return True
        return self.expires_at > _now()

    def to_dict(self) -> dict:
        """Hash-free projection — the token value never renders here."""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "scopes": list(self.scopes or []),
            "active": bool(self.active),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_by": str(self.created_by) if self.created_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
