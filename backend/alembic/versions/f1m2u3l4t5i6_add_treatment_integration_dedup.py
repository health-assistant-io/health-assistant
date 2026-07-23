"""add integration dedup columns + indexes to treatment tables

Plan: dev/plans/fhir-server-multi-resource-sync-2026-07-23.md (Phase 4,
Route A). Extends the multi-resource FHIR-server pull to
MedicationStatement/MedicationRequest, AllergyIntolerance, and Immunization.

These three tables currently lack the ``source_integration_id`` /
``external_id`` provenance + dedup columns that ``clinical_events``,
``examinations``, and ``documents`` already carry. Without them, every
re-sync of a remote FHIR server would duplicate the patient's
medications / allergies / immunizations. This migration mirrors the
dedup pattern shipped on those three tables
(``b1l2i3n4t5ev_add_clinical_event_integration_dedup.py``,
``e1x2a3m4i5n6_add_examination_integration_dedup.py``,
``d1o2c3u4m5e6_add_document_integration_dedup.py``):

  - ``source_integration_id``: nullable FK to ``user_integrations(id)``
    with ``ON DELETE SET NULL`` (deleting the integration preserves the
    clinical row; the dedup simply stops firing).
  - ``external_id``: free-text — the upstream's stable resource id
    (the remote FHIR server's ``Condition.id`` / ``MedicationStatement.id``
    / ``Immunization.id`` / ...).
  - Partial unique index on
    ``(tenant_id, patient_id, source_integration_id, external_id)``
    that fires only when all four columns are non-NULL. UI writes
    (both fields NULL) bypass it; integration-sourced rows are deduped
    at the DB layer (catches the race window between the service's
    SELECT and INSERT).

Two new non-unique indexes per table keep the engine's lookup-by-key
path fast.

Revision ID: f1m2u3l4t5i6
Revises: d1r2o3p4s5a6
Create Date: 2026-07-23
"""

from alembic import op


revision = "f1m2u3l4t5i6"
down_revision = "d1r2o3p4s5a6"
branch_labels = None
depends_on = None


# (table, src-index, ext-index, dedup-index)
_TABLES = [
    (
        "fhir_medications",
        "ix_fhir_medications_source_integration_id",
        "ix_fhir_medications_external_id",
        "uq_fhir_medications_integration_dedup",
    ),
    (
        "fhir_allergy_intolerances",
        "ix_fhir_allergy_intolerances_source_integration_id",
        "ix_fhir_allergy_intolerances_external_id",
        "uq_allergy_intolerance_integration_dedup",
    ),
    (
        "patient_immunizations",
        "ix_patient_immunizations_source_integration_id",
        "ix_patient_immunizations_external_id",
        "uq_patient_immunizations_integration_dedup",
    ),
]


def upgrade() -> None:
    for table, src_ix, ext_ix, dedup_ix in _TABLES:
        op.execute(
            f"""
            ALTER TABLE {table}
              ADD COLUMN IF NOT EXISTS source_integration_id UUID
                REFERENCES user_integrations(id) ON DELETE SET NULL
            """
        )
        op.execute(
            f"""
            ALTER TABLE {table}
              ADD COLUMN IF NOT EXISTS external_id VARCHAR(255)
            """
        )
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {src_ix} ON {table} (source_integration_id)"
        )
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {ext_ix} ON {table} (external_id)"
        )
        op.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {dedup_ix}
            ON {table} (tenant_id, patient_id, source_integration_id, external_id)
            WHERE source_integration_id IS NOT NULL
              AND external_id IS NOT NULL
            """
        )


def downgrade() -> None:
    for table, src_ix, ext_ix, dedup_ix in _TABLES:
        op.execute(f"DROP INDEX IF EXISTS {dedup_ix}")
        op.execute(f"DROP INDEX IF EXISTS {ext_ix}")
        op.execute(f"DROP INDEX IF EXISTS {src_ix}")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS external_id")
        op.execute(
            f"ALTER TABLE {table} DROP COLUMN IF EXISTS source_integration_id"
        )
