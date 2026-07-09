"""catalog graph completion: class_concept_id on catalogs + AFFECTS relation

Revision ID: a1b2c3d4e5f7
Revises: f2c3d4e5f6a7
Create Date: 2026-07-08

Phase 2 of the unified-catalog architecture (``dev/plans/unified-catalog-
architecture-2026-07-08.md``). Gives ``medication_catalog`` and
``allergy_catalog`` a ``class_concept_id`` FK into ``concepts.id`` so every
catalog row has a native taxonomy classification (the established
``<role>_concept_id`` convention) and can participate in ``concept_edges``
without being mirrored as a concept. Adds the ``AFFECTS`` value to the
``conceptrelationtype`` PG enum (the biomarker→anatomy semantic link that does
not fit ``INDICATES`` cleanly).

Additive only — no data migration, no column drops. Downgrade reverses the
columns; the PG enum value cannot be shed (standard PG limitation).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, Sequence[str], None] = "f2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # class_concept_id on medication_catalog
    op.add_column(
        "medication_catalog",
        sa.Column(
            "class_concept_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_medication_catalog_class_concept",
        "medication_catalog",
        "concepts",
        ["class_concept_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_medication_catalog_class_concept_id",
        "medication_catalog",
        ["class_concept_id"],
    )

    # class_concept_id on allergy_catalog
    op.add_column(
        "allergy_catalog",
        sa.Column(
            "class_concept_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_allergy_catalog_class_concept",
        "allergy_catalog",
        "concepts",
        ["class_concept_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_allergy_catalog_class_concept_id",
        "allergy_catalog",
        ["class_concept_id"],
    )

    # AFFECTS semantic relation type (biomarker → anatomy etc.)
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block; alembic
    # issues it in its own autocommit stanza when used with op.execute.
    op.execute(
        "ALTER TYPE conceptrelationtype ADD VALUE IF NOT EXISTS 'AFFECTS'"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_allergy_catalog_class_concept_id", table_name="allergy_catalog"
    )
    op.drop_constraint(
        "fk_allergy_catalog_class_concept", "allergy_catalog", type_="foreignkey"
    )
    op.drop_column("allergy_catalog", "class_concept_id")

    op.drop_index(
        "ix_medication_catalog_class_concept_id", table_name="medication_catalog"
    )
    op.drop_constraint(
        "fk_medication_catalog_class_concept",
        "medication_catalog",
        type_="foreignkey",
    )
    op.drop_column("medication_catalog", "class_concept_id")
    # PG has no ALTER TYPE ... DROP VALUE — leave 'AFFECTS' in place.
