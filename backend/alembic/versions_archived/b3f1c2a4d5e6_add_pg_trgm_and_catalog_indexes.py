"""add pg_trgm and catalog indexes

Enables the pg_trgm extension (trigram similarity + GIN trigram indexes) and
creates GIN indexes on the searchable text columns of the catalog tables so
that fuzzy/typo-tolerant lookup via the % operator and similarity() is indexed.

Covers: medication_catalog, biomarker_definitions, allergy_catalog,
clinical_event_types, clinical_event_categories.

Revision ID: b3f1c2a4d5e6
Revises: 9c4e7a2f0d11
Create Date: 2026-06-20 15:00:00.000000

NOTE: pg_trgm is a trusted extension on every managed Postgres (RDS, Cloud SQL,
Aurora, Supabase) and ships in the official postgres / timescale/timescaledb
images. On self-hosted clusters the migrating role needs SUPERUSER (or the
pg_trgm owner to grant CREATE). If the role lacks privilege, run
`CREATE EXTENSION pg_trgm;` manually as superuser, then re-run the migration.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b3f1c2a4d5e6'
down_revision: Union[str, Sequence[str], None] = '9c4e7a2f0d11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Extension is idempotent. Requires SUPERUSER on self-hosted; trusted on managed.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # medication_catalog: search by name (primary) + indications (secondary)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_medication_catalog_name_trgm "
        "ON medication_catalog USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_medication_catalog_indications_trgm "
        "ON medication_catalog USING gin (indications gin_trgm_ops)"
    )

    # biomarker_definitions: search by name + slug
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_biomarker_definitions_name_trgm "
        "ON biomarker_definitions USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_biomarker_definitions_slug_trgm "
        "ON biomarker_definitions USING gin (slug gin_trgm_ops)"
    )

    # allergy_catalog
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_allergy_catalog_name_trgm "
        "ON allergy_catalog USING gin (name gin_trgm_ops)"
    )

    # clinical_event_types
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clinical_event_types_name_trgm "
        "ON clinical_event_types USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clinical_event_types_slug_trgm "
        "ON clinical_event_types USING gin (slug gin_trgm_ops)"
    )

    # clinical_event_categories
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clinical_event_categories_name_trgm "
        "ON clinical_event_categories USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clinical_event_categories_slug_trgm "
        "ON clinical_event_categories USING gin (slug gin_trgm_ops)"
    )

    # NOTE: pg_trgm.similarity_threshold is set per-session by the search
    # service (SELECT set_limit(0.2)) rather than via ALTER DATABASE here,
    # so migration does not require a known DB name.


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_medication_catalog_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_medication_catalog_indications_trgm")
    op.execute("DROP INDEX IF EXISTS ix_biomarker_definitions_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_biomarker_definitions_slug_trgm")
    op.execute("DROP INDEX IF EXISTS ix_allergy_catalog_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_clinical_event_types_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_clinical_event_types_slug_trgm")
    op.execute("DROP INDEX IF EXISTS ix_clinical_event_categories_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_clinical_event_categories_slug_trgm")
    # Intentionally do NOT drop the extension: other code may depend on it and
    # dropping an extension requires SUPERUSER anyway.
