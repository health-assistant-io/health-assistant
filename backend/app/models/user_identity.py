"""The link-only OIDC identity table (ADR 0054 D2 — program Phase 12B).

One row is one external identity: ``(issuer, subject)`` — the globally
unique pair the IdP mints — bound to exactly one local user in exactly one
tenant. This is the "one identity = one tenant" option D2 named: the
``users.oidc_issuer``/``oidc_subject`` columns stay the family §5 shape on
the account row, while per-tenant SSO links resolve here.

Laws enforced as constraints:

* ``uq_user_identities_issuer_subject`` — an IdP subject can never link to
  two accounts (in any tenant). Link-only: no merge path exists.
* ``uq_user_identities_tenant_user_issuer`` — one link per (user, issuer)
  inside the tenant.
* ``ck_user_identities_email_verified`` — a link exists only for a
  ``email_verified = true`` address; the writers enforce it, the row law
  records it (a verified-required claim the store itself guarantees).

Tenant scoping is hard: ``tenant_id`` is NOT NULL and must equal the
linked user's tenant — the service asserts the pairing before insert.

Retention: ``record`` — links die with the tenant and the user (cascade
FKs); a deleted account's external identity frees the subject for a fresh,
explicit link decision, never an automatic re-binding.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin


class UserIdentity(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """One verified external identity linked to one local account."""

    __tablename__ = "user_identities"

    user_id = Column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issuer = Column(String(500), nullable=False)
    subject = Column(String(500), nullable=False)
    email = Column(String(255), nullable=False)
    email_verified = Column(Boolean, nullable=False, default=True, server_default="true")
    linked_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (
        UniqueConstraint("issuer", "subject"),
        UniqueConstraint("tenant_id", "user_id", "issuer"),
        CheckConstraint(
            "email_verified IS TRUE",
            name="email_verified_required",
        ),
        CheckConstraint("subject <> ''", name="subject_required"),
        Index("ix_user_identities_tenant_issuer_subject", "tenant_id", "issuer", "subject"),
    )

    @property
    def linked_email(self) -> str:
        """The verified address captured at link time (lowercased on write)."""
        return self.email or ""

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "user_id": str(self.user_id),
            "issuer": self.issuer,
            "subject": self.subject,
            "email": self.email,
            "email_verified": bool(self.email_verified),
            "linked_at": self.linked_at.isoformat() if self.linked_at else None,
        }
