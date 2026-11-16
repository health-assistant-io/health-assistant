"""Descriptor-derived catalog search indexes (ADR 0055 Phase 4).

The migration lane freezes the surfaces it indexed
(``alembic/versions/0024_catalog_cleanup.py::SURFACES_AT_0024``); the live
registry must still produce exactly those surfaces, the indexes must exist, and
the query expression must be recognised by the planner — otherwise a searched
column silently seq-scans.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.catalog_search_index import CatalogSearchSurface
from app.core.database import AsyncSessionLocal
from app.services.catalog.catalog_search_service import (
    _build_hybrid_sql,
    _specs_by_type,
    catalog_search_surfaces,
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0024_catalog_cleanup.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("catalog_cleanup_0024", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_migration_surfaces_equal_the_live_registry():
    """A searched column added without a migration fails here."""
    frozen = {s.catalog_type: s for s in _migration_module().SURFACES_AT_0024}
    live = {s.catalog_type: s for s in catalog_search_surfaces()}
    assert frozen.keys() == live.keys()
    for catalog_type, surface in live.items():
        expected = frozen[catalog_type]
        assert surface.fts_columns == expected.fts_columns, catalog_type
        assert surface.trigram_columns == expected.trigram_columns, catalog_type
        assert surface.json_columns == expected.json_columns, catalog_type
        assert surface.table == expected.table, catalog_type


def test_every_registered_catalog_declares_a_search_surface():
    from app.catalogs.registry import CatalogRegistry

    for descriptor in CatalogRegistry.all():
        assert descriptor.fts_columns, descriptor.type
        assert descriptor.search_surface.table == descriptor.model.__tablename__
        assert descriptor.search_surface.fts_columns == descriptor.fts_columns


def test_query_expression_is_the_indexed_expression():
    """The hybrid spec's FTS body comes from the same surface the index uses."""
    specs = _specs_by_type()
    for surface in catalog_search_surfaces():
        spec = specs[surface.catalog_type]
        assert spec.fts_expression == surface.fts_expression()
        assert spec.label_column == surface.label_column
        assert spec.extra_trgm_columns == tuple(
            column for column in surface.trigram_columns if column != surface.label_column
        )
        sql = _build_hybrid_sql(spec)
        assert f"to_tsvector('simple', {surface.fts_expression()})" in sql


@pytest.mark.asyncio
async def test_indexes_exist_and_are_used():
    """Every surface's FTS + trigram indexes exist and the planner picks them."""
    async with AsyncSessionLocal() as db:
        existing = {
            row[0]
            for row in (
                await db.execute(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
                )
            ).all()
        }
        missing = [
            name
            for surface in catalog_search_surfaces()
            for name in surface.index_names()
            if name not in existing
        ]
        assert not missing, f"0024 catalog search indexes missing: {missing}"
        assert "ix_concepts_trgm" not in existing

        for surface in catalog_search_surfaces():
            await db.execute(text("SAVEPOINT plan_probe"))
            await db.execute(text("SET LOCAL enable_seqscan = off"))
            fts_plan = "\n".join(
                row[0]
                for row in (
                    await db.execute(
                        text(
                            "EXPLAIN SELECT 1 FROM "
                            f"{surface.table} t WHERE to_tsvector('simple', "
                            f"{surface.fts_expression()}) "
                            "@@ websearch_to_tsquery('simple', 'glucose')"
                        )
                    )
                ).all()
            )
            assert surface.fts_index_name() in fts_plan, (surface.table, fts_plan)

            trgm_plan = "\n".join(
                row[0]
                for row in (
                    await db.execute(
                        text(
                            "EXPLAIN SELECT 1 FROM "
                            f"{surface.table} t WHERE t.{surface.label_column} % 'glucose'"
                        )
                    )
                ).all()
            )
            assert surface.trigram_index_name(surface.label_column) in trgm_plan, (
                surface.table,
                trgm_plan,
            )
            await db.execute(text("ROLLBACK TO SAVEPOINT plan_probe"))
        await db.rollback()


def test_trigram_jsonb_members_are_not_indexed_as_text_columns():
    """A JSONB member is searched through its cast; only plain text columns
    carry a per-column trigram index."""
    for surface in catalog_search_surfaces():
        assert not set(surface.indexed_trigram_columns) & set(surface.json_columns)


def test_surface_ddl_is_self_consistent():
    surface = CatalogSearchSurface(
        catalog_type="probe",
        table="probe_table",
        fts_columns=("name", "notes"),
        trigram_columns=("slug",),
        json_columns=("aliases",),
    )
    assert surface.fts_expression() == (
        "coalesce(name::text, '') || ' ' || coalesce(notes::text, '')"
    )
    assert surface.index_names() == (
        "ix_probe_table_name_trgm",
        "ix_probe_table_slug_trgm",
        "ix_probe_table_fts",
    )
    names = [name for name, _ in surface.index_ddl()]
    assert names == list(surface.index_names())


def test_hybrid_sql_name_only_surface_has_no_dangling_join():
    """F11: a surface with no extra matched columns must not emit ``..., ,``.

    The matched-flag projection used to join an always-present list; a
    name-only surface (label + FTS only) left an empty item between the commas
    and produced invalid SQL.
    """
    from app.services.catalog.catalog_search_service import _CatalogSearchSpec

    class _NameOnly:
        __tablename__ = "name_only_rows"

    spec = _CatalogSearchSpec(
        type_name="name_only",
        model=_NameOnly,
        label_column="name",
        fts_expression="coalesce(t.name, '')",
    )
    sql = _build_hybrid_sql(spec)
    assert ", ," not in sql
    assert "r.m_label_ilike, r.m_fts," in sql
    assert "r.m_label_ilike, r.m_fts, )" not in sql


def test_hybrid_sql_every_real_surface_has_no_dangling_join():
    for catalog_type, spec in _specs_by_type().items():
        sql = _build_hybrid_sql(spec)
        assert ", ," not in sql, catalog_type
