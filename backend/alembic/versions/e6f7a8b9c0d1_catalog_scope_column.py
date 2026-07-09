"""catalog scope column + ownership-based access (Phase A)

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-08

Phase A of the catalog access-control plan
(``dev/plans/catalog-access-control-and-ui-2026-07-08.md``). Adds an explicit
``scope`` column (``system`` | ``tenant`` | ``user``) to every registered
catalog table and backfills it from ``tenant_id`` (NULL → system, non-NULL →
tenant). Also adds the ``AuditMixin`` columns (``created_by`` / ``updated_by``)
to ``anatomy_structures`` so ownership checks work uniformly across all
catalogs.

The new ``CatalogAccessPolicy`` decides access by ``(role, item.scope,
ownership)`` instead of the old coarse ``(role, is_global_row)`` — letting any
user *create* (lands in user-scope) without being able to break curated
system/tenant data.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every catalog table that gains the scope column.
SCOPE_TABLES = (
    "biomarker_definitions",
    "medication_catalog",
    "allergy_catalog",
    "vaccine_catalog",
    "anatomy_structures",
    "concepts",
)

# Enum values persisted as their lowercase string form.
SCOPE_VALUES = ("system", "tenant", "user")


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Create the shared PG enum type (checkfirst so a partial state is
    #    tolerated). Used by the ``scope`` column on every catalog table.
    postgresql.ENUM(*SCOPE_VALUES, name="catalogscope").create(
        bind, checkfirst=True
    )
    # Reference that does NOT try to (re)create the type when columns are added.
    scope_type = postgresql.ENUM(
        *SCOPE_VALUES, name="catalogscope", create_type=False
    )

    # 2. anatomy_structures: add the AuditMixin columns (created_by/updated_by)
    #    so the ownership model is uniform. Other catalog tables already have
    #    them.
    op.add_column(
        "anatomy_structures",
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "anatomy_structures",
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # 3. Add the scope column to every catalog table with a server default of
    #    'system' so existing rows are classified immediately.
    for table in SCOPE_TABLES:
        op.add_column(
            table,
            sa.Column(
                "scope",
                scope_type,
                nullable=False,
                server_default="system",
            ),
        )
        op.create_index(f"ix_{table}_scope", table, ["scope"])

    # 4. Backfill: tenant_id IS NULL stays 'system' (the default); non-NULL
    #    becomes 'tenant'. (No 'user'-scope rows exist pre-migration.)
    for table in SCOPE_TABLES:
        op.execute(
            f"UPDATE {table} SET scope = 'tenant' WHERE tenant_id IS NOT NULL"
        )


def downgrade() -> None:
    for table in SCOPE_TABLES:
        op.drop_index(f"ix_{table}_scope", table_name=table)
        op.drop_column(table, "scope")

    op.drop_column("anatomy_structures", "updated_by")
    op.drop_column("anatomy_structures", "created_by")

    postgresql.ENUM(
        *SCOPE_VALUES,
        name="catalogscope",
        create_type=False,
    ).drop(op.get_bind(), checkfirst=True)
