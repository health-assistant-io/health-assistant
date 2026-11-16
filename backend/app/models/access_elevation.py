"""Break-glass access elevations (ADR 0054 D1/D6 — program Phase 12A).

One row is one emergency-access decision: a principal requests a time-boxed
widening of **held** permissions, a second person approves, and the window
auto-expires. ``authorize()`` consults active rows per request (never the
token); every transition and every use is audited.

- **scope** — ``tenant`` (rows inside the requester's tenant; widened reach
  never crosses the tenant boundary) or ``system`` (an accountability window
  for a SYSTEM_ADMIN; requestable and approvable by SYSTEM_ADMIN only).
- **permissions** — a JSONB array that MUST be a subset of the requester's
  held permissions (the closed :class:`~app.core.authz.permissions.Permission`
  catalog; the service validates membership, the CHECK vocabulary laws live
  on the enum columns). An elevation widens the effective scope of the
  listed permissions — it is never a permission of its own.
- **status** — ``pending → active → expired|revoked``, ``pending → denied``;
  the legal transitions live in exactly one module
  (:mod:`app.services.auth.elevations`). ``tenant_id`` is NULL exactly for
  ``scope = 'system'`` rows, and ``requested_by <> approved_by`` is law
  (two-person; the CHECK enforces it at the row level).

Single-admin instances have no elevation path by design (D6): nobody can
approve the requester's own row; recovery is the host rescue runbook
(documented in ``SECURITY.md``), never a product bypass.

Retention: ``record`` — erased with the tenant/requester/approver via the
cascade FKs; settled rows are kept as the accountability ledger.
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
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.models.base import AuditMixin, Base, SystemScopeTenantMixin, TimestampMixin, UUIDMixin


class ElevationScope(enum.StrEnum):
    """How far an elevation reaches (tenant rows or the system actor's)."""

    TENANT = "tenant"
    SYSTEM = "system"


class ElevationStatus(enum.StrEnum):
    """Elevation lifecycle (transitions owned by the elevation service)."""

    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    DENIED = "denied"


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


def _values(cls: type) -> tuple[str, ...]:
    return tuple(member.value for member in cls)


SCOPES = _values(ElevationScope)
STATUSES = _values(ElevationStatus)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class AccessElevation(Base, UUIDMixin, SystemScopeTenantMixin, TimestampMixin, AuditMixin):
    """One two-person, auto-expiring break-glass elevation."""

    __tablename__ = "access_elevations"

    requested_by = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approved_by = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    scope = Column(
        Enum(ElevationScope, native_enum=False, values_callable=_enum_values), nullable=False
    )
    status = Column(
        Enum(ElevationStatus, native_enum=False, values_callable=_enum_values), nullable=False
    )
    permissions = Column(JSONB, nullable=False)
    reason = Column(Text, nullable=False)
    duration_minutes = Column(Integer(), nullable=False)
    requested_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    starts_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    elevation_metadata = Column("metadata", JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(f"scope IN ({_in(SCOPES)})", name="scope_vocabulary"),
        CheckConstraint(f"status IN ({_in(STATUSES)})", name="status_vocabulary"),
        CheckConstraint(
            "approved_by IS NULL OR approved_by <> requested_by",
            name="requester_is_not_approver",
        ),
        CheckConstraint(
            "duration_minutes BETWEEN 1 AND 240",
            name="duration_bounds",
        ),
        CheckConstraint(
            "(scope = 'system' AND tenant_id IS NULL)"
            " OR (scope = 'tenant' AND tenant_id IS NOT NULL)",
            name="scope_tenant_pairing",
        ),
        Index(
            "ix_access_elevations_tenant_status_expires",
            "tenant_id",
            "status",
            "expires_at",
        ),
        Index(
            "uq_access_elevations_live_requester",
            "scope",
            "requested_by",
            unique=True,
            postgresql_where=text("status IN ('pending', 'active')"),
            sqlite_where=text("status IN ('pending', 'active')"),
        ),
    )

    @property
    def is_live(self) -> bool:
        """True while the elevation confers reach (active and inside the window)."""
        if self.status != ElevationStatus.ACTIVE:
            return False
        if self.expires_at is None:
            return False
        now = _now()
        if self.starts_at is not None and self.starts_at > now:
            return False
        return self.expires_at > now

    def covers(self, permission: str) -> bool:
        """Whether ``permission`` (a ``resource:action`` value) is elevated."""
        return permission in (self.permissions or [])

    def to_dict(self) -> dict:
        """Id-shaped projection (no free-text reason — safe for audit payloads)."""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "requested_by": str(self.requested_by),
            "approved_by": str(self.approved_by) if self.approved_by else None,
            "scope": ElevationScope(self.scope).value,
            "status": ElevationStatus(self.status).value,
            "permissions": list(self.permissions or []),
            "duration_minutes": self.duration_minutes,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
        }


def _now() -> datetime:
    return datetime.now(UTC)
