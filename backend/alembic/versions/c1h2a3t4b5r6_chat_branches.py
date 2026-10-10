"""Chat message branches: parent tree + active-path pointers (plan 13 H2).

Adds the family branching contract columns (plan 11 §6) to health's chat
tables: ``chat_messages.parent_id`` + ``chat_messages.active_child_id`` and
``chat_sessions.active_root_id`` (indexed, service-enforced pointers — no
FKs, matching career's 0014 reference).

Backfills every existing session into an identical LINEAR tree so old
conversations look unchanged: messages chain parent -> active_child in
(created_at, id) order and each session points at its first message.
"""

import itertools

import sqlalchemy as sa

from alembic import op

revision = "c1h2a3t4b5r6"
down_revision = "h5m2f3a4t6o7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("parent_id", sa.UUID(), nullable=True))
    op.add_column("chat_messages", sa.Column("active_child_id", sa.UUID(), nullable=True))
    op.create_index("ix_chat_messages_parent_id", "chat_messages", ["parent_id"])
    op.create_index("ix_chat_messages_active_child_id", "chat_messages", ["active_child_id"])
    op.add_column("chat_sessions", sa.Column("active_root_id", sa.UUID(), nullable=True))
    op.create_index("ix_chat_sessions_active_root_id", "chat_sessions", ["active_root_id"])

    messages = sa.table(
        "chat_messages",
        sa.column("id", sa.UUID()),
        sa.column("session_id", sa.UUID()),
        sa.column("parent_id", sa.UUID()),
        sa.column("active_child_id", sa.UUID()),
        sa.column("created_at", sa.DateTime()),
    )
    sessions = sa.table(
        "chat_sessions",
        sa.column("id", sa.UUID()),
        sa.column("active_root_id", sa.UUID()),
    )

    conn = op.get_bind()
    session_ids = [row[0] for row in conn.execute(sa.select(messages.c.session_id).distinct())]
    for session_id in session_ids:
        rows = conn.execute(
            sa.select(messages.c.id)
            .where(messages.c.session_id == session_id)
            .order_by(messages.c.created_at, messages.c.id)
        ).fetchall()
        ids = [row[0] for row in rows]
        for parent_id, child_id in itertools.pairwise(ids):
            conn.execute(
                messages.update().where(messages.c.id == parent_id).values(active_child_id=child_id)
            )
            conn.execute(
                messages.update().where(messages.c.id == child_id).values(parent_id=parent_id)
            )
        if ids:
            conn.execute(
                sessions.update().where(sessions.c.id == session_id).values(active_root_id=ids[0])
            )


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_active_root_id", table_name="chat_sessions")
    op.drop_column("chat_sessions", "active_root_id")
    op.drop_index("ix_chat_messages_active_child_id", table_name="chat_messages")
    op.drop_index("ix_chat_messages_parent_id", table_name="chat_messages")
    op.drop_column("chat_messages", "active_child_id")
    op.drop_column("chat_messages", "parent_id")
