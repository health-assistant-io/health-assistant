"""make clinical_event_types.schedule_kind NOT NULL

Phase 8a of the calendar-ongoing-events plan: tighten the wire format.
Phase 4 added ``schedule_kind`` as nullable so legacy rows survived
untouched. With the greenfield constraint (no users), every shipped seed
now declares it, so we promote it to NOT NULL with a server default of
``'state'`` (the safe "never per-day expansion" rendering).

The migration backfills any NULL rows to ``'state'`` first, then adds
the NOT NULL constraint + server default.

Revision ID: p8a1b2c3d4e5
Revises: p4c1e2v3e4n5
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "p8a1b2c3d4e5"
down_revision = "p4c1e2v3e4n5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Backfill any NULL values to 'state' before adding the constraint.
    #    Every shipped seed already declares a non-NULL value, so this is a
    #    defensive no-op for greenfield deploys — but cheap insurance against
    #    a half-applied Phase 4 migration or manual edits.
    op.execute(
        "UPDATE clinical_event_types SET schedule_kind = 'state' "
        "WHERE schedule_kind IS NULL"
    )
    # 2. Add the server default + NOT NULL constraint.
    op.alter_column(
        "clinical_event_types",
        "schedule_kind",
        existing_type=sa.Enum("state", "range", "recurring", "point", name="schedulekind"),
        nullable=False,
        server_default="state",
    )


def downgrade() -> None:
    # Revert to nullable, drop the server default. Existing values are kept
    # (we don't null them out — that would lose real data).
    op.alter_column(
        "clinical_event_types",
        "schedule_kind",
        existing_type=sa.Enum("state", "range", "recurring", "point", name="schedulekind"),
        nullable=True,
        server_default=None,
    )
