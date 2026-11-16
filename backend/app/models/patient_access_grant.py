"""Care-team / patient access grants (ADR 0054 Phase 2 — program Phase 7A).

One row grants a grantee principal access to one patient's record:

- **grantee** — exactly one of ``grantee_user_id`` (a staff/guardian login)
  or ``grantee_doctor_id`` (a doctor directory entry, which must itself be
  linked to a user — the service layer refuses unlinked doctors, since a
  grant nobody can authenticate as is dead data).
- **permission** — ``read | write | admin``; ``admin`` additionally lets the
  grantee manage further grants on the patient. ``read`` never satisfies a
  write check (the access helpers rank the levels).
- **source** — provenance of the decision: ``admin`` (admin surface),
  ``care_team`` (patient Access tab), ``patient`` (self-service share,
  pending until the grantee staff accepts), ``guardian`` (admin-granted for
  a patient with no owner account). ``break_glass`` is deliberately NOT a
  source — ADR 0054 D1: elevations live only in ``access_elevations``.
- **status** — the lifecycle (``pending → active → revoked/expired``,
  ``pending → declined``); the legal transitions live in exactly one module
  (:mod:`app.services.auth.patient_grants`).

``Patient.user_id`` stays the implicit ``own`` grant — no row is created for
ownership, and ``granted_by``/``accepted_at`` record the human decision.

Retention: ``record`` — erased with the tenant/patient/grantee via the
cascade FKs; revoked/expired rows are kept as the sharing ledger.
"""

import enum
from datetime import UTC

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.models.base import AuditMixin, Base, TenantMixin, TimestampMixin, UUIDMixin


class GrantPermission(enum.StrEnum):
    """What a grant allows (ranked: read < write < admin)."""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class GrantSource(enum.StrEnum):
    """Who decided to grant (provenance; break_glass is an elevation, not a grant)."""

    ADMIN = "admin"
    CARE_TEAM = "care_team"
    PATIENT = "patient"
    GUARDIAN = "guardian"


class GrantStatus(enum.StrEnum):
    """Grant lifecycle (transitions owned by the grant service)."""

    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    DECLINED = "declined"


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


def _values(cls: type) -> tuple[str, ...]:
    return tuple(member.value for member in cls)


PERMISSIONS = _values(GrantPermission)
SOURCES = _values(GrantSource)
STATUSES = _values(GrantStatus)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class PatientAccessGrant(Base, UUIDMixin, TenantMixin, TimestampMixin, AuditMixin):
    """One grantee's access to one patient (care-team / self-service share)."""

    __tablename__ = "patient_access_grants"

    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    grantee_user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    grantee_doctor_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    permission = Column(
        Enum(GrantPermission, native_enum=False, values_callable=_enum_values), nullable=False
    )
    source = Column(
        Enum(GrantSource, native_enum=False, values_callable=_enum_values), nullable=False
    )
    status = Column(
        Enum(GrantStatus, native_enum=False, values_callable=_enum_values), nullable=False
    )
    granted_by = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    granted_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    reason = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "((grantee_user_id IS NULL) <> (grantee_doctor_id IS NULL))",
            name="grantee_exactly_one",
        ),
        CheckConstraint(f"permission IN ({_in(PERMISSIONS)})", name="permission_vocabulary"),
        CheckConstraint(f"source IN ({_in(SOURCES)})", name="source_vocabulary"),
        CheckConstraint(f"status IN ({_in(STATUSES)})", name="status_vocabulary"),
        Index("ix_patient_access_grants_tenant_patient", "tenant_id", "patient_id"),
        Index("ix_patient_access_grants_tenant_grantee_user", "tenant_id", "grantee_user_id"),
        Index("ix_patient_access_grants_tenant_grantee_doctor", "tenant_id", "grantee_doctor_id"),
        # One ACTIVE grant per (patient, grantee identity): a re-grant after a
        # revoke inserts a fresh row — the ledger keeps the superseded ones.
        Index(
            "uq_patient_access_grants_active_user",
            "tenant_id",
            "patient_id",
            "grantee_user_id",
            unique=True,
            postgresql_where=text("status = 'active' AND grantee_user_id IS NOT NULL"),
            sqlite_where=text("status = 'active' AND grantee_user_id IS NOT NULL"),
        ),
        Index(
            "uq_patient_access_grants_active_doctor",
            "tenant_id",
            "patient_id",
            "grantee_doctor_id",
            unique=True,
            postgresql_where=text("status = 'active' AND grantee_doctor_id IS NOT NULL"),
            sqlite_where=text("status = 'active' AND grantee_doctor_id IS NOT NULL"),
        ),
    )

    @property
    def is_live(self) -> bool:
        """True while the grant confers access (active and not past expiry)."""
        if self.status != GrantStatus.ACTIVE:
            return False
        if self.expires_at is None:
            return True
        from datetime import datetime

        return self.expires_at > datetime.now(UTC)

    def to_dict(self) -> dict:
        """Minimal, id-shaped projection (no PHI — safe for audit payloads)."""

        def _value(member, cls):
            return cls(member).value if member is not None else None

        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "patient_id": str(self.patient_id),
            "grantee_user_id": str(self.grantee_user_id) if self.grantee_user_id else None,
            "grantee_doctor_id": str(self.grantee_doctor_id) if self.grantee_doctor_id else None,
            "permission": _value(self.permission, GrantPermission),
            "source": _value(self.source, GrantSource),
            "status": _value(self.status, GrantStatus),
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
