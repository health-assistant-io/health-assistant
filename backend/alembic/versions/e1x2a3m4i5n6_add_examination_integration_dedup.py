"""add examination integration dedup index

Workstream E.1 of the integrations follow-ups pass
(plan: dev/plans/integrations-sdk-followups-2026-07-21.md).

The ``examinations`` table already has ``source_integration_id`` and
``external_id`` columns (they've been there for a while, originally added
for the bridge provider's dedup), but **no unique index** to enforce it
— the bridge's dedup was application-level and only worked for its own
writes. This migration adds the same partial unique index pattern we
shipped on ``clinical_events`` in B.1
(``b1l2i3n4t5ev_add_clinical_event_integration_dedup.py``):

  - Partial unique index on
    ``(tenant_id, patient_id, source_integration_id, external_id)``
  - Fires only when all four columns are non-NULL (UI-created rows bypass
    it; integration-sourced rows are deduped at the DB layer).
  - Catches the race window between the service-level SELECT and the
    subsequent INSERT — a concurrent sync attempt that wins the INSERT
    raises ``IntegrityError`` instead of double-inserting.

No schema change (columns already exist). No data backfill. Downgrade
drops the index only.

Revision ID: e1x2a3m4i5n6
Revises: b1l2i3n4t5ev
Create Date: 2026-07-21
"""

from alembic import op


revision = "e1x2a3m4i5n6"
down_revision = "b1l2i3n4t5ev"
branch_labels = None
depends_on = None


_DEDUP_INDEX = "uq_examination_integration_dedup"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_DEDUP_INDEX}
        ON examinations (tenant_id, patient_id, source_integration_id, external_id)
        WHERE source_integration_id IS NOT NULL
          AND external_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_DEDUP_INDEX}")
