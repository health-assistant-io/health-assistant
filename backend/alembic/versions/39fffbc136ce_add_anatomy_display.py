"""Add display JSONB column to anatomy_structures

Revision ID: 39fffbc136ce
Revises: 4f61f50eb0be
Create Date: 2026-06-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '39fffbc136ce'
down_revision: Union[str, Sequence[str], None] = '4f61f50eb0be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'anatomy_structures',
        sa.Column('display', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('anatomy_structures', 'display')
