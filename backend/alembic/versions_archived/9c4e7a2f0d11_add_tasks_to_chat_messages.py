"""add tasks to chat messages

Revision ID: 9c4e7a2f0d11
Revises: 2f60048dd5ec
Create Date: 2026-06-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '9c4e7a2f0d11'
down_revision: Union[str, Sequence[str], None] = '2f60048dd5ec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'tasks',
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.drop_column('tasks')
