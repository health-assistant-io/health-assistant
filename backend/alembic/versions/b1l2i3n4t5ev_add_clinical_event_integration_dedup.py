"""add clinical_event integration dedup columns (source_integration_id + external_id)

Workstream B.1 of the integrations follow-ups pass
(plan: dev/plans/integrations-sdk-followups-2026-07-21.md).

Adds two nullable columns to ``clinical_events`` so an integration can
dedup its pulled events across syncs:

  - ``source_integration_id`` — FK to ``user_integrations.id`` (ON DELETE
    SET NULL, matching ``examinations.source_integration_id``).
  - ``external_id`` — free-text string the integration supplies (the
    upstream hospital's encounter id, the wearable's session id, ...).

Plus a **partial unique index** on
``(tenant_id, patient_id, source_integration_id, external_id)`` that only
fires when all four columns are non-NULL. UI-created events (which leave
both fields NULL) are not constrained; integration-sourced events are
deduped automatically at the DB layer. Matches the existing pattern on
``examinations`` (``examination_model.py:53-59``).

Downgrade drops the columns and the index. No data backfill needed —
existing UI-created rows just have NULLs in both new columns.

Revision ID: b1l2i3n4t5ev
Revises: p8e5f6g7h8i9
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "b1l2i3n4t5ev"
down_revision = "p8e5f6g7h8i9"
branch_labels = None
depends_on = None


_DEDUP_INDEX = "uq_clinical_event_integration_dedup"


def upgrade() -> None:
    op.add_column(
        "clinical_events",
        sa.Column(
            "source_integration_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "clinical_events",
        sa.Column("external_id", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_clinical_events_source_integration_id",
        "clinical_events",
        ["source_integration_id"],
    )
    op.create_index(
        "ix_clinical_events_external_id",
        "clinical_events",
        ["external_id"],
    )
    op.create_foreign_key(
        "fk_clinical_events_source_integration_id",
        "clinical_events",
        "user_integrations",
        ["source_integration_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Partial unique index — only enforces uniqueness when ALL four columns
    # are non-NULL. UI-created events (both fields NULL) bypass it.
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_DEDUP_INDEX}
        ON clinical_events (tenant_id, patient_id, source_integration_id, external_id)
        WHERE source_integration_id IS NOT NULL
          AND external_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_DEDUP_INDEX}")
    op.drop_constraint(
        "fk_clinical_events_source_integration_id",
        "clinical_events",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_clinical_events_external_id", table_name="clinical_events"
    )
    op.drop_index(
        "ix_clinical_events_source_integration_id", table_name="clinical_events"
    )
    op.drop_column("clinical_events", "external_id")
    op.drop_column("clinical_events", "source_integration_id")
