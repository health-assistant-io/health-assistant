# ruff: noqa: E501 -- long immutable DDL strings; reflow when touched
"""catalog cleanup: descriptor-derived search indexes, one ownership flag

Revision ID: 0024_catalog_cleanup
Revises: 0023_ai_cost_provenance
Create Date: 2026-11-11

Catalog search indexes are now generated from each catalog's declared search
surface (:mod:`app.core.catalog_search_index`) instead of hand-written SQL per
table, so a searched column and its index cannot drift. ``SURFACES_AT_0024``
is the frozen snapshot the live registry must still equal
(``tests/test_catalog_search_index.py``); a new searched column needs a new
revision, never an edit here.

Also drops ``anatomy_structures.is_custom``: ownership has exactly one
representation, ``scope`` + ``tenant_id`` (ADR 0055 D8), and the boolean
duplicated it.

Downgrade restores the previous shape: the 0001-era index set (including the
retired concept name||slug trigram index) is recreated verbatim and
``is_custom`` returns as a NOT NULL boolean defaulting to false.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.core.catalog_search_index import CatalogSearchSurface

revision = "0024_catalog_cleanup"
down_revision = "0023_ai_cost_provenance"
branch_labels = None
depends_on = None


SURFACES_AT_0024: tuple[CatalogSearchSurface, ...] = (
    CatalogSearchSurface(
        catalog_type="biomarker",
        table="biomarker_definitions",
        fts_columns=("name", "slug", "description", "info", "code", "aliases"),
        trigram_columns=("slug",),
        json_columns=("aliases",),
    ),
    CatalogSearchSurface(
        catalog_type="medication",
        table="medication_catalog",
        fts_columns=("name", "description", "indications", "side_effects", "contraindications"),
        json_columns=("side_effects",),
    ),
    CatalogSearchSurface(
        catalog_type="allergy",
        table="allergy_catalog",
        fts_columns=("name", "description"),
    ),
    CatalogSearchSurface(
        catalog_type="anatomy",
        table="anatomy_structures",
        fts_columns=("name", "slug", "description", "standard_code"),
        trigram_columns=("slug",),
    ),
    CatalogSearchSurface(
        catalog_type="vaccine",
        table="vaccine_catalog",
        fts_columns=("name", "description", "code"),
    ),
    CatalogSearchSurface(
        catalog_type="concept",
        table="concepts",
        fts_columns=("name", "slug", "description", "code", "aliases"),
        trigram_columns=("slug",),
        json_columns=("aliases",),
    ),
    CatalogSearchSurface(
        catalog_type="clinical_event_type",
        table="clinical_event_types",
        fts_columns=("name", "slug", "description"),
        trigram_columns=("slug",),
    ),
)

# The 0001_baseline expression indexes this revision replaces. ``ix_concepts_trgm``
# (name || ' ' || slug) is retired: no query matches that shape.
_RETIRED_INDEXES = ("ix_concepts_trgm",)

_LEGACY_INDEX_DDL = (
    "CREATE INDEX ix_allergy_catalog_name_trgm ON allergy_catalog USING GIN (name gin_trgm_ops)",
    "CREATE INDEX ix_anatomy_structures_name_trgm ON anatomy_structures USING GIN (name gin_trgm_ops)",
    "CREATE INDEX ix_anatomy_structures_slug_trgm ON anatomy_structures USING GIN (slug gin_trgm_ops)",
    "CREATE INDEX ix_biomarker_definitions_name_trgm ON biomarker_definitions USING GIN (name gin_trgm_ops)",
    "CREATE INDEX ix_biomarker_definitions_slug_trgm ON biomarker_definitions USING GIN (slug gin_trgm_ops)",
    "CREATE INDEX ix_concepts_name_trgm ON concepts USING GIN (name gin_trgm_ops)",
    "CREATE INDEX ix_concepts_slug_trgm ON concepts USING GIN (slug gin_trgm_ops)",
    "CREATE INDEX ix_concepts_trgm ON concepts USING GIN (((name)::text || ' ' || (slug)::text) gin_trgm_ops)",
    "CREATE INDEX ix_medication_catalog_name_trgm ON medication_catalog USING GIN (name gin_trgm_ops)",
    "CREATE INDEX ix_vaccine_catalog_name_trgm ON vaccine_catalog USING GIN (name gin_trgm_ops)",
    "CREATE INDEX ix_allergy_catalog_fts ON allergy_catalog USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(description, '')))",
    "CREATE INDEX ix_anatomy_structures_fts ON anatomy_structures USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(slug, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(standard_code, '')))",
    "CREATE INDEX ix_biomarker_definitions_fts ON biomarker_definitions USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(slug, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(info, '') || ' ' || COALESCE(code, '') || ' ' || COALESCE(aliases::text, '')))",
    "CREATE INDEX ix_concepts_fts ON concepts USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(slug, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(code, '') || ' ' || COALESCE(aliases::text, '')))",
    "CREATE INDEX ix_medication_catalog_fts ON medication_catalog USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(indications, '') || ' ' || COALESCE(side_effects::text, '') || ' ' || COALESCE(contraindications, '')))",
    "CREATE INDEX ix_vaccine_catalog_fts ON vaccine_catalog USING GIN (to_tsvector('simple', COALESCE(name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(code, '')))",
)


def _all_index_names() -> tuple[str, ...]:
    names: list[str] = []
    for surface in SURFACES_AT_0024:
        names.extend(surface.index_names())
    return (*names, *_RETIRED_INDEXES)


def upgrade() -> None:
    """Upgrade schema."""
    for name in _all_index_names():
        op.execute(f"DROP INDEX IF EXISTS {name}")
    for surface in SURFACES_AT_0024:
        for _name, statement in surface.index_ddl():
            op.execute(statement)
    with op.batch_alter_table("anatomy_structures", schema=None) as batch_op:
        batch_op.drop_column("is_custom")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("anatomy_structures", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    for name in _all_index_names():
        op.execute(f"DROP INDEX IF EXISTS {name}")
    for statement in _LEGACY_INDEX_DDL:
        op.execute(statement)
