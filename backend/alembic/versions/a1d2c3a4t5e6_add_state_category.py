"""add biomarker_states.category column

Adds a nullable ``category`` VARCHAR column to ``biomarker_states`` so the
admin UI can group states into subcategories (Microbiology & Serology /
Antimicrobial Susceptibility / Qualitative Presence / Limits & Thresholds /
Data Absent). Existing rows are backfilled by re-seeding
(``biomarker_states.json`` v1.1.0 includes the category on every item).

Revision ID: a1d2c3a4t5e6
Revises: s1t2a3t4e5b6
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "a1d2c3a4t5e6"
down_revision = "s1t2a3t4e5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'biomarker_states' AND column_name = 'category'"
        )
    )
    if result.scalar() is None:
        op.add_column(
            "biomarker_states",
            sa.Column("category", sa.String(length=80), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("biomarker_states", "category")
