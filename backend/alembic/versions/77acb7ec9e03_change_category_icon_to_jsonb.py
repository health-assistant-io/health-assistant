"""change_category_icon_to_jsonb

Revision ID: 77acb7ec9e03
Revises: 8e4d2f1b3a5c
Create Date: 2026-03-23 00:58:49.771411

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "77acb7ec9e03"
down_revision: Union[str, Sequence[str], None] = "8e4d2f1b3a5c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Alter column type and migrate data using postgres JSONB build object
    # We use 'USING' to convert existing VARCHAR values to {"type": "lucide", "value": "old_value"}
    op.execute(
        "ALTER TABLE examination_categories ALTER COLUMN icon TYPE JSONB USING "
        "CASE WHEN icon IS NOT NULL AND icon != '' THEN jsonb_build_object('type', 'lucide', 'value', icon) ELSE NULL END"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Convert back to VARCHAR(50) by extracting the 'value' field
    op.execute(
        "ALTER TABLE examination_categories ALTER COLUMN icon TYPE VARCHAR(50) USING "
        "CASE WHEN icon IS NOT NULL AND jsonb_typeof(icon) = 'object' THEN icon->>'value' ELSE icon::text END"
    )
