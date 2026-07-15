"""Observation/DiagnosticReport patient_id FK (audit B3)

Revision ID: n5e6f7a8b9c0
Revises: m4d5e6f7a8b9
Create Date: 2026-07-15

The patient was encoded only inside the FHIR ``subject`` JSONB
(``{"reference":"Patient/<uuid>"}``) on ``fhir_observations`` and
``fhir_diagnostic_reports`` — so there was no referential integrity, no
``ON DELETE CASCADE``, and every read had to parse JSONB. ``Medication`` got a
real ``patient_id`` FK long ago; this applies the same pattern to the two
remaining patient-owned clinical tables.

Adds a maintained ``patient_id UUID`` column (FK → ``fhir_patients.id``
``ON DELETE CASCADE``, indexed) to both tables. Existing rows are backfilled
from the ``subject`` reference (only where the referenced patient actually
exists — dangling ``Patient/unknown`` / deleted-patient refs stay NULL, which
the nullable FK permits). All application write paths were updated in the same
change to keep ``patient_id`` in sync with ``subject`` (``create_observation``,
``create_diagnostic_report``, the OCR pipeline, the import upserters, the
telemetry backfill worker, and the integration sync sites). ``subject`` remains
the FHIR serialization projection; ``patient_id`` is the relational source of
truth for joins/cascade/scoping.

Bonus: the FHIR R4 facade ``?patient=`` search param now actually filters
Observation/DiagnosticReport (``facade/crud._build_resource_filter`` keys on
``hasattr(model, "patient_id")`` — previously a silent no-op for these two).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "n5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "m4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_UUID_RX = (
    "([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    "[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

# (table, fk-constraint-name, index-name)
_TABLES = [
    ("fhir_observations", "fk_fhir_observations_patient_id", "ix_fhir_observations_patient_id"),
    (
        "fhir_diagnostic_reports",
        "fk_fhir_diagnostic_reports_patient_id",
        "ix_fhir_diagnostic_reports_patient_id",
    ),
]


def upgrade() -> None:
    derived = f"(substring(subject->>'reference' from '{_UUID_RX}'))::uuid"

    for table, fk_name, idx_name in _TABLES:
        # 1. Add the column (nullable, no FK yet).
        op.execute(f"ALTER TABLE {table} ADD COLUMN patient_id UUID")
        # 2. Backfill from subject — only where the referenced patient exists
        #    (dangling refs stay NULL so the FK can be added).
        op.execute(
            f"UPDATE {table} SET patient_id = {derived} "
            f"WHERE {derived} IS NOT NULL "
            f"AND EXISTS (SELECT 1 FROM fhir_patients p WHERE p.id = {derived})"
        )
        # 3. Foreign key with cascade (patient delete removes their data).
        op.create_foreign_key(
            fk_name,
            table,
            "fhir_patients",
            ["patient_id"],
            ["id"],
            ondelete="CASCADE",
        )
        # 4. Index for per-patient scoping.
        op.create_index(idx_name, table, ["patient_id"])


def downgrade() -> None:
    for table, fk_name, idx_name in reversed(_TABLES):
        op.drop_index(idx_name, table_name=table)
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.execute(f"ALTER TABLE {table} DROP COLUMN patient_id")
