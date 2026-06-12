"""merge_heads

Revision ID: e0bb749cb0dd
Revises: 002fb3b9f7fe, 2d4a87441382
Create Date: 2026-06-09 13:56:16.009277

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e0bb749cb0dd'
down_revision: Union[str, Sequence[str], None] = ('002fb3b9f7fe', '2d4a87441382')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
