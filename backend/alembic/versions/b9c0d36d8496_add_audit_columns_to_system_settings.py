"""add audit columns to system settings

Revision ID: b9c0d36d8496
Revises: 07347dd87716
Create Date: 2026-06-14 06:22:46.859947

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9c0d36d8496'
down_revision: Union[str, Sequence[str], None] = '07347dd87716'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('system_settings', sa.Column('created_by', sa.UUID(), nullable=True))
    op.add_column('system_settings', sa.Column('updated_by', sa.UUID(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('system_settings', 'updated_by')
    op.drop_column('system_settings', 'created_by')
