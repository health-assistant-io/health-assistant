"""Instance → catalog FKs: link fhir_medications / fhir_allergy_intolerances (ADR 0055 D6).

Program Phase 2: the JSONB ``code`` snapshot
(``{"text": ..., "catalog_id": ...}``) has carried the catalog reference as an
unvalidated string; this revision adds the real link:

* ``fhir_medications.medication_catalog_id UUID NULL`` — FK to
  ``medication_catalog.id`` with ``ON DELETE SET NULL``, plus its index.
* ``fhir_allergy_intolerances.allergy_catalog_id UUID NULL`` — FK to
  ``allergy_catalog.id`` with ``ON DELETE SET NULL``, plus its index.
* Backfill from ``code->>'catalog_id'`` where the target row still exists:
  malformed UUIDs are guarded by a regex cast (they must not abort the
  migration) and dangling ids stay NULL. The JSONB snapshot is never
  modified — it remains the point-in-time display snapshot (ADR 0055 D6).
* ``patient_immunizations.vaccine_catalog_id`` already follows this exact
  pattern (FK to ``vaccine_catalog.id`` with ``ON DELETE SET NULL`` plus the
  JSONB ``vaccine_code`` snapshot) — deliberately not duplicated here; it is
  the template the two new columns mirror.

Downgrade drops the indexes, the FKs and the columns; the backfill is not
reversed (the JSONB snapshots carry the same information throughout, so the
previous shape loses nothing).

Revision ID: 0011_instance_catalog_fks
Revises: 0010_catalog_identity
Create Date: 2026-11-09

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_instance_catalog_fks"
down_revision: str | Sequence[str] | None = "0010_catalog_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

# (instance table, new FK column, catalog table)
_LINKS = (
    ("fhir_medications", "medication_catalog_id", "medication_catalog"),
    ("fhir_allergy_intolerances", "allergy_catalog_id", "allergy_catalog"),
)


def _backfill_statement(table: str, column: str, catalog: str) -> str:
    """Link rows whose JSONB snapshot points at a catalog row that still exists.

    The regex guard keeps malformed ids from aborting the cast; the EXISTS
    guard leaves dangling ids NULL; the JSONB column is never written.
    """
    return (
        f"UPDATE {table} AS instance "
        f"SET {column} = (instance.code->>'catalog_id')::uuid "
        f"WHERE instance.code IS NOT NULL "
        f"AND instance.code ? 'catalog_id' "
        f"AND instance.code->>'catalog_id' ~ '{_UUID_RE}' "
        f"AND EXISTS (SELECT 1 FROM {catalog} catalog "
        f"WHERE catalog.id = (instance.code->>'catalog_id')::uuid)"
    )


def upgrade() -> None:
    """Add the nullable catalog FKs (+ indexes) and backfill from the JSONB."""
    for table, column, catalog in _LINKS:
        op.add_column(table, sa.Column(column, sa.UUID(), nullable=True))
        op.execute(_backfill_statement(table, column, catalog))
        op.create_foreign_key(
            f"fk_{table}_{column}",
            table,
            catalog,
            [column],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_{table}_{column}", table, [column], unique=False)


def downgrade() -> None:
    """Drop the indexes, FKs and columns (the JSONB snapshot keeps the reference)."""
    for table, column, _catalog in _LINKS:
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_constraint(f"fk_{table}_{column}", table, type_="foreignkey")
        op.drop_column(table, column)
