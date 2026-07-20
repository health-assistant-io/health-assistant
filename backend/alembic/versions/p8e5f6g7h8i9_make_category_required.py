"""make clinical_event_types.category_concept_id NOT NULL

Phase 8e of the calendar-ongoing-events plan: tighten the wire format for
``category_concept_id``, mirroring the Phase 8a tightening on
``schedule_kind``. Every ``ClinicalEventType`` must belong to a category
so the frontend's category-first picker can surface every type.

Steps:
  1. Ensure the system "General" concept (slug ``general-event``) exists.
     The seed loader creates it on startup; this migration also does a
     defensive ``INSERT ... ON CONFLICT DO NOTHING`` so a fresh DB
     (migrated before the seed runs) still has a backfill target.
  2. Backfill any NULL ``category_concept_id`` rows to that concept's id.
  3. Drop the old ``SET NULL`` FK and replace it with a ``RESTRICT`` FK
     (you can't delete a category that types still reference).
  4. Add the NOT NULL constraint.

Reverses by dropping the NOT NULL constraint and restoring the SET NULL
FK. Backfilled rows keep their assigned General category.

Revision ID: p8e5f6g7h8i9
Revises: p8a1b2c3d4e5
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "p8e5f6g7h8i9"
down_revision = "p8a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Defensive: ensure the General concept exists so the backfill has a
    #    target even on a fresh DB that hasn't run the seed yet. ON CONFLICT
    #    keeps this idempotent — if the seed already inserted it, no-op.
    #    `aliases` is NOT NULL on concepts (default `[]`); we provide it
    #    explicitly because raw SQL bypasses the SQLAlchemy default.
    op.execute(
        """
        INSERT INTO concepts (id, slug, name, color, description, aliases, scope, status, primary_kind, version, display_order)
        SELECT
            gen_random_uuid(),
            'general-event',
            'General',
            '#64748b',
            'Catch-all category for clinical events that don''t fit a more specific specialty.',
            '[]'::jsonb,
            'system',
            'active',
            'event_category',
            1,
            0
        WHERE NOT EXISTS (
            SELECT 1 FROM concepts c
            JOIN concept_kind_tags t ON t.concept_id = c.id
            WHERE c.slug = 'general-event' AND t.kind = 'event_category'
        )
        """
    )
    # Tag it with the event_category kind if we just inserted it (the kind
    # tag is what concept_kind_tags / concepts_with_kind filters on).
    op.execute(
        """
        INSERT INTO concept_kind_tags (concept_id, kind)
        SELECT c.id, 'event_category'
        FROM concepts c
        WHERE c.slug = 'general-event'
          AND NOT EXISTS (
            SELECT 1 FROM concept_kind_tags t
            WHERE t.concept_id = c.id AND t.kind = 'event_category'
          )
        """
    )

    # 2. Backfill NULL rows to the General concept.
    op.execute(
        """
        UPDATE clinical_event_types
        SET category_concept_id = (
            SELECT c.id FROM concepts c
            JOIN concept_kind_tags t ON t.concept_id = c.id
            WHERE c.slug = 'general-event' AND t.kind = 'event_category'
            LIMIT 1
        )
        WHERE category_concept_id IS NULL
        """
    )

    # 3. Swap the FK constraint from SET NULL → RESTRICT. The original
    #    constraint name varies by Alembic history, so drop & recreate by
    #    pattern. RESTRICT matches the new NOT NULL semantics — deleting a
    #    category that types still reference is now blocked, not silently
    #    orphaning the types.
    op.drop_constraint(
        "clinical_event_types_category_concept_id_fkey",
        "clinical_event_types",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "clinical_event_types_category_concept_id_fkey",
        "clinical_event_types",
        "concepts",
        ["category_concept_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 4. Add NOT NULL.
    op.alter_column(
        "clinical_event_types",
        "category_concept_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )


def downgrade() -> None:
    # Revert to nullable + SET NULL FK. Existing values are kept.
    op.alter_column(
        "clinical_event_types",
        "category_concept_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.drop_constraint(
        "clinical_event_types_category_concept_id_fkey",
        "clinical_event_types",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "clinical_event_types_category_concept_id_fkey",
        "clinical_event_types",
        "concepts",
        ["category_concept_id"],
        ["id"],
        ondelete="SET NULL",
    )
