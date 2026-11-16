"""WebAuthn/passkey credentials (ADR 0054 Phase 6 — program 12C2).

One row is one registered authenticator for one account. The browser
ceremony (registration/assertion) is WebAuthn Level 2; the server stores
only what a later assertion needs to verify:

- ``credential_id`` — the authenticator's credential id (raw bytes, globally
  unique: the same authenticator can only be registered once).
- ``public_key`` — the CBOR-encoded COSE public key from the attestation.
- ``sign_count`` — the monotonic signature counter (clone detection: a
  non-zero counter that fails to advance is a cloned authenticator).
- ``transports`` — the optional transport hints the client reported
  (``usb``/``nfc``/``ble``/``internal``/``hybrid``), used to build
  ``allowCredentials``.
- ``aaguid`` — the authenticator model identifier from the attestation.
- ``backup_eligible`` / ``backup_state`` — WebAuthn backup flags (synced
  passkeys report both true; clone detection is skipped for them).
- ``name`` — the user's label for the device (settings card).
- ``disabled`` — soft-disable on clone detection; a disabled credential is
  never offered and never verifies.

Retention: ``record`` — the row is account security state, erased with the
user/tenant via the cascade FKs. No PHI is stored here.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.models.base import Base, TenantMixin, UUIDMixin


class WebAuthnCredential(Base, UUIDMixin, TenantMixin):
    """One registered WebAuthn credential (a passkey/security key)."""

    __tablename__ = "webauthn_credentials"
    __table_args__ = (
        UniqueConstraint("credential_id", name="uq_webauthn_credentials_credential_id"),
        Index("ix_webauthn_credentials_tenant_user", "tenant_id", "user_id"),
    )

    user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credential_id = Column(LargeBinary, nullable=False)
    public_key = Column(LargeBinary, nullable=False)
    sign_count = Column(Integer, nullable=False, default=0, server_default="0")
    transports = Column(JSONB, nullable=False, default=list, server_default="[]")
    aaguid = Column(String(36), nullable=True)
    backup_eligible = Column(Boolean, nullable=False, default=False, server_default="false")
    backup_state = Column(Boolean, nullable=False, default=False, server_default="false")
    name = Column(String(200), nullable=False, default="", server_default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    disabled = Column(Boolean, nullable=False, default=False, server_default="false")

    def to_dict(self) -> dict:
        """The self-service credential shape (no key material)."""
        return {
            "id": str(self.id),
            "name": self.name,
            "transports": list(self.transports or []),
            "aaguid": self.aaguid,
            "backup_eligible": bool(self.backup_eligible),
            "backup_state": bool(self.backup_state),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "disabled": bool(self.disabled),
        }
