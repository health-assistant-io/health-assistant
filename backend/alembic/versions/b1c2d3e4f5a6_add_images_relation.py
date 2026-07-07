"""add IMAGES concept relation type

Revision ID: b1c2d3e4f5a6
Revises: 9a3f7c2e1b4d
Create Date: 2026-07-07

Adds the ``IMAGES`` value to the ``conceptrelationtype`` PG enum so the
polymorphic concept graph can distinguish diagnostic-imaging relationships
(Echocardiography IMAGES heart) from physical-examination ones
(Perineum EXAMINES prostate). Greenfield; ``downgrade()`` not supported
(PG enums can't easily shed values).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "9a3f7c2e1b4d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block; alembic
    # issues it in its own autocommit stanza when used with op.execute.
    op.execute("ALTER TYPE conceptrelationtype ADD VALUE IF NOT EXISTS 'IMAGES'")


def downgrade() -> None:
    # PG has no ALTER TYPE ... DROP VALUE. Leave the value in place.
    pass
