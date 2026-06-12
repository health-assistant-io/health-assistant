"""change_task_log_message_to_text

Revision ID: af8044faf825
Revises: fe4ab9988fad
Create Date: 2026-03-22 21:52:21.325653

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "af8044faf825"
down_revision: Union[str, Sequence[str], None] = "fe4ab9988fad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Handle task_logs.message type change
    with op.batch_alter_table("task_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "message",
            existing_type=sa.VARCHAR(length=500),
            type_=sa.Text(),
            existing_nullable=False,
        )

    # Handle removal of is_default from ai_models
    with op.batch_alter_table("ai_models", schema=None) as batch_op:
        # Drop indices first if they exist
        try:
            batch_op.drop_index("idx_ai_models_default")
        except:
            pass
        try:
            batch_op.drop_index("idx_ai_models_provider_active")
        except:
            pass
        try:
            batch_op.drop_index("ix_ai_models_is_default")
        except:
            pass

        # Drop column if it exists
        try:
            batch_op.drop_column("is_default")
        except:
            pass

    # Handle removal of is_default from ai_providers
    with op.batch_alter_table("ai_providers", schema=None) as batch_op:
        try:
            batch_op.drop_index("idx_ai_providers_default")
        except:
            pass
        try:
            batch_op.drop_index("idx_ai_providers_tenant_active")
        except:
            pass
        try:
            batch_op.drop_index("ix_ai_providers_is_default")
        except:
            pass

        try:
            batch_op.drop_column("is_default")
        except:
            pass

    # Handle ai_task_assignments indices
    with op.batch_alter_table("ai_task_assignments", schema=None) as batch_op:
        try:
            batch_op.drop_index("idx_ai_task_assignments_priority")
        except:
            pass
        try:
            batch_op.drop_index("idx_ai_task_assignments_tenant_task")
        except:
            pass


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("task_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "message",
            existing_type=sa.Text(),
            type_=sa.VARCHAR(length=500),
            existing_nullable=False,
        )

    with op.batch_alter_table("ai_providers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_default", sa.BOOLEAN(), nullable=True))

    with op.batch_alter_table("ai_models", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_default", sa.BOOLEAN(), nullable=True))
