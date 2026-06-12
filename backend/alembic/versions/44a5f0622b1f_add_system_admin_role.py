"""add_system_admin_role

Revision ID: 44a5f0622b1f
Revises: e0bb749cb0dd
Create Date: 2026-06-09 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '44a5f0622b1f'
down_revision: Union[str, Sequence[str], None] = 'e0bb749cb0dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres doesn't allow ALTER TYPE ... ADD VALUE inside a transaction block 
    # until Postgres 12, and Health Assistant requirements say Postgres 14+.
    # However, Alembic usually runs in a transaction.
    # We use a safe check and execute.
    
    op.execute("COMMIT") # End the current transaction
    op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'SYSTEM_ADMIN'")

def downgrade() -> None:
    # Removing enum values is hard in Postgres, usually involves recreating the type.
    # Given this is a critical role, we'll just leave it.
    pass
