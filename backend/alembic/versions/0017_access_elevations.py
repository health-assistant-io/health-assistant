"""Break-glass access elevations (ADR 0054 D1/D6 — program Phase 12A).

Creates ``access_elevations`` — one row per two-person emergency-access
decision — with the closed vocabularies mirrored in CHECK constraints
(scope ``tenant|system``, status ``pending|active|expired|revoked|denied``).

Laws enforced as constraints/indexes:

* ``ck_access_elevations_requester_is_not_approver`` — two-person approval
  (D1): the approver is a second person, never the requester.
* ``ck_access_elevations_scope_tenant_pairing`` — ``scope='system'`` rows
  are exactly the NULL-tenant rows (a system elevation belongs to the
  instance); ``scope='tenant'`` rows always carry their tenant.
* ``uq_access_elevations_live_requester`` — partial unique
  ``(scope, requested_by)`` among ``status IN ('pending','active')``: one
  live elevation per principal per scope; settled rows survive as the
  accountability ledger.
* ``break_glass`` stays out of the grant source vocabulary (ADR 0054 D1) —
  elevations are their own rows, never a grant flavor.

Row-level security: the table is nullable-tenant (system elevations), so it
carries the audit-flavor policies of ``0016_rls`` (tenant match or the
system flag — NULL-tenant rows stay hidden from plain tenants, the system
actor has full reach) plus the runtime role's DML grants, re-asserted
self-healing like ``0016`` does.

Downgrade drops the table (the owner doctrine applies — this program has
no production users yet).

Revision ID: 0017_access_elevations
Revises: 0016_rls
Create Date: 2026-11-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from app.models.access_elevation import SCOPES, STATUSES, ElevationScope, ElevationStatus

# revision identifiers, used by Alembic.
revision: str = "0017_access_elevations"
down_revision: str | Sequence[str] | None = "0016_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "neuronection_health_app"

_TENANT_CTX = "(SELECT NULLIF(current_setting('app.tenant_id', true), ''))::uuid"
_SYSTEM_CTX = "COALESCE((SELECT NULLIF(current_setting('app.system', true), ''))::boolean, false)"

#: Identical to ``0016_rls.HIDDEN_READ`` / ``TENANT_ONLY`` (the audit-flavor
#: classification this table keeps — NULL-tenant system rows stay hidden
#: from plain tenants; the system flag reaches every row).
TENANT_ONLY = f"tenant_id = {_TENANT_CTX} OR {_SYSTEM_CTX}"


def _enum_values(obj: object) -> list[str]:
    return [member.value for member in obj]


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    """Create the elevation table with its CHECK mirrors, FKs and indexes."""
    op.create_table(
        "access_elevations",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column(
            "scope",
            sa.Enum(ElevationScope, native_enum=False, values_callable=_enum_values),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(ElevationStatus, native_enum=False, values_callable=_enum_values),
            nullable=False,
        ),
        sa.Column("permissions", JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.CheckConstraint(
            f"scope IN ({_in(SCOPES)})",
            name=op.f("ck_access_elevations_scope_vocabulary"),
        ),
        sa.CheckConstraint(
            f"status IN ({_in(STATUSES)})",
            name=op.f("ck_access_elevations_status_vocabulary"),
        ),
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by <> requested_by",
            name=op.f("ck_access_elevations_requester_is_not_approver"),
        ),
        sa.CheckConstraint(
            "duration_minutes BETWEEN 1 AND 240",
            name=op.f("ck_access_elevations_duration_bounds"),
        ),
        sa.CheckConstraint(
            "(scope = 'system' AND tenant_id IS NULL) "
            "OR (scope = 'tenant' AND tenant_id IS NOT NULL)",
            name=op.f("ck_access_elevations_scope_tenant_pairing"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_access_elevations_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_access_elevations_requested_by"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["users.id"],
            name=op.f("fk_access_elevations_approved_by"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_elevations")),
    )
    for name, columns in (
        ("ix_access_elevations_tenant_id", ["tenant_id"]),
        ("ix_access_elevations_requested_by", ["requested_by"]),
        ("ix_access_elevations_approved_by", ["approved_by"]),
        (
            "ix_access_elevations_tenant_status_expires",
            ["tenant_id", "status", "expires_at"],
        ),
        ("ix_access_elevations_created_at", ["created_at"]),
        ("ix_access_elevations_updated_at", ["updated_at"]),
    ):
        op.create_index(name, "access_elevations", columns, unique=False)
    op.create_index(
        "uq_access_elevations_live_requester",
        "access_elevations",
        ["scope", "requested_by"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'active')"),
        sqlite_where=sa.text("status IN ('pending', 'active')"),
    )

    op.execute("ALTER TABLE access_elevations ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY rls_tenant_read ON access_elevations FOR SELECT USING ({TENANT_ONLY})"
    )
    op.execute(
        f"CREATE POLICY rls_tenant_write ON access_elevations FOR INSERT WITH CHECK ({TENANT_ONLY})"
    )
    op.execute(
        f"CREATE POLICY rls_tenant_write_upd ON access_elevations FOR UPDATE "
        f"USING ({TENANT_ONLY}) WITH CHECK ({TENANT_ONLY})"
    )
    op.execute(
        f"CREATE POLICY rls_tenant_write_del ON access_elevations FOR DELETE USING ({TENANT_ONLY})"
    )

    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}).scalar():
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON access_elevations TO {APP_ROLE}")


def downgrade() -> None:
    """Drop the elevation table (destructive owner doctrine — no production users)."""
    op.drop_index("uq_access_elevations_live_requester", table_name="access_elevations")
    for name in (
        "ix_access_elevations_updated_at",
        "ix_access_elevations_created_at",
        "ix_access_elevations_tenant_status_expires",
        "ix_access_elevations_approved_by",
        "ix_access_elevations_requested_by",
        "ix_access_elevations_tenant_id",
    ):
        op.drop_index(name, table_name="access_elevations")
    op.drop_table("access_elevations")
