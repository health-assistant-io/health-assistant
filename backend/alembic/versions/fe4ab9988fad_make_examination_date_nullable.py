"""Make examination_date nullable

Revision ID: fe4ab9988fad
Revises: f3ce714f3ce4
Create Date: 2026-03-21 19:47:54.997784

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "fe4ab9988fad"
down_revision: Union[str, Sequence[str], None] = "f3ce714f3ce4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("examinations", schema=None) as batch_op:
        batch_op.alter_column(
            "examination_date", existing_type=sa.DATE(), nullable=True
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("examinations", schema=None) as batch_op:
        batch_op.alter_column(
            "examination_date", existing_type=sa.DATE(), nullable=False
        )
