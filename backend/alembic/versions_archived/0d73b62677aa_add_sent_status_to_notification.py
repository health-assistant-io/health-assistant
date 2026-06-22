"""add_sent_status_to_notification

Revision ID: 0d73b62677aa
Revises: 07c550bbe0c8
Create Date: 2026-03-24 12:38:30.884791

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0d73b62677aa"
down_revision: Union[str, Sequence[str], None] = "07c550bbe0c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add 'SENT' to NotificationStatus enum
    # We use execute because Postgres needs a specific command for enum updates
    # and it cannot be run in a transaction in some Postgres versions,
    # but here we'll try standard way.
    op.execute("ALTER TYPE notificationstatus ADD VALUE 'SENT'")


def downgrade() -> None:
    pass
