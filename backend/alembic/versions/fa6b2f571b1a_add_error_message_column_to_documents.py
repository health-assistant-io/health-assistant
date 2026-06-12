"""add_error_message_column_to_documents

Revision ID: fa6b2f571b1a
Revises: 7154f1006424
Create Date: 2026-03-17 09:41:54.666135

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "fa6b2f571b1a"
down_revision: Union[str, Sequence[str], None] = "7154f1006424"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "error_message")
