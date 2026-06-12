"""Add chat sessions and messages

Revision ID: dc07adf1f3bc
Revises: fd359dc0d5ce
Create Date: 2026-03-20 20:29:42.776242

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "dc07adf1f3bc"
down_revision: Union[str, Sequence[str], None] = "fd359dc0d5ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "chat_sessions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column(
            "metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["fhir_patients.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("chat_sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_chat_sessions_user_id"), ["user_id"], unique=False
        )

    op.create_table(
        "chat_messages",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["chat_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_chat_messages_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_chat_messages_session_id"), ["session_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_chat_messages_updated_at"), ["updated_at"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_chat_messages_updated_at"))
        batch_op.drop_index(batch_op.f("ix_chat_messages_session_id"))
        batch_op.drop_index(batch_op.f("ix_chat_messages_created_at"))

    op.drop_table("chat_messages")
    with op.batch_alter_table("chat_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_chat_sessions_user_id"))
        batch_op.drop_index(batch_op.f("ix_chat_sessions_updated_at"))
        batch_op.drop_index(batch_op.f("ix_chat_sessions_tenant_id"))
        batch_op.drop_index(batch_op.f("ix_chat_sessions_patient_id"))
        batch_op.drop_index(batch_op.f("ix_chat_sessions_created_at"))

    op.drop_table("chat_sessions")
