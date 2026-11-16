"""Scoped integration & extension principals (ADR 0054 D8, Program Phase 13).

One row is one **service identity** — never an owner-role impersonation:

- **Bound to a real person, authorized as itself.** ``owner_user_id`` is the
  accountable human (audit attribution and the permission ceiling), while the
  principal's ``scopes`` are the *only* permissions it exercises. At use the
  stored set is re-intersected with the owner's live role and, for an
  integration-bound principal, with the extension manifest's derived
  capability scopes (host-enforced, never display).
- **Kind + binding.** ``kind=integration`` is bound to one
  ``user_integrations`` row (connector sync, webhook/API ingest);
  ``kind=extension`` is a tenant-level extension identity; ``kind=service``
  is a machine identity not tied to a connector. At most one *active*
  principal per integration.
- **Patient scope** mirrors the PAT vocabulary: ``all`` (the owner's live
  reach), ``granted`` (the explicit allowlist intersected with that reach),
  ``none`` (no patient-scoped access). Revoking a care-team grant narrows an
  outstanding principal on its next call.
- **Credential.** ``ha_int_<lookup-prefix>_<secret>``: an opaque value shown
  once, stored as an indexed prefix plus a sha256. Auto-provisioned internal
  principals may carry no credential at all (they are only resolvable through
  the integration row); rotation mints one.
- **Lifecycle.** ``active|revoked|rotated`` plus ``revoked_at`` and
  ``rotated_from``; revoked/rotated rows stay as the accountability ledger.

Retention: ``record`` — a principal lives and dies with its tenant, owner and
integration (cascade/set-null FKs, like every other credential row).
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

from app.models.api_token import PATIENT_SCOPE_LENGTH, PatientScope
from app.models.base import AuditMixin, Base, TenantMixin, TimestampMixin, UUIDMixin


class IntegrationPrincipalKind(enum.StrEnum):
    """What a principal is bound to (closed set)."""

    INTEGRATION = "integration"
    EXTENSION = "extension"
    SERVICE = "service"


class IntegrationPrincipalStatus(enum.StrEnum):
    """Principal lifecycle (closed set)."""

    ACTIVE = "active"
    REVOKED = "revoked"
    ROTATED = "rotated"


PRINCIPAL_KINDS: tuple[str, ...] = tuple(member.value for member in IntegrationPrincipalKind)
PRINCIPAL_STATUSES: tuple[str, ...] = tuple(member.value for member in IntegrationPrincipalStatus)
PATIENT_SCOPES: tuple[str, ...] = tuple(member.value for member in PatientScope)

KIND_LENGTH = 16
STATUS_LENGTH = 16


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


def _now() -> datetime:
    return datetime.now(UTC)


class IntegrationPrincipal(Base, UUIDMixin, TenantMixin, TimestampMixin, AuditMixin):
    """One scoped service identity (never an impersonated owner session)."""

    __tablename__ = "integration_principals"

    owner_user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(200), nullable=False)
    kind = Column(
        Enum(
            IntegrationPrincipalKind,
            native_enum=False,
            values_callable=_enum_values,
            length=KIND_LENGTH,
        ),
        nullable=False,
        default=IntegrationPrincipalKind.INTEGRATION,
        server_default=IntegrationPrincipalKind.INTEGRATION.value,
    )
    integration_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_integrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scopes = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
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
    status = Column(
        Enum(
            IntegrationPrincipalStatus,
            native_enum=False,
            values_callable=_enum_values,
            length=STATUS_LENGTH,
        ),
        nullable=False,
        default=IntegrationPrincipalStatus.ACTIVE,
        server_default=IntegrationPrincipalStatus.ACTIVE.value,
    )
    secret_prefix = Column(String(24), nullable=True, unique=True)
    secret_hash = Column(String(64), nullable=True, unique=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    rotated_from = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("integration_principals.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint("name <> ''", name="name_required"),
        CheckConstraint(f"kind IN ({_in(PRINCIPAL_KINDS)})", name="kind_vocabulary"),
        CheckConstraint(f"status IN ({_in(PRINCIPAL_STATUSES)})", name="status_vocabulary"),
        CheckConstraint(
            f"patient_scope IN ({_in(PATIENT_SCOPES)})", name="patient_scope_vocabulary"
        ),
        CheckConstraint(
            "patient_scope <> 'granted' OR jsonb_array_length(patient_ids) > 0",
            name="granted_scope_requires_ids",
        ),
        CheckConstraint(
            "kind <> 'integration' OR integration_id IS NOT NULL",
            name="integration_kind_requires_binding",
        ),
        CheckConstraint(
            "(secret_prefix IS NULL) = (secret_hash IS NULL)",
            name="secret_pair_together",
        ),
        Index("ix_integration_principals_tenant_owner", "tenant_id", "owner_user_id"),
        Index(
            "uq_integration_principals_active_integration",
            "tenant_id",
            "integration_id",
            unique=True,
            postgresql_where=text("integration_id IS NOT NULL AND status = 'active'"),
        ),
    )

    @property
    def is_live(self) -> bool:
        """True when the principal may resolve (active, unexpired)."""
        if IntegrationPrincipalStatus(self.status) is not IntegrationPrincipalStatus.ACTIVE:
            return False
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires > _now()

    def to_dict(self) -> dict:
        """Hash-free projection — the secret value never renders here."""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "owner_user_id": str(self.owner_user_id),
            "name": self.name,
            "kind": IntegrationPrincipalKind(self.kind).value,
            "integration_id": str(self.integration_id) if self.integration_id else None,
            "scopes": list(self.scopes or []),
            "patient_scope": self.patient_scope,
            "patient_ids": [str(value) for value in (self.patient_ids or [])],
            "status": IntegrationPrincipalStatus(self.status).value,
            "secret_prefix": self.secret_prefix,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "rotated_from": str(self.rotated_from) if self.rotated_from else None,
            "created_by": str(self.created_by) if self.created_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
