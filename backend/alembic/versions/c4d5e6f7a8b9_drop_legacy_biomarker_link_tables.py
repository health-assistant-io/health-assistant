"""drop legacy biomarker link tables (migrated to concept_edges)

Revision ID: c4d5e6f7a8b9
Revises: a1b2c3d4e5f7
Create Date: 2026-07-08

Phase 3 of the unified-catalog architecture (``dev/plans/unified-catalog-
architecture-2026-07-08.md`` §3.5). Drops the two legacy biomarker link tables
now that their data model lives in the polymorphic ``concept_edges`` graph:

- ``biomarker_relationships`` (biomarker↔biomarker) — was dead code (no
  service/endpoint/test readers); the equivalent relationship is a
  ``CORRELATES_WITH`` ``concept_edges`` row.
- ``biomarker_event_correlations`` (biomarker↔clinical_event_type) — its live
  readers (``clinical_event_service`` + the ``/clinical-events/types/{id}/
  biomarkers`` endpoint) are rewritten to query ``concept_edges`` (biomarker
  ``MONITORS`` clinical_event_type, with ``correlation_type``/``description`` on
  the edge's ``properties`` JSONB).

Greenfield (no users) → no data backfill: the seed loader re-creates edges from
JSON. Downgrade recreates both tables empty (the ORM models are gone, so the
rows would not be populated on downgrade — kept only to preserve schema history
for a clean round-trip).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_biomarker_event_correlations_event_type_id",
        table_name="biomarker_event_correlations",
        if_exists=True,
    )
    op.drop_index(
        "ix_biomarker_event_correlations_biomarker_id",
        table_name="biomarker_event_correlations",
        if_exists=True,
    )
    op.drop_table("biomarker_event_correlations", if_exists=True)

    op.drop_index(
        "ix_biomarker_relationships_target_biomarker_id",
        table_name="biomarker_relationships",
        if_exists=True,
    )
    op.drop_index(
        "ix_biomarker_relationships_source_biomarker_id",
        table_name="biomarker_relationships",
        if_exists=True,
    )
    op.drop_table("biomarker_relationships", if_exists=True)


def downgrade() -> None:
    # Recreate empty so `alembic downgrade` round-trips cleanly.
    op.create_table(
        "biomarker_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_biomarker_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("biomarker_definitions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_biomarker_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("biomarker_definitions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("relation_type", sa.String(100), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "biomarker_event_correlations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "biomarker_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("biomarker_definitions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "event_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clinical_event_types.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("correlation_type", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
