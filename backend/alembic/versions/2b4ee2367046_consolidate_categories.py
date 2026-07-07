"""consolidate categories into unified concepts

Revision ID: 2b4ee2367046
Revises: 1a3dd1256035
Create Date: 2026-07-05

Drops the scattered category/group tables (examination_categories,
clinical_event_categories, biomarker_groups, biomarker_group_members) and
repoints their FK consumers (examinations.category_id,
clinical_event_types.category_id) to the unified ``concepts`` table.

Also adds concept FK columns to entity tables that previously used free-text
or enum ``category`` columns:
- biomarker_definitions.class_concept_id (replaces ``category`` string)
- anatomy_structures.class_concept_id (replaces ``category`` enum)
- doctors.specialty_concept_id (replaces ``specialty`` string)
- documents.category_concept_id (replaces ``entities->'document_category'`` JSONB path)

No data migration — greenfield rebuild (no users in production).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "2b4ee2367046"
down_revision: Union[str, Sequence[str], None] = "1a3dd1256035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Add concept FK columns to entity tables
    # ------------------------------------------------------------------
    with op.batch_alter_table("biomarker_definitions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("class_concept_id", sa.UUID(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_biomarker_def_concept",
            "concepts",
            ["class_concept_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("anatomy_structures", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("class_concept_id", sa.UUID(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_anatomy_concept",
            "concepts",
            ["class_concept_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("doctors", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("specialty_concept_id", sa.UUID(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_doctor_specialty_concept",
            "concepts",
            ["specialty_concept_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("category_concept_id", sa.UUID(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_doc_category_concept",
            "concepts",
            ["category_concept_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # ------------------------------------------------------------------
    # 2. Repoint examinations.category_id FK → concepts.id
    # ------------------------------------------------------------------
    op.drop_constraint(
        "examinations_category_id_fkey", "examinations", type_="foreignkey"
    )
    op.execute("UPDATE examinations SET category_id = NULL")
    op.create_foreign_key(
        "examinations_category_id_fkey",
        "examinations",
        "concepts",
        ["category_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------
    # 3. Repoint clinical_event_types.category_id FK → concepts.id
    # ------------------------------------------------------------------
    op.drop_constraint(
        "clinical_event_types_category_id_fkey",
        "clinical_event_types",
        type_="foreignkey",
    )
    op.execute("UPDATE clinical_event_types SET category_id = NULL")
    op.create_foreign_key(
        "clinical_event_types_category_id_fkey",
        "clinical_event_types",
        "concepts",
        ["category_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------
    # 4. Drop old free-text / enum category columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("biomarker_definitions", schema=None) as batch_op:
        batch_op.drop_column("category")

    with op.batch_alter_table("doctors", schema=None) as batch_op:
        batch_op.drop_column("specialty")

    # anatomy_structures.category is a PG enum — drop the column then the type
    with op.batch_alter_table("anatomy_structures", schema=None) as batch_op:
        batch_op.drop_column("category")
    op.execute("DROP TYPE IF EXISTS anatomycategory CASCADE")

    # ------------------------------------------------------------------
    # 5. Drop the old standalone category/group tables
    # ------------------------------------------------------------------
    op.drop_table("biomarker_group_members")
    op.drop_table("biomarker_groups")
    op.drop_table("examination_categories")
    op.drop_table("clinical_event_categories")


def downgrade() -> None:
    # Not supported — this is a consolidation migration on a greenfield DB.
    pass
