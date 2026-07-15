"""Stratified biomarker reference ranges (audit B9 / F3)

Revision ID: r8b9c0d1e2f3
Revises: q7a8b9c0d1e2
Create Date: 2026-07-15

``BiomarkerDefinition`` carried a single global ``reference_range_min`` /
``reference_range_max``, which made ``relative_score`` and the status badge
unreliable for anyone outside the "default" demographic. This adds a child
table ``biomarker_reference_ranges`` (0..* ranges each scoped by sex / age
window / unit — mirroring FHIR ``Observation.referenceRange`` with
``appliesTo`` + ``age``). See ``app/services/reference_ranges.py`` for the
specificity-ranked resolver.

Backfill: every biomarker that had a legacy global range now also has one
"catch-all" stratified row (sex=NULL, age unbounded, unit=NULL) carrying the
same bounds. The resolver prefers more-specific rows but falls back to this
default, and ultimately to the legacy columns — so existing behaviour is
preserved exactly while new stratified rows can be added per demographic.

The legacy ``reference_range_min`` / ``reference_range_max`` columns are kept
on ``biomarker_definitions`` (display sites + resolver fallback).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "r8b9c0d1e2f3"
down_revision: Union[str, None] = "q7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Reference the existing ``gender`` enum type (created with fhir_patients);
    # do not attempt to recreate it.
    gender = postgresql.ENUM(name="gender", create_type=False)
    op.create_table(
        "biomarker_reference_ranges",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "biomarker_id",
            sa.UUID(),
            sa.ForeignKey("biomarker_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sex", gender, nullable=True),
        sa.Column("age_min", sa.Float(), nullable=True),
        sa.Column("age_max", sa.Float(), nullable=True),
        sa.Column(
            "unit_id",
            sa.UUID(),
            sa.ForeignKey("units.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("applies_to", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "low IS NULL OR high IS NULL OR low <= high",
            name="ck_biomarker_reference_ranges_low_le_high",
        ),
        sa.CheckConstraint(
            "age_min IS NULL OR age_max IS NULL OR age_min <= age_max",
            name="ck_biomarker_reference_ranges_age_window",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_biomarker_reference_ranges_biomarker_id"),
        "biomarker_reference_ranges",
        ["biomarker_id"],
        unique=False,
    )

    # Backfill: one catch-all row per biomarker that defined a legacy global
    # range. Specific stratified rows (added later by clinicians/AI) will
    # outrank this default via the resolver's specificity scoring.
    op.execute(
        """
        INSERT INTO biomarker_reference_ranges
            (biomarker_id, low, high, created_at, updated_at)
        SELECT id, reference_range_min, reference_range_max, now(), now()
        FROM biomarker_definitions
        WHERE reference_range_min IS NOT NULL OR reference_range_max IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_biomarker_reference_ranges_biomarker_id"),
        table_name="biomarker_reference_ranges",
    )
    op.drop_table("biomarker_reference_ranges")
