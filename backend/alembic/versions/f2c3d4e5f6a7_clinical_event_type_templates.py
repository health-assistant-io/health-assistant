"""clinical event type templates

Revision ID: f2c3d4e5f6a7
Revises: f1b2c3d4e5f6
Create Date: 2026-07-07

Promotes ``ClinicalEventType`` from a passive form-schema holder into a
journey *template* that drives behavior. Adds four nullable JSONB columns:

- ``severity_scale`` — e.g. ``{"type":"numeric","min":1,"max":10}`` or
  ``{"type":"ordinal","values":["mild","moderate","severe"]}``; drives occurrence
  validation + UI.
- ``phases`` — ``[{"name":"Trimester 1","start_offset_days":0,"end_offset_days":90}, ...]``;
  the engine computes the current phase from ``onset_date``.
- ``milestones`` — ``[{"name":"EDD","date_field":"edd","alert_before_days":14}]``;
  the engine surfaces upcoming milestones.
- ``default_duration_days`` — when set, the engine flags an ACTIVE journey as
  overdue past this window.

All nullable + optional, so existing types and rows are unaffected. See
``dev/plans/clinical-events-architecture-2026-07-07.md`` Phase 4a.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f2c3d4e5f6a7"
down_revision = "f1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clinical_event_types",
        sa.Column(
            "severity_scale",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "clinical_event_types",
        sa.Column("phases", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "clinical_event_types",
        sa.Column(
            "milestones", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.add_column(
        "clinical_event_types",
        sa.Column("default_duration_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clinical_event_types", "default_duration_days")
    op.drop_column("clinical_event_types", "milestones")
    op.drop_column("clinical_event_types", "phases")
    op.drop_column("clinical_event_types", "severity_scale")
