"""Data-lifecycle control plane: the erasure ledger and retention policies.

``erasure_requests``
    First-class, auditable deletion. One row per request: the subject
    (``user|patient|tenant``), who asked, whether it is a dry run, the
    ordered-walker impact report (``report`` + ``report_schema_version``) and
    the run timestamps. ``done`` rows are the **erasure ledger**: a restore
    replays their subject ids against the restored database before serving
    traffic, so a backup cannot resurrect an erased subject (plan 6 §2.7).
    ``tenant_id`` is nullable ``ON DELETE SET NULL``: a tenant erasure would
    otherwise cascade its own ledger row away, and the ledger must outlive
    the subject. Ledger rows are append-only — the lifecycle job never prunes
    them.

``retention_policies``
    Retention as **data** (constitution rule 10): scope is a lifecycle class
    or a specific table, action is ``delete|anonymize``, ``days`` is the
    window, ``timestamp_column`` names the column the cutoff compares
    against, and ``enabled`` is the operator switch. Rows are seeded from
    :mod:`app.services.data_lifecycle.retention_policies` at boot; seeding inserts missing
    rows only, so an operator edit survives the next boot. Jobs read this
    table — never a hard-coded constant or an env var.
"""

from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import text

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.enums import (
    ErasureStatus,
    ErasureSubjectType,
    RetentionAction,
    RetentionScope,
)


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


class ErasureRequest(Base, UUIDMixin, TimestampMixin):
    """One auditable data-subject erasure (dry-run or real)."""

    __tablename__ = "erasure_requests"

    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    subject_type = Column(
        SQLEnum(
            ErasureSubjectType,
            name="erasure_subject_type",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
    )
    subject_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    requested_by = Column(UUID(as_uuid=True), nullable=True, index=True)
    status = Column(
        SQLEnum(
            ErasureStatus,
            name="erasure_status",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
        default=ErasureStatus.QUEUED,
        server_default=text("'queued'"),
        index=True,
    )
    dry_run = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    report = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    report_schema_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_erasure_requests_subject", "subject_type", "subject_id"),
        CheckConstraint("report_schema_version > 0", name="report_schema_version_positive"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "subject_type": self.subject_type.value if self.subject_type else None,
            "subject_id": str(self.subject_id),
            "requested_by": str(self.requested_by) if self.requested_by else None,
            "status": self.status.value if self.status else None,
            "dry_run": bool(self.dry_run),
            "report": self.report or {},
            "report_schema_version": self.report_schema_version,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class RetentionPolicy(Base, UUIDMixin, TimestampMixin):
    """One retention rule: a lifecycle class or a specific table."""

    __tablename__ = "retention_policies"

    scope = Column(
        SQLEnum(
            RetentionScope,
            name="retention_scope",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
    )
    scope_value = Column(String(64), nullable=False)
    action = Column(
        SQLEnum(
            RetentionAction,
            name="retention_action",
            native_enum=False,
            create_constraint=True,
            values_callable=_enum_values,
            length=16,
        ),
        nullable=False,
    )
    days = Column(Integer, nullable=False, default=90, server_default=text("90"))
    timestamp_column = Column(
        String(64), nullable=False, default="created_at", server_default=text("'created_at'")
    )
    enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))

    __table_args__ = (
        UniqueConstraint("scope", "scope_value", name="uq_retention_policies_scope_value"),
        CheckConstraint("days >= 0", name="days_non_negative"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "scope": self.scope.value if self.scope else None,
            "scope_value": self.scope_value,
            "action": self.action.value if self.action else None,
            "days": self.days,
            "timestamp_column": self.timestamp_column,
            "enabled": bool(self.enabled),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
