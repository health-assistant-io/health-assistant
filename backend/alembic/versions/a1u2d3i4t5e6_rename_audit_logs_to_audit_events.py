"""H2 audit stream rename + outcome (plan 16 H2, identity-auth §5/§17)

Renames the audit table to the family contract name and adds the
normative ``outcome`` column:

* ``audit_logs`` → ``audit_events`` (§17 names the Class S extension
  ``audit_events``; the consolidated baseline had created it as
  ``audit_logs``). The four ``ix_audit_logs_*`` indexes are renamed to
  match. Destructive-OK per H1 policy — dev DBs are recreated; existing
  rows are kept as-is.
* ``outcome`` TEXT(20) NOT NULL DEFAULT 'ok' — 'ok' (default),
  'denied' (403/404 access refusals, failed logins, refresh reuse), or
  'error' (other failures). Indexed (the admin viewer filters on it).

Product additions (``old_value``/``new_value``) stay untouched (§5
allows adding columns).

Revision ID: a1u2d3i4t5e6
Revises: h1c2o3n4t5r6
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa


revision = "a1u2d3i4t5e6"
down_revision = "h1c2o3n4t5r6"
branch_labels = None
depends_on = None

_RENAMED_INDEXES = (
    "ix_audit_logs_action",
    "ix_audit_logs_created_at",
    "ix_audit_logs_tenant_id",
    "ix_audit_logs_user_id",
)


def upgrade() -> None:
    op.rename_table("audit_logs", "audit_events")
    for old_name in _RENAMED_INDEXES:
        new_name = old_name.replace("audit_logs", "audit_events")
        op.execute(f"ALTER INDEX IF EXISTS {old_name} RENAME TO {new_name}")

    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "outcome",
                sa.String(length=20),
                nullable=False,
                server_default="ok",
            )
        )
        batch_op.create_index(
            batch_op.f("ix_audit_events_outcome"), ["outcome"], unique=False
        )


def downgrade() -> None:
    # Reversed for dev convenience only (H1 policy: no backwards
    # compatibility — pre-release databases are recreated).
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_events_outcome"))
        batch_op.drop_column("outcome")

    for old_name in _RENAMED_INDEXES:
        new_name = old_name.replace("audit_logs", "audit_events")
        op.execute(f"ALTER INDEX IF EXISTS {new_name} RENAME TO {old_name}")
    op.rename_table("audit_events", "audit_logs")
