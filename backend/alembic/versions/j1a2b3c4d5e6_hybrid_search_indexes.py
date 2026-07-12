"""Hybrid search indexes (trigram + FTS tsvector) for all clinical catalogs

Revision ID: j1a2b3c4d5e6
Revises: i7b8c9d0e1f2
Create Date: 2026-07-12

Adds two complementary GIN indexes per catalog table so the unified
``search_catalogs`` dispatcher can run a single hybrid SQL per catalog:

1. Trigram index on the primary lexical surface (``name``/``slug``) —
   powers typo-tolerant matching via the ``%`` operator and
   ``similarity()`` ranking. The concepts table already had
   ``ix_concepts_trgm``; this migration adds parity for biomarkers,
   medications, allergies, anatomy, and vaccines.
2. FTS tsvector expression index — powers multi-word / "find by concept"
   queries (``indications``, ``description``, ``info``, ``aliases``)
   via ``websearch_to_tsquery`` + ``ts_rank_cd`` ranking. Tokenizer is
   ``'simple'`` (case-normalising, no stemming) so clinical / Latin
   terms and drug names aren't truncated. JSONB ``aliases`` is included
   via ``::text`` cast — the FTS parser strips the JSON punctuation,
   leaving the alias tokens.

Both indexes are expression indexes (no schema change), so the upgrade
is non-destructive. The downgrade drops only the indexes this migration
introduced; the pre-existing ``ix_concepts_trgm`` is left untouched.

See ``app/services/catalog_search_service.py`` for the hybrid query.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "j1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "h9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Per-catalog index spec.
#   trgm_cols   — list of columns that each get their own trigram GIN index
#                 (so per-column ``col % q`` predicates can use the index).
#                 Postgres can combine multiple trigram indexes via BitmapOr
#                 for an OR of per-column ``%`` predicates.
#   fts_cols    — (column, sql_expr) pairs concatenated into the tsvector.
#                 Use a literal SQL expression so NULLs are coalesced.
#                 For JSONB aliases we cast to text; the FTS parser strips
#                 the array punctuation.
#
# Each entry yields:
#   ix_<table>_<col>_trgm  — one GIN (<col> gin_trgm_ops) per trgm_cols entry
#   ix_<table>_fts         — one GIN (to_tsvector('simple', <fts_cols>))
_CATALOGS = [
    # biomarker_definitions
    {
        "table": "biomarker_definitions",
        "trgm_cols": ["name", "slug"],
        "fts_expr": (
            "coalesce(name, '') || ' ' || coalesce(slug, '') || ' ' || "
            "coalesce(description, '') || ' ' || coalesce(info, '') || ' ' || "
            "coalesce(code, '') || ' ' || coalesce(aliases::text, '')"
        ),
    },
    # medication_catalog. ``side_effects`` is JSONB (list of strings) — cast
    # to text; the FTS parser strips the array punctuation.
    {
        "table": "medication_catalog",
        "trgm_cols": ["name"],
        "fts_expr": (
            "coalesce(name, '') || ' ' || coalesce(description, '') || ' ' || "
            "coalesce(indications, '') || ' ' || "
            "coalesce(side_effects::text, '') || ' ' || "
            "coalesce(contraindications, '')"
        ),
    },
    # allergy_catalog
    {
        "table": "allergy_catalog",
        "trgm_cols": ["name"],
        "fts_expr": "coalesce(name, '') || ' ' || coalesce(description, '')",
    },
    # anatomy_structures
    {
        "table": "anatomy_structures",
        "trgm_cols": ["name", "slug"],
        "fts_expr": (
            "coalesce(name, '') || ' ' || coalesce(slug, '') || ' ' || "
            "coalesce(description, '') || ' ' || coalesce(standard_code, '')"
        ),
    },
    # vaccine_catalog
    {
        "table": "vaccine_catalog",
        "trgm_cols": ["name"],
        "fts_expr": (
            "coalesce(name, '') || ' ' || coalesce(description, '') || ' ' || "
            "coalesce(code, '')"
        ),
    },
    # concepts: the existing ix_concepts_trgm is on (name || ' ' || slug)
    # which is NOT usable by per-column ``name % q`` predicates. Add proper
    # per-column indexes; the old narrow one is left in place for any caller
    # that references the concatenated expression directly.
    {
        "table": "concepts",
        "trgm_cols": ["name", "slug"],
        "fts_expr": (
            "coalesce(name, '') || ' ' || coalesce(slug, '') || ' ' || "
            "coalesce(description, '') || ' ' || coalesce(code, '') || ' ' || "
            "coalesce(aliases::text, '')"
        ),
    },
]


def upgrade() -> None:
    """Create trigram + FTS GIN expression indexes per catalog."""
    for spec in _CATALOGS:
        table = spec["table"]
        # Per-column trigram indexes. Each one lets the planner use a GIN
        # scan + BitmapOr for ``col1 % q OR col2 % q`` predicates.
        for col in spec["trgm_cols"]:
            idx_name = f"ix_{table}_{col}_trgm"
            op.execute(
                f"CREATE INDEX IF NOT EXISTS {idx_name} "
                f"ON {table} USING GIN ({col} gin_trgm_ops)"
            )
        # FTS tsvector expression index — one per table.
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_fts "
            f"ON {table} USING GIN (to_tsvector('simple', {spec['fts_expr']}))"
        )


def downgrade() -> None:
    """Drop only the indexes this migration created."""
    for spec in _CATALOGS:
        table = spec["table"]
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_fts")
        for col in spec["trgm_cols"]:
            op.execute(f"DROP INDEX IF EXISTS ix_{table}_{col}_trgm")
