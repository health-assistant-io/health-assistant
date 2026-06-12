"""Add auto_extract_metadata to ExaminationModel

Revision ID: f3ce714f3ce4
Revises: dc07adf1f3bc
Create Date: 2026-03-21 19:34:50.138648

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f3ce714f3ce4"
down_revision: Union[str, Sequence[str], None] = "dc07adf1f3bc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("examinations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("auto_extract_metadata", sa.Boolean(), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("examinations", schema=None) as batch_op:
        batch_op.drop_column("auto_extract_metadata")
