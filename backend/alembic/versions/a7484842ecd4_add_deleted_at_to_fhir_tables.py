"""add deleted_at to FHIR tables (soft-delete / tombstone support)

Revision ID: a7484842ecd4
Revises: f1a2b3c4d5e6
Create Date: 2026-06-21 14:00:00.000000

Adds the ``deleted_at TIMESTAMPTZ`` column to every FHIR-exposed table so
the R4 facade can soft-delete resources and return ``410 Gone`` on reads
of deleted rows (audit item C5). Existing rows default to ``NULL`` (not
deleted). New resources gain the column via the SoftDeleteMixin and are
created in subsequent migrations.
"""
from alembic import op


revision = "a7484842ecd4"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


# Tables that already exist today and need deleted_at added for the R4 facade.
EXISTING_FHIR_TABLES = (
    "fhir_patients",
    "fhir_observations",
    "fhir_diagnostic_reports",
    "fhir_medications",
    "fhir_allergy_intolerances",
    "fhir_organizations",
    "examinations",            # Encounter analog (Phase 3.2 will FHIR-enable it)
    "clinical_events",         # Condition projection (Phase 3.1)
    "documents",               # DocumentReference projection (Phase 3.3)
)


def upgrade() -> None:
    for table in EXISTING_FHIR_TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL"
        )
        op.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_deleted_at "
            f"ON {table} (deleted_at) WHERE deleted_at IS NULL"
        )


def downgrade() -> None:
    for table in EXISTING_FHIR_TABLES:
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_deleted_at")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS deleted_at")
