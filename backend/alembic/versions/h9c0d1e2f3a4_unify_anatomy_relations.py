"""unify anatomy_relations into concept_edges

Revision ID: h9c0d1e2f3a4
Revises: g8b9c0d1e2f3
Create Date: 2026-07-10

Migrates the legacy ``anatomy_relations`` table (a separate anatomy-only
hierarchy graph with ``AnatomyRelationType`` values) into the unified
``concept_edges`` polymorphic graph. Every anatomy→anatomy edge becomes a
``ConceptEdge`` row with ``src_type='anatomy', dst_type='anatomy'``.

The 6 ``AnatomyRelationType`` values missing from ``ConceptRelationType``
(``BRANCH_OF``, ``DRAINS_INTO``, ``ARTICULATES_WITH``, ``INNERVATED_BY``,
``SUPPLIED_BY``, ``CONTINUOUS_WITH``) are added to the ``conceptrelationtype``
PG enum first. ``PART_OF`` already exists.

After the data copy, the ``anatomy_relations`` table and the
``anatomyrelationtype`` PG enum are dropped — the unified ``concept_edges``
table is the single graph.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "h9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "g8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_RELATION_VALUES = (
    "BRANCH_OF",
    "DRAINS_INTO",
    "ARTICULATES_WITH",
    "INNERVATED_BY",
    "SUPPLIED_BY",
    "CONTINUOUS_WITH",
)


def upgrade() -> None:
    # 1. Add the 6 missing anatomy relation types to the conceptrelationtype
    #    PG enum. PART_OF already exists. ALTER TYPE ... ADD VALUE must run
    #    outside a transaction block in Postgres, so we commit first.
    for value in _NEW_RELATION_VALUES:
        op.execute(
            f"ALTER TYPE conceptrelationtype ADD VALUE IF NOT EXISTS '{value}'"
        )

    # 2. Copy all anatomy_relations rows into concept_edges as
    #    src_type='anatomy', dst_type='anatomy' edges.
    op.execute(
        """
        INSERT INTO concept_edges (id, src_type, src_id, dst_type, dst_id,
                                   relation, status, source, tenant_id,
                                   created_at, updated_at)
        SELECT gen_random_uuid(),
               'anatomy',
               source_id,
               'anatomy',
               target_id,
               relation_type::text::conceptrelationtype,
               'approved',
               'seed',
               NULL,
               COALESCE(created_at, now()),
               COALESCE(updated_at, now())
        FROM anatomy_relations
        ON CONFLICT DO NOTHING
        """
    )

    # 3. Drop the anatomy_relations table + indexes (idempotent — the table
    #    may already be gone from a prior partial run).
    op.execute("DROP INDEX IF EXISTS idx_anatomy_relation_target")
    op.execute("DROP INDEX IF EXISTS idx_anatomy_relation_source")
    op.execute("DROP INDEX IF EXISTS idx_anatomy_relation_unique")
    op.execute("DROP TABLE IF EXISTS anatomy_relations")

    # 4. Drop the anatomyrelationtype PG enum.
    op.execute("DROP TYPE IF EXISTS anatomyrelationtype")


def downgrade() -> None:
    # Recreate the table + enum (data is lost — the downgrade is structural
    # only; re-run the seed to repopulate).
    op.execute(
        "CREATE TYPE anatomyrelationtype AS ENUM "
        "('PART_OF', 'BRANCH_OF', 'DRAINS_INTO', 'ARTICULATES_WITH', "
        "'INNERVATED_BY', 'SUPPLIED_BY', 'CONTINUOUS_WITH')"
    )
    op.create_table(
        "anatomy_relations",
        op.Column("id", op.UUID(as_uuid=True), primary_key=True),
        op.Column("source_id", op.UUID(as_uuid=True), nullable=False),
        op.Column("target_id", op.UUID(as_uuid=True), nullable=False),
        op.Column(
            "relation_type",
            op.Enum(
                "PART_OF",
                "BRANCH_OF",
                "DRAINS_INTO",
                "ARTICULATES_WITH",
                "INNERVATED_BY",
                "SUPPLIED_BY",
                "CONTINUOUS_WITH",
                name="anatomyrelationtype",
            ),
            nullable=False,
        ),
        op.Column("created_at", op.DateTime(timezone=True)),
        op.Column("updated_at", op.DateTime(timezone=True)),
        op.ForeignKeyConstraint(
            ["source_id"], ["anatomy_structures.id"], ondelete="CASCADE"
        ),
        op.ForeignKeyConstraint(
            ["target_id"], ["anatomy_structures.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "idx_anatomy_relation_unique",
        "anatomy_relations",
        ["source_id", "target_id", "relation_type"],
        unique=True,
    )
    op.create_index(
        "idx_anatomy_relation_source", "anatomy_relations", ["source_id"]
    )
    op.create_index(
        "idx_anatomy_relation_target", "anatomy_relations", ["target_id"]
    )
