"""add concepts and concept_edges tables

Revision ID: 1a3dd1256035
Revises: f19a2b3c4d5e
Create Date: 2026-07-05

Introduces the unified taxonomy / knowledge-graph foundation:
- ``concepts`` — controlled-vocabulary nodes (specialties, categories, panels,
  diseases, …) replacing the scattered ``examination_categories``,
  ``clinical_event_categories``, ``biomarker_groups`` tables and the free-text
  ``category`` columns on biomarkers/anatomy/doctors/documents.
- ``concept_edges`` — typed polymorphic edges between Concepts and/or domain
  entities (MEMBER_OF, EXAMINES, PERFORMS, TREATS, …).

Design notes:
- ``coding_system`` is a free String, not an enum — terminology systems are
  open-ended (LOINC, SNOMED, ATC, ICD-10, CVX, MeSH, …) and adding new ones
  must not require a migration. Matches FHIR ``system`` URI semantics.
- The unique partial indexes use ``COALESCE(tenant_id, sentinel)`` so that
  multiple GLOBAL rows (tenant_id IS NULL) collide correctly (Postgres treats
  NULLs as distinct under UNIQUE, which would silently allow duplicate global
  slugs).
- Trigram GIN indexes on name+slug power the ``search_concepts`` ranked
  typeahead; a JSONB GIN on aliases enables containment matching.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "1a3dd1256035"
down_revision: Union[str, Sequence[str], None] = "f19a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NULL_TENANT = "00000000-0000-0000-0000-000000000000"

_ENUMS = [
    (
        "conceptkind",
        "('specialty', 'examination_category', 'event_category', "
        "'biomarker_class', 'biomarker_panel', 'anatomy_class', "
        "'vaccine_class', 'medication_class', 'document_category', "
        "'disease', 'body_system', 'procedure', 'lifestyle', 'factor', "
        "'symptom', 'organ')",
    ),
    ("conceptstatus", "('draft', 'active', 'retired')"),
    (
        "conceptprovenance",
        "('seed', 'integration', 'ai', 'manual')",
    ),
    ("edgeapprovalstatus", "('approved', 'proposed', 'rejected')"),
    (
        "edgeendpointtype",
        "('concept', 'biomarker', 'medication', 'clinical_event_type', "
        "'allergy', 'immunization', 'observation', 'doctor', 'examination', "
        "'anatomy', 'document')",
    ),
    (
        "conceptrelationtype",
        "('MEMBER_OF', 'HAS_SPECIALTY', 'CLASSIFIED_AS', 'EXAMINES', "
        "'PERFORMS', 'ORDERS', 'LOCATED_IN', 'PART_OF', 'TREATS', "
        "'INDICATES', 'PREVENTS', 'CONTRAINDICATES', 'CORRELATES_WITH', "
        "'CAUSED_BY', 'MONITORS', 'RISK_OF', 'SCREENS_FOR')",
    ),
]


def _create_enum(name: str, values: str) -> None:
    op.execute(
        f"DO $$ BEGIN CREATE TYPE {name} AS ENUM {values}; "
        f"EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )


def _drop_enum(name: str) -> None:
    op.execute(f"DROP TYPE IF EXISTS {name} CASCADE")


def upgrade() -> None:
    for name, values in _ENUMS:
        _create_enum(name, values)

    op.create_table(
        "concepts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
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
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("coding_system", sa.String(length=50), nullable=True),
        sa.Column("code", sa.String(length=100), nullable=True),
        sa.Column(
            "aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("icon", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("color", sa.String(length=50), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "draft", "active", "retired", name="conceptstatus", create_type=False
            ),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("meta_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
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
            ["parent_id"], ["concepts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE"
        ),
    )

    with op.batch_alter_table("concepts", schema=None) as batch_op:
        batch_op.create_index("ix_concepts_slug", ["slug"], unique=False)
        batch_op.create_index("ix_concepts_kind", ["kind"], unique=False)
        batch_op.create_index("ix_concepts_parent_id", ["parent_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_concepts_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_concepts_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_concepts_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index("ix_concepts_deleted_at", ["deleted_at"], unique=False)

    op.execute(
        f"CREATE UNIQUE INDEX ix_concepts_kind_slug_tenant ON concepts "
        f"(kind, slug, COALESCE(tenant_id, '{_NULL_TENANT}'::uuid))"
    )
    op.execute(
        "CREATE INDEX ix_concepts_kind_status ON concepts (kind, status)"
    )
    op.execute(
        "CREATE INDEX ix_concepts_trgm ON concepts USING GIN "
        "((name || ' ' || slug) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_concepts_aliases_gin ON concepts USING GIN (aliases)"
    )

    op.create_table(
        "concept_edges",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=True),
        sa.Column(
            "src_type",
            postgresql.ENUM(
                "concept",
                "biomarker",
                "medication",
                "clinical_event_type",
                "allergy",
                "immunization",
                "observation",
                "doctor",
                "examination",
                "anatomy",
                "document",
                name="edgeendpointtype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("src_id", sa.UUID(), nullable=False),
        sa.Column(
            "dst_type",
            postgresql.ENUM(
                "concept",
                "biomarker",
                "medication",
                "clinical_event_type",
                "allergy",
                "immunization",
                "observation",
                "doctor",
                "examination",
                "anatomy",
                "document",
                name="edgeendpointtype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("dst_id", sa.UUID(), nullable=False),
        sa.Column(
            "relation",
            postgresql.ENUM(
                "MEMBER_OF",
                "HAS_SPECIALTY",
                "CLASSIFIED_AS",
                "EXAMINES",
                "PERFORMS",
                "ORDERS",
                "LOCATED_IN",
                "PART_OF",
                "TREATS",
                "INDICATES",
                "PREVENTS",
                "CONTRAINDICATES",
                "CORRELATES_WITH",
                "CAUSED_BY",
                "MONITORS",
                "RISK_OF",
                "SCREENS_FOR",
                name="conceptrelationtype",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("properties", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "source",
            postgresql.ENUM(
                "seed",
                "integration",
                "ai",
                "manual",
                name="conceptprovenance",
                create_type=False,
            ),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "approved",
                "proposed",
                "rejected",
                name="edgeapprovalstatus",
                create_type=False,
            ),
            server_default=sa.text("'approved'"),
            nullable=False,
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
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
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE"
        ),
    )

    with op.batch_alter_table("concept_edges", schema=None) as batch_op:
        batch_op.create_index("ix_concept_edges_src_type", ["src_type"], unique=False)
        batch_op.create_index("ix_concept_edges_src_id", ["src_id"], unique=False)
        batch_op.create_index("ix_concept_edges_dst_type", ["dst_type"], unique=False)
        batch_op.create_index("ix_concept_edges_dst_id", ["dst_id"], unique=False)
        batch_op.create_index(
            "ix_concept_edges_relation", ["relation"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_concept_edges_created_at"),
            ["created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_concept_edges_updated_at"),
            ["updated_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_concept_edges_tenant_id"),
            ["tenant_id"],
            unique=False,
        )

    op.execute(
        "CREATE INDEX ix_concept_edges_src ON concept_edges (src_type, src_id)"
    )
    op.execute(
        "CREATE INDEX ix_concept_edges_dst ON concept_edges (dst_type, dst_id)"
    )
    op.execute(
        "CREATE INDEX ix_concept_edges_relation_status "
        "ON concept_edges (relation, status)"
    )
    op.execute(
        f"CREATE UNIQUE INDEX ix_concept_edges_unique ON concept_edges "
        f"(src_type, src_id, dst_type, dst_id, relation, "
        f"COALESCE(tenant_id, '{_NULL_TENANT}'::uuid))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_concept_edges_unique")
    op.execute("DROP INDEX IF EXISTS ix_concept_edges_relation_status")
    op.execute("DROP INDEX IF EXISTS ix_concept_edges_dst")
    op.execute("DROP INDEX IF EXISTS ix_concept_edges_src")
    op.drop_table("concept_edges")

    op.execute("DROP INDEX IF EXISTS ix_concepts_aliases_gin")
    op.execute("DROP INDEX IF EXISTS ix_concepts_trgm")
    op.execute("DROP INDEX IF EXISTS ix_concepts_kind_status")
    op.execute("DROP INDEX IF EXISTS ix_concepts_kind_slug_tenant")
    op.drop_table("concepts")

    for name, _ in reversed(_ENUMS):
        _drop_enum(name)
