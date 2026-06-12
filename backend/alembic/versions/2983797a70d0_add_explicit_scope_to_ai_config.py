"""add explicit scope to ai config

Revision ID: 2983797a70d0
Revises: 5b22c66d1503
Create Date: 2026-03-24 10:43:40.059522

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2983797a70d0"
down_revision: Union[str, Sequence[str], None] = "5b22c66d1503"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create enum type first
    aiscope_enum = sa.Enum("SYSTEM", "TENANT", "USER", name="aiscope")
    aiscope_enum.create(op.get_bind(), checkfirst=True)

    # 1. Add scope to ai_providers (nullable first for population)
    with op.batch_alter_table("ai_providers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "scope",
                sa.Enum("SYSTEM", "TENANT", "USER", name="aiscope"),
                nullable=True,
            )
        )

    # 2. Populate scope for ai_providers
    op.execute("UPDATE ai_providers SET scope = 'USER' WHERE user_id IS NOT NULL")
    op.execute(
        "UPDATE ai_providers SET scope = 'TENANT' WHERE user_id IS NULL AND tenant_id IS NOT NULL"
    )
    op.execute(
        "UPDATE ai_providers SET scope = 'SYSTEM' WHERE user_id IS NULL AND tenant_id IS NULL"
    )

    # 3. Make non-nullable and add indexes
    with op.batch_alter_table("ai_providers", schema=None) as batch_op:
        batch_op.alter_column("scope", nullable=False)
        batch_op.create_index("idx_ai_providers_scope", ["scope"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_ai_providers_scope"), ["scope"], unique=False
        )

    # 4. Add scope to ai_task_assignments
    with op.batch_alter_table("ai_task_assignments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "scope",
                sa.Enum("SYSTEM", "TENANT", "USER", name="aiscope"),
                nullable=True,
            )
        )

    # 5. Populate scope for assignments
    op.execute(
        "UPDATE ai_task_assignments SET scope = 'USER' WHERE user_id IS NOT NULL"
    )
    op.execute(
        "UPDATE ai_task_assignments SET scope = 'TENANT' WHERE user_id IS NULL AND tenant_id IS NOT NULL"
    )
    op.execute(
        "UPDATE ai_task_assignments SET scope = 'SYSTEM' WHERE user_id IS NULL AND tenant_id IS NULL"
    )

    # 6. Make non-nullable and add indexes
    with op.batch_alter_table("ai_task_assignments", schema=None) as batch_op:
        batch_op.alter_column("scope", nullable=False)
        batch_op.create_index("idx_ai_task_assignments_scope", ["scope"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_ai_task_assignments_scope"), ["scope"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("ai_task_assignments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_task_assignments_scope"))
        batch_op.drop_index("idx_ai_task_assignments_scope")
        batch_op.drop_column("scope")

    with op.batch_alter_table("ai_providers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_providers_scope"))
        batch_op.drop_index("idx_ai_providers_scope")
        batch_op.drop_column("scope")

    # Drop enum type
    sa.Enum(name="aiscope").drop(op.get_bind(), checkfirst=True)
