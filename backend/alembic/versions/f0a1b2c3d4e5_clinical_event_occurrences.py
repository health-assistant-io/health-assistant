"""clinical event occurrences

Revision ID: f0a1b2c3d4e5
Revises: e1f2a3b4c5d6
Create Date: 2026-07-07

Promotes the legacy untyped ``clinical_events.occurrences`` JSONB array into a
queryable first-class model: ``clinical_event_occurrences``. Each row is one
discrete episode within a health journey (e.g. a specific migraine with an
intensity and a body site).

The legacy ``clinical_events.occurrences`` JSONB column is RETAINED for one
cycle as read-back fallback (``ClinicalEvent.to_dict`` sources from the new
table when its ``occurrence_links`` relationship is loaded, else from JSONB).
Existing JSONB occurrence entries are backfilled into the new table so the new
model is the source of truth immediately.

See ``dev/plans/clinical-events-architecture-2026-07-07.md`` Phase 3a.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f0a1b2c3d4e5"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clinical_event_occurrences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clinical_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("severity", sa.String(50), nullable=True),
        sa.Column("intensity", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "anatomy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("anatomy_structures.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'"),
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
    )
    op.create_index(
        "ix_clinical_event_occurrences_event_id",
        "clinical_event_occurrences",
        ["event_id"],
    )
    op.create_index(
        "ix_clinical_event_occurrences_occurred_at",
        "clinical_event_occurrences",
        ["occurred_at"],
    )
    op.create_index(
        "ix_clinical_event_occurrences_anatomy_id",
        "clinical_event_occurrences",
        ["anatomy_id"],
    )

    # Backfill: expand each row's legacy ``occurrences`` JSONB array into rows.
    # Entries are free-form dicts; we pull the recognized keys (date, intensity,
    # notes, severity, title) and stash anything else under ``metadata``. The
    # ``date`` key (legacy) becomes ``occurred_at``; rows without a date are
    # skipped (occurred_at is NOT NULL). ``gen_random_uuid`` assigns ids.
    op.execute(
        """
        INSERT INTO clinical_event_occurrences
            (id, event_id, occurred_at, title, severity, intensity, notes, metadata)
        SELECT
            gen_random_uuid(),
            ce.id,
            (elem->>'date')::timestamptz,
            elem->>'title',
            elem->>'severity',
            CASE
                WHEN elem ? 'intensity'
                     AND (elem->>'intensity') ~ '^[0-9]+$'
                THEN (elem->>'intensity')::int
                ELSE NULL
            END,
            elem->>'notes',
            COALESCE(
                (elem - 'date' - 'title' - 'severity' - 'intensity' - 'notes')
               ::jsonb,
                '{}'::jsonb
            )
        FROM clinical_events ce,
             jsonb_array_elements(ce.occurrences) AS elem
        WHERE jsonb_typeof(ce.occurrences) = 'array'
          AND elem ? 'date'
          AND (elem->>'date') IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_clinical_event_occurrences_anatomy_id",
        table_name="clinical_event_occurrences",
    )
    op.drop_index(
        "ix_clinical_event_occurrences_occurred_at",
        table_name="clinical_event_occurrences",
    )
    op.drop_index(
        "ix_clinical_event_occurrences_event_id",
        table_name="clinical_event_occurrences",
    )
    op.drop_table("clinical_event_occurrences")
