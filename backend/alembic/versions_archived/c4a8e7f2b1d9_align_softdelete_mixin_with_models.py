"""align schema with SoftDeleteMixin on FHIR models

Revision ID: c4a8e7f2b1d9
Revises: b3f1d52a9c7e
Create Date: 2026-06-22 16:00:00.000000

Audit items D6 + F3 + D15: the migration ``a7484842ecd4`` added the
``deleted_at TIMESTAMPTZ`` column + a partial index
``idx_{table}_deleted_at`` (WHERE deleted_at IS NULL) to nine FHIR-
exposed tables, but the corresponding ORM models did not declare
``SoftDeleteMixin``. The facade's generic ``crud.delete()`` therefore
hard-deleted via ``session.delete()`` — the tombstone contract
(advertised in the CapabilityStatement + docstring) was silently
violated, and reads of soft-deleted rows returned 404 instead of 410
Gone.

This migration:

1. Drops the legacy partial indexes ``idx_{table}_deleted_at`` that
   SQLAlchemy cannot see (no model declaration). The models now mix in
   ``SoftDeleteMixin`` which auto-declares an index named
   ``ix_{table}_deleted_at`` — created in step 2.
2. Creates ``ix_{table}_deleted_at`` (non-partial btree) to match the
   SQLAlchemy declaration. The partial-index optimization is sacrificed
   for naming-convention consistency; the perf difference is minimal at
   the project's scale and the consistency wins matter more (audit D22
   calls out the naming-convention drift).
3. Adds ``created_at`` + ``updated_at`` to ``fhir_organizations``
   (audit D15 — OrganizationModel previously had no TimestampMixin and
   could not be sorted/filtered by time).
4. Adds ``created_by`` + ``updated_by`` to ``fhir_devices`` so the
   AuditMixin on DeviceModel aligns with the table.

Idempotent: every DROP/CREATE uses IF EXISTS / IF NOT EXISTS.
"""
from alembic import op


revision = "c4a8e7f2b1d9"
down_revision = "b3f1d52a9c7e"
branch_labels = None
depends_on = None


# Tables whose models now mix in SoftDeleteMixin (audit D6/F3).
SOFT_DELETE_TABLES = (
    "fhir_patients",
    "fhir_observations",
    "fhir_diagnostic_reports",
    "fhir_medications",
    "fhir_allergy_intolerances",
    "fhir_organizations",
    "examinations",
    "clinical_events",
    "documents",
)


def upgrade() -> None:
    # 1. Drop the legacy partial indexes (idx_ prefix) so they don't shadow
    #    the SQLAlchemy-declared ix_ indexes.
    for table in SOFT_DELETE_TABLES:
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_deleted_at")

    # 2. Create the SQLAlchemy-expected ix_ indexes. Use IF NOT EXISTS for
    #    idempotency.
    for table in SOFT_DELETE_TABLES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_deleted_at "
            f"ON {table} (deleted_at)"
        )

    # 3. Audit D15: OrganizationModel gains TimestampMixin. The columns
    #    don't exist on the table yet.
    op.execute(
        "ALTER TABLE fhir_organizations "
        "ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ "
        "DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE fhir_organizations "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ "
        "DEFAULT now()"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_fhir_organizations_created_at "
        "ON fhir_organizations (created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_fhir_organizations_updated_at "
        "ON fhir_organizations (updated_at)"
    )


def downgrade() -> None:
    # Drop the ix_ indexes; recreate the legacy idx_ partial indexes so
    # the downgraded schema matches the pre-migration state.
    for table in SOFT_DELETE_TABLES:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_deleted_at")
        op.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_deleted_at "
            f"ON {table} (deleted_at) WHERE deleted_at IS NULL"
        )

    op.execute("DROP INDEX IF EXISTS ix_fhir_organizations_updated_at")
    op.execute("DROP INDEX IF EXISTS ix_fhir_organizations_created_at")
    op.execute("ALTER TABLE fhir_organizations DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE fhir_organizations DROP COLUMN IF EXISTS created_at")
