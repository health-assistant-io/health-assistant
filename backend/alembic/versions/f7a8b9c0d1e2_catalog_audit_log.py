"""catalog audit log table (Phase B)

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-08

Phase B of the catalog access-control plan
(``dev/plans/catalog-access-control-and-ui-2026-07-08.md``). Adds the
append-only ``catalog_audit_log`` table recording every catalog create / update
/ delete / promote / demote operation (who, when, scope transition, item
snapshot). Denormalized ``user_email`` and ``item_name`` survive user / item
deletion.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_email", sa.Text(), nullable=False, server_default=""),
        sa.Column("catalog_type", sa.String(50), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("operation", sa.String(20), nullable=False),
        sa.Column("from_scope", sa.String(20), nullable=True),
        sa.Column("to_scope", sa.String(20), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_catalog_audit_log_tenant_id", "catalog_audit_log", ["tenant_id"])
    op.create_index("ix_catalog_audit_log_user_id", "catalog_audit_log", ["user_id"])
    op.create_index(
        "ix_catalog_audit_log_catalog_type", "catalog_audit_log", ["catalog_type"]
    )
    op.create_index("ix_catalog_audit_log_item_id", "catalog_audit_log", ["item_id"])
    op.create_index(
        "ix_catalog_audit_log_operation", "catalog_audit_log", ["operation"]
    )
    op.create_index(
        "ix_catalog_audit_type_item", "catalog_audit_log", ["catalog_type", "item_id"]
    )
    op.create_index(
        "ix_catalog_audit_created_at", "catalog_audit_log", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_audit_created_at", table_name="catalog_audit_log")
    op.drop_index("ix_catalog_audit_type_item", table_name="catalog_audit_log")
    op.drop_index("ix_catalog_audit_log_operation", table_name="catalog_audit_log")
    op.drop_index("ix_catalog_audit_log_item_id", table_name="catalog_audit_log")
    op.drop_index(
        "ix_catalog_audit_log_catalog_type", table_name="catalog_audit_log"
    )
    op.drop_index("ix_catalog_audit_log_user_id", table_name="catalog_audit_log")
    op.drop_index("ix_catalog_audit_log_tenant_id", table_name="catalog_audit_log")
    op.drop_table("catalog_audit_log")
