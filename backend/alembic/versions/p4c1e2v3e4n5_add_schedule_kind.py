"""add clinical_event_types.schedule_kind

Adds an explicit ``schedule_kind`` enum column to ``clinical_event_types``
declaring how instances of each type should render in calendar/schedule views
(``state`` | ``range`` | ``recurring`` | ``point``). Phase 4 of the
calendar-ongoing-events plan: replaces the frontend's status-based heuristic
with an admin-declared intent set on the type blueprint.

Column is nullable so existing rows survive untouched — the frontend falls
back to the status-based heuristic when ``schedule_kind IS NULL``. Seed update
sets appropriate values per shipped type.

Revision ID: p4c1e2v3e4n5
Revises: s1t2m3o4d5e6
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "p4c1e2v3e4n5"
down_revision = "s1t2m3o4d5e6"
branch_labels = None
depends_on = None

# Bind-time enum so we can both create the type explicitly and reference it
# from the column add.
SCHEDULE_KIND = sa.Enum(
    "state", "range", "recurring", "point", name="schedulekind"
)


def upgrade() -> None:
    # Create the PG enum type first — `op.add_column` does not auto-create it.
    SCHEDULE_KIND.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "clinical_event_types",
        sa.Column("schedule_kind", SCHEDULE_KIND, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clinical_event_types", "schedule_kind")
    SCHEDULE_KIND.drop(op.get_bind(), checkfirst=True)
