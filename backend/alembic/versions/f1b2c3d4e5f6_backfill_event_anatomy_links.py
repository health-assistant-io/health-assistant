"""backfill event anatomy links

Revision ID: f1b2c3d4e5f6
Revises: f0a1b2c3d4e5
Create Date: 2026-07-07

Promotes the ad-hoc ``clinical_events.event_metadata->>'body_part_id'`` JSONB
value into a proper ``event_anatomy_links`` row (relation_type='primary_site').
The ``EventAnatomyLink`` table already existed (migration ``4f61f50eb0be``) but
was unwired; Phase 3b makes it the structured anatomy path.

Only rows whose ``event_metadata`` has a non-null ``body_part_id`` referencing
an existing ``anatomy_structures.id`` are migrated. The (event_id, anatomy_id)
pair is unique, so the INSERT is guarded by ``ON CONFLICT DO NOTHING``.

See ``dev/plans/clinical-events-architecture-2026-07-07.md`` Phase 3b.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "f1b2c3d4e5f6"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO event_anatomy_links (id, event_id, anatomy_id, relation_type)
        SELECT
            gen_random_uuid(),
            ce.id,
            (ce.event_metadata->>'body_part_id')::uuid,
            'primary_site'
        FROM clinical_events ce
        WHERE jsonb_typeof(ce.event_metadata) = 'object'
          AND ce.event_metadata ? 'body_part_id'
          AND ce.event_metadata->>'body_part_id' IS NOT NULL
          AND (ce.event_metadata->>'body_part_id')::uuid IN (SELECT id FROM anatomy_structures)
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    # Only remove the backfilled primary_site links; keep any manually-created ones.
    op.execute(
        "DELETE FROM event_anatomy_links WHERE relation_type = 'primary_site'"
    )
