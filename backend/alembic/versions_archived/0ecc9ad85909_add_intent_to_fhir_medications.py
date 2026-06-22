"""add intent column to fhir_medications (MedicationStatement vs MedicationRequest)

Revision ID: 0ecc9ad85909
Revises: a7484842ecd4
Create Date: 2026-06-21 15:00:00.000000

Adds the ``intent`` discriminator column to ``fhir_medications`` so the R4
facade can route each row to either ``/fhir/R4/MedicationStatement`` or
``/fhir/R4/MedicationRequest``. Audit items C11 + C12: one table serves
both FHIR resources.

Existing rows default to ``statement`` (the historical behavior — every
row was a MedicationStatement before this migration).
"""
from alembic import op


revision = "0ecc9ad85909"
down_revision = "a7484842ecd4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the intent column with a server default of 'statement' so existing
    # rows backfill automatically. Then add an index for facade filtering
    # (`/fhir/R4/MedicationStatement` queries `WHERE intent = 'statement'`).
    op.execute(
        "ALTER TABLE fhir_medications "
        "ADD COLUMN IF NOT EXISTS intent VARCHAR(50) NOT NULL DEFAULT 'statement'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_fhir_medications_intent "
        "ON fhir_medications (intent)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_fhir_medications_intent")
    op.execute("ALTER TABLE fhir_medications DROP COLUMN IF EXISTS intent")
