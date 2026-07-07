"""multi-kind concepts: kind column -> concept_kind_tags join table

Revision ID: 9a3f7c2e1b4d
Revises: 2b4ee2367046
Create Date: 2026-07-07

Moves the single-valued ``concepts.kind`` column to a many-to-many
``concept_kind_tags`` join table so a single concept can carry multiple
domain tags (e.g. "Blood Laboratory" is simultaneously an
``examination_category``, a ``biomarker_class``, and a ``document_category``).

This is a **pure schema migration**: it creates the join table, denormalized
``primary_kind`` column, backfills both from the legacy ``kind`` column for
any rows present at upgrade time, swaps the unique index from
``(kind, slug, tenant)`` to ``(slug, tenant)``, and drops ``kind``.

**Deduplication of the legacy 16 same-name concepts is NOT done here** — it
lives entirely in the seed data (``data/seeds/concepts.json``), which is the
single source of truth for which concepts exist. This migration assumes the
``concepts`` table is empty or contains no duplicate ``(slug, tenant)`` rows
at upgrade time (greenfield, per the ``consolidate_categories`` note: "no
users in production"). The seed loader writes the already-merged multi-kind
rows.

``downgrade()`` is not supported (consolidation migration).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "9a3f7c2e1b4d"
down_revision: Union[str, Sequence[str], None] = "2b4ee2367046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NULL_TENANT = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create the concept_kind_tags join table (reuses the existing
    #    conceptkind PG enum type — no new enum needed).
    # ------------------------------------------------------------------
    op.create_table(
        "concept_kind_tags",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("concept_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(
                "specialty",
                "examination_category",
                "event_category",
                "biomarker_class",
                "biomarker_panel",
                "anatomy_class",
                "vaccine_class",
                "medication_class",
                "document_category",
                "disease",
                "body_system",
                "procedure",
                "lifestyle",
                "factor",
                "symptom",
                "organ",
                name="conceptkind",
                create_type=False,
            ),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["concept_id"], ["concepts.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_concept_kind_tags_concept_id", "concept_kind_tags", ["concept_id"]
    )
    op.create_index(
        "ix_concept_kind_tags_unique",
        "concept_kind_tags",
        ["concept_id", "kind"],
        unique=True,
    )
    op.create_index("ix_concept_kind_tags_kind", "concept_kind_tags", ["kind"])
    op.create_index(
        "ix_concept_kind_tags_created_at", "concept_kind_tags", ["created_at"]
    )
    op.create_index(
        "ix_concept_kind_tags_updated_at", "concept_kind_tags", ["updated_at"]
    )

    # ------------------------------------------------------------------
    # 2. Add primary_kind to concepts (denormalized mirror of one tag,
    #    for cheap display ordering + single-badge rendering).
    # ------------------------------------------------------------------
    with op.batch_alter_table("concepts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "primary_kind",
                postgresql.ENUM(
                    "specialty",
                    "examination_category",
                    "event_category",
                    "biomarker_class",
                    "biomarker_panel",
                    "anatomy_class",
                    "vaccine_class",
                    "medication_class",
                    "document_category",
                    "disease",
                    "body_system",
                    "procedure",
                    "lifestyle",
                    "factor",
                    "symptom",
                    "organ",
                    name="conceptkind",
                    create_type=False,
                ),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_concepts_primary_kind", ["primary_kind"], unique=False
        )

    # ------------------------------------------------------------------
    # 3. Backfill kind tags + primary_kind from the legacy kind column.
    #    No-op on an empty (greenfield) table; correct for any pre-existing
    #    rows. Deduplication of same-name concepts is the seed loader's job.
    # ------------------------------------------------------------------
    op.execute(
        "INSERT INTO concept_kind_tags (id, concept_id, kind, created_at, updated_at) "
        "SELECT gen_random_uuid(), id, kind, now(), now() FROM concepts"
    )
    op.execute("UPDATE concepts SET primary_kind = kind")

    # ------------------------------------------------------------------
    # 4. Swap the unique index: (kind, slug, tenant) -> (slug, tenant).
    #    Drop the kind-dependent indexes created by 1a3dd1256035.
    # ------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS ix_concepts_kind_slug_tenant")
    op.execute("DROP INDEX IF EXISTS ix_concepts_kind_status")
    op.execute("DROP INDEX IF EXISTS ix_concepts_kind")
    op.execute(
        f"CREATE UNIQUE INDEX ix_concepts_slug_tenant ON concepts "
        f"(slug, COALESCE(tenant_id, '{_NULL_TENANT}'::uuid))"
    )
    op.execute(
        "CREATE INDEX ix_concepts_primary_kind_status ON concepts (primary_kind, status)"
    )

    # ------------------------------------------------------------------
    # 5. Drop the legacy kind column.
    # ------------------------------------------------------------------
    with op.batch_alter_table("concepts", schema=None) as batch_op:
        batch_op.drop_column("kind")


def downgrade() -> None:
    # Not supported — consolidation migration on a greenfield DB.
    pass
