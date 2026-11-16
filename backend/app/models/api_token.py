"""Scoped API tokens / personal access tokens (ADR 0054 Phase 6, program 12C2B).

One row is one **owner-bound, tenant-bounded, permission-subset** credential:

- **Opaque value, hashed at rest.** ``token_prefix`` is the indexed lookup
  key (the middle segment of the presented value); ``token_hash`` is the
  sha256 of the whole plaintext (unique). The plaintext is shown once at
  creation and never stored, logged or echoed — the SCIM-token precedent,
  32 random bytes make a slow hash unnecessary.
- **Least privilege.** ``permissions`` is a JSONB array of closed
  :class:`~app.core.authz.permissions.Permission` values and MUST be a
  subset of the owner's held permissions at creation, rotation *and* every
  use (a role demotion narrows the token immediately). The effective scope
  per permission is the owner's live role-scope rule, capped at tenant —
  a PAT never reaches the system actor.
- **Patient scope.** ``patient_scope`` is ``all`` (every patient the owner
  can reach at use time), ``granted`` (only the ids in ``patient_ids``
  that the owner can still reach), or ``none`` (no patient-scoped access).
  The owner's own access always bounds the token's reach, so a revoked
  care-team grant narrows the PAT on the next request.
- **Lifecycle.** Expiry, revocation (``revoked_at``), and rotation
  (``rotated_from`` links the replacement; the predecessor is revoked in
  the same transaction). Revoked rows stay as the accountability ledger.

Retention: ``record`` — credentials live and die with their tenant and
owner (cascade FKs). Deleting either kills every row, so every stale
bearer fails resolution (401) at once.
"""

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.models.base import AuditMixin, Base, TenantMixin, TimestampMixin, UUIDMixin


class PatientScope(enum.StrEnum):
    """How far a token's patient-scoped access reaches (closed set)."""

    ALL = "all"
    GRANTED = "granted"
    NONE = "none"


PATIENT_SCOPES: tuple[str, ...] = tuple(member.value for member in PatientScope)

#: Column width for the vocabulary — wider than the longest label so the
#: stored shape stays the one ``0021`` created (no type churn on upgrade).
PATIENT_SCOPE_LENGTH = 16


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _now() -> datetime:
    return datetime.now(UTC)


class ApiToken(Base, UUIDMixin, TenantMixin, TimestampMixin, AuditMixin):
    """One scoped personal access token (sha256 at rest, never a JWT)."""

    __tablename__ = "api_tokens"

    owner_user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(200), nullable=False)
    token_prefix = Column(String(24), nullable=False, unique=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    permissions = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    patient_scope = Column(
        Enum(
            PatientScope,
            native_enum=False,
            values_callable=_enum_values,
            length=PATIENT_SCOPE_LENGTH,
        ),
        nullable=False,
        default=PatientScope.ALL,
        server_default=PatientScope.ALL.value,
    )
    patient_ids = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    expires_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    rotated_from = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("api_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint("name <> ''", name="name_required"),
        CheckConstraint("token_prefix <> ''", name="token_prefix_required"),
        CheckConstraint("token_hash <> ''", name="token_hash_required"),
        CheckConstraint(
            f"patient_scope IN ({_in(PATIENT_SCOPES)})", name="patient_scope_vocabulary"
        ),
        CheckConstraint(
            "patient_scope <> 'granted' OR jsonb_array_length(patient_ids) > 0",
            name="granted_scope_requires_ids",
        ),
        Index("ix_api_tokens_tenant_owner", "tenant_id", "owner_user_id"),
    )

    @property
    def is_live(self) -> bool:
        """True when the bearer may resolve (not revoked, not expired)."""
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires > _now()

    def to_dict(self) -> dict:
        """Hash-free projection — the token value never renders here."""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "owner_user_id": str(self.owner_user_id),
            "name": self.name,
            "token_prefix": self.token_prefix,
            "permissions": list(self.permissions or []),
            "patient_scope": PatientScope(self.patient_scope).value,
            "patient_ids": [str(value) for value in (self.patient_ids or [])],
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "rotated_from": str(self.rotated_from) if self.rotated_from else None,
            "created_by": str(self.created_by) if self.created_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
