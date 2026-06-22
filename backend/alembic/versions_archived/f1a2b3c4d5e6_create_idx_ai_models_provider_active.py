"""create idx_ai_models_provider_active index

Revision ID: f1a2b3c4d5e6
Revises: b3f1c2a4d5e6
Create Date: 2026-06-21 12:00:00.000000

Fixes the AIModel.__table_args typo (was __table_args without trailing
underscores) that silently prevented the composite index from ever being
created on existing databases. See audit item A3.
"""
from alembic import op


revision = "f1a2b3c4d5e6"
down_revision = "b3f1c2a4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_models_provider_active "
        "ON ai_models (provider_id, is_active)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_ai_models_provider_active")
