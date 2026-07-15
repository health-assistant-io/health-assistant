"""ChatMessage (session_id, created_at) composite index (audit B13)

Revision ID: o6f7a8b9c0d1
Revises: n5e6f7a8b9c0
Create Date: 2026-07-15

``chat_messages`` had a bare ``session_id`` index but messages are always loaded
``WHERE session_id = ? ORDER BY created_at`` (the relationship's ``order_by``).
A ``(session_id, created_at)`` composite serves that ordered fan-out without a
sort step.

The audit also flagged ``AuditMixin.created_by`` / ``updated_by`` as unindexed;
those are evaluated as **not worth indexing** — they are provenance columns read
*with* the row, never a ``WHERE`` predicate (grep finds only reads/writes, no
filter), so a multi-table index migration would add write cost with no query
benefit.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "o6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "n5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEX = "ix_chat_messages_session_created_at"


def upgrade() -> None:
    op.create_index(_INDEX, "chat_messages", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="chat_messages")
