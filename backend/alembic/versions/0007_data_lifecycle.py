"""Data lifecycle: the erasure ledger and retention policies as data.

One revision (plan 6 W1-W3; parent plan constitution rules 7a, 10):

**``erasure_requests``** is the first-class, auditable deletion record. One
row per request: subject (`user|patient|tenant`), requester, dry-run flag,
the ordered-walker impact report (`report` + `report_schema_version`) and
run timestamps. `done` rows are the **erasure ledger** replayed against a
restored backup before it serves traffic. `tenant_id` is nullable
(`ON DELETE SET NULL`) so a tenant erasure's own ledger row survives the
tenant; `requested_by` deliberately has no FK so the ledger outlives the
account that asked.

**``retention_policies``** turns bounded retention into operator-editable
data: `scope` (`class|table`) + `scope_value`, `action`
(`delete|anonymize`), `days`, the `timestamp_column` the cutoff compares
against, and `enabled`. Rows are seeded at boot from
`app.services.retention_policies` (insert-missing, so operator edits win);
the beat job reads this table instead of hard-coded windows. The
checkpoint-pruning window moves here too — `AI_CHECKPOINT_RETENTION_DAYS`
is retired in the same change (one source, no env/table dual).

Downgrade drops both tables and restores the previous shape; no data
backfill is attempted (owner doctrine — this program has no users and the
policy rows are re-seeded).

Revision ID: 0007_data_lifecycle
Revises: 0006_connector_framework
Create Date: 2026-10-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_data_lifecycle"
down_revision: str | Sequence[str] | None = "0006_connector_framework"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUBJECT_TYPES = ("user", "patient", "tenant")
_ERASURE_STATUSES = ("queued", "running", "done", "failed", "dry_run_done")
_RETENTION_SCOPES = ("class", "table")
_RETENTION_ACTIONS = ("delete", "anonymize")


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "retention_policies",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_value", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("days", sa.Integer(), server_default=sa.text("90"), nullable=False),
        sa.Column(
            "timestamp_column",
            sa.String(length=64),
            server_default=sa.text("'created_at'"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.CheckConstraint(
            f"scope IN ({_in(_RETENTION_SCOPES)})",
            name=op.f("ck_retention_policies_retention_scope"),
        ),
        sa.CheckConstraint(
            f"action IN ({_in(_RETENTION_ACTIONS)})",
            name=op.f("ck_retention_policies_retention_action"),
        ),
        sa.CheckConstraint("days >= 0", name=op.f("ck_retention_policies_days_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retention_policies")),
        sa.UniqueConstraint("scope", "scope_value", name=op.f("uq_retention_policies_scope_value")),
    )
    for name in (
        "ix_retention_policies_created_at",
        "ix_retention_policies_updated_at",
    ):
        op.create_index(name, "retention_policies", [name.removeprefix("ix_retention_policies_")])

    op.create_table(
        "erasure_requests",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column("dry_run", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "report_schema_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.CheckConstraint(
            f"subject_type IN ({_in(_SUBJECT_TYPES)})",
            name=op.f("ck_erasure_requests_erasure_subject_type"),
        ),
        sa.CheckConstraint(
            f"status IN ({_in(_ERASURE_STATUSES)})",
            name=op.f("ck_erasure_requests_erasure_status"),
        ),
        sa.CheckConstraint(
            "report_schema_version > 0",
            name=op.f("ck_erasure_requests_report_schema_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_erasure_requests_tenant_id"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_erasure_requests")),
    )
    for name, columns in (
        ("ix_erasure_requests_created_at", ["created_at"]),
        ("ix_erasure_requests_requested_by", ["requested_by"]),
        ("ix_erasure_requests_status", ["status"]),
        ("ix_erasure_requests_subject", ["subject_type", "subject_id"]),
        ("ix_erasure_requests_subject_id", ["subject_id"]),
        ("ix_erasure_requests_tenant_id", ["tenant_id"]),
        ("ix_erasure_requests_updated_at", ["updated_at"]),
    ):
        op.create_index(name, "erasure_requests", columns, unique=False)


def downgrade() -> None:
    op.drop_table("erasure_requests")
    op.drop_table("retention_policies")
