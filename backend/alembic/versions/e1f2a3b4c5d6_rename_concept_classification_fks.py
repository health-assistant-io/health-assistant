"""rename concept classification FKs

Revision ID: e1f2a3b4c5d6
Revises: b1c2d3e4f5a6
Create Date: 2026-07-07

Standardizes every domain-specific classification FK into ``concepts.id`` on
the ``<role>_concept_id`` naming convention:

    examinations.category_id           -> category_concept_id
    clinical_event_types.category_id   -> category_concept_id

``documents.category_concept_id`` already exists in the DB (added by
``2b4ee2367046``) but lacked an index and was never declared on the ORM —
this migration adds the missing index; the ORM model is reconciled in the
same change set. See ``dev/plans/concept-fk-naming-convention-2026-07-07.md``.

PostgreSQL keeps FK constraints and indexes attached across a ``RENAME
COLUMN``; the constraint/index *names* are renamed explicitly so introspection
stays self-describing.
"""
from alembic import op
from sqlalchemy import text as sa_text


# revision identifiers, used by Alembic.
revision = "e1f2a3b4c5d6"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


# (table, old_col, new_col, old_fk, new_fk, old_idx, new_idx)
_RENAMES = [
    (
        "examinations",
        "category_id",
        "category_concept_id",
        "examinations_category_id_fkey",
        "examinations_category_concept_id_fkey",
        "ix_examinations_category_id",
        "ix_examinations_category_concept_id",
    ),
    (
        "clinical_event_types",
        "category_id",
        "category_concept_id",
        "clinical_event_types_category_id_fkey",
        "clinical_event_types_category_concept_id_fkey",
        "ix_clinical_event_types_category_id",
        "ix_clinical_event_types_category_concept_id",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()

    for table, old_col, new_col, old_fk, new_fk, old_idx, new_idx in _RENAMES:
        op.execute(
            f'ALTER TABLE "{table}" RENAME COLUMN "{old_col}" TO "{new_col}";'
        )
        op.execute(
            f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old_fk}" TO "{new_fk}";'
        )
        op.execute(f'ALTER INDEX "{old_idx}" RENAME TO "{new_idx}";')

    # documents.category_concept_id: the column + FK (fk_doc_category_concept)
    # were added by 2b4ee2367046 but never indexed. Add the index so the ORM
    # model (which declares index=True) and the DB agree. Guarded so a DB that
    # somehow lacks the column converges rather than crashes.
    has_col = bind.execute(
        sa_text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'documents' AND column_name = 'category_concept_id'"
        )
    ).scalar()
    if not has_col:
        op.execute(
            'ALTER TABLE "documents" ADD COLUMN "category_concept_id" UUID NULL;'
        )
        op.create_foreign_key(
            "fk_doc_category_concept",
            "documents",
            "concepts",
            ["category_concept_id"],
            ["id"],
            ondelete="SET NULL",
        )
    has_idx = bind.execute(
        sa_text(
            "SELECT 1 FROM pg_indexes "
            "WHERE indexname = 'ix_documents_category_concept_id'"
        )
    ).scalar()
    if not has_idx:
        op.create_index(
            "ix_documents_category_concept_id",
            "documents",
            ["category_concept_id"],
        )

    # ------------------------------------------------------------------
    # 3. Backfill missing indexes on the other concept-FK family columns.
    # The ORM models declare ``index=True`` on these but no prior migration
    # created the index — so the model and DB silently disagreed. This
    # refactor standardizes the whole concept-FK family, so we converge them
    # here. Idempotent (``IF NOT EXISTS``) and safe (b-tree on a UUID).
    # ------------------------------------------------------------------
    _MISSING_CONCEPT_INDEXES = [
        ("ix_anatomy_structures_class_concept_id", "anatomy_structures",
         ["class_concept_id"]),
        ("ix_biomarker_definitions_class_concept_id", "biomarker_definitions",
         ["class_concept_id"]),
        ("ix_doctors_specialty_concept_id", "doctors", ["specialty_concept_id"]),
        ("ix_concepts_parent", "concepts", ["parent_id"]),
    ]
    for idx_name, table, cols in _MISSING_CONCEPT_INDEXES:
        has = bind.execute(
            sa_text(
                "SELECT 1 FROM pg_indexes WHERE indexname = :n"
            ),
            {"n": idx_name},
        ).scalar()
        if not has:
            op.create_index(idx_name, table, cols)


def downgrade() -> None:
    bind = op.get_bind()

    # Drop the backfilled concept-family indexes (this migration created them).
    _MISSING_CONCEPT_INDEXES = [
        "ix_concepts_parent",
        "ix_doctors_specialty_concept_id",
        "ix_biomarker_definitions_class_concept_id",
        "ix_anatomy_structures_class_concept_id",
    ]
    for idx_name in _MISSING_CONCEPT_INDEXES:
        has = bind.execute(
            sa_text("SELECT 1 FROM pg_indexes WHERE indexname = :n"),
            {"n": idx_name},
        ).scalar()
        if has:
            _table = {
                "ix_concepts_parent": "concepts",
                "ix_doctors_specialty_concept_id": "doctors",
                "ix_biomarker_definitions_class_concept_id": "biomarker_definitions",
                "ix_anatomy_structures_class_concept_id": "anatomy_structures",
            }[idx_name]
            op.drop_index(idx_name, table_name=_table)

    # Drop the documents index we added (leave the column/FK — they predate
    # this migration and the ORM now declares them).
    has_idx = bind.execute(
        sa_text(
            "SELECT 1 FROM pg_indexes "
            "WHERE indexname = 'ix_documents_category_concept_id'"
        )
    ).scalar()
    if has_idx:
        op.drop_index("ix_documents_category_concept_id", table_name="documents")

    for table, old_col, new_col, old_fk, new_fk, old_idx, new_idx in _RENAMES:
        op.execute(
            f'ALTER TABLE "{table}" RENAME COLUMN "{new_col}" TO "{old_col}";'
        )
        op.execute(
            f'ALTER TABLE "{table}" RENAME CONSTRAINT "{new_fk}" TO "{old_fk}";'
        )
        op.execute(f'ALTER INDEX "{new_idx}" RENAME TO "{old_idx}";')
