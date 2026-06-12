"""add_ai_provider_tables

Revision ID: 7154f1006424
Revises: 34f8b79e8ce2
Create Date: 2026-03-16

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "7154f1006424"
down_revision = "34f8b79e8ce2"
branch_labels = None


def upgrade():
    # Create ai_providers table
    op.create_table(
        "ai_providers",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("name", sa.String(100), nullable=False, index=True),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("api_base", sa.String(500), nullable=False),
        sa.Column("api_key", sa.String(500), nullable=True),
        sa.Column("is_default", sa.Boolean, default=False, index=True),
        sa.Column("is_active", sa.Boolean, default=True, index=True),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text),
            nullable=True,
            default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            index=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            index=True,
        ),
        sa.Index("idx_ai_providers_tenant_active", "tenant_id", "is_active"),
        sa.Index("idx_ai_providers_default", "is_default", "is_active"),
    )

    # Create ai_models table
    op.create_table(
        "ai_models",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "provider_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("ai_providers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(200), nullable=False, index=True),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_default", sa.Boolean, default=False, index=True),
        sa.Column("is_active", sa.Boolean, default=True, index=True),
        sa.Column("max_tokens", sa.Integer, default=4096),
        sa.Column("temperature", sa.Float, default=0.7),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text),
            nullable=True,
            default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            index=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            index=True,
        ),
        sa.Index("idx_ai_models_provider_active", "provider_id", "is_active"),
        sa.Index("idx_ai_models_default", "is_default", "is_active"),
    )

    # Create ai_task_assignments table
    op.create_table(
        "ai_task_assignments",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("task_type", sa.String(50), nullable=False, index=True),
        sa.Column(
            "provider_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("ai_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "model_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("ai_models.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean, default=True, index=True),
        sa.Column("priority", sa.Integer, default=0),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            index=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            index=True,
        ),
        sa.Index(
            "idx_ai_task_assignments_tenant_task", "tenant_id", "task_type", "is_active"
        ),
        sa.Index("idx_ai_task_assignments_priority", "priority"),
    )


def downgrade():
    op.drop_table("ai_task_assignments")
    op.drop_table("ai_models")
    op.drop_table("ai_providers")
