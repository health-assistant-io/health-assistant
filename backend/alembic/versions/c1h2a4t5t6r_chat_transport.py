"""Model rows gain the chat_transport seam (ADR-0039).

Nullable ``ai_models.chat_transport`` (``'responses'`` |
``'completions'``; NULL/anything else = auto = chat completions) — the
per-model OpenAI API surface knob. The gpt-6 family rejects function
tools combined with ``reasoning_effort`` on chat completions; a row
serving tool tasks with reasoning opts into ``responses`` (honored only
when the base URL rides OpenAI's own API, and always constructed
store-less).

No data migration: NULL means auto, which is every existing row's
current behavior.
"""

import sqlalchemy as sa

from alembic import op

revision = "c1h2a4t5t6r"
down_revision = "t1e2m3p4r5o6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_models", sa.Column("chat_transport", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_models", "chat_transport")
