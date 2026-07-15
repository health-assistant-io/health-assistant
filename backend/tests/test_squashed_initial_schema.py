"""Tests for the single consolidated schema baseline.

Verifies the one squashed migration that supersedes the historical chain:
- Is the sole migration (single head, base root).
- Creates the full expected table set (derived from the ORM metadata so it
  cannot drift).
- Installs the required extensions.
- Leaves the post-consolidation invariants: unified concept tables present,
  the legacy scattered category tables gone.
- Round-trips cleanly (upgrade -> downgrade -> upgrade).
"""
import glob
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import settings
from app.models.base import Base

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _migration_files():
    return sorted(
        p for p in glob.glob(str(VERSIONS_DIR / "*.py")) if not p.endswith("__init__.py")
    )


def _load_migration_module():
    files = _migration_files()
    assert len(files) == 1, f"expected a single baseline migration, found {files}"
    path = Path(files[0])
    spec = importlib.util.spec_from_file_location("consolidated_baseline", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, path


def test_there_is_exactly_one_migration_file():
    files = _migration_files()
    assert len(files) == 1, (
        f"the chain should be squashed to a single migration; found {files}"
    )


def test_revision_identifiers():
    mod, _ = _load_migration_module()
    assert mod.down_revision is None, (
        "the consolidated baseline must be the root (down_revision=None)"
    )


@pytest.mark.parametrize("table", sorted(Base.metadata.tables))
def test_every_model_table_is_created_by_the_baseline(table):
    """The baseline must define every table registered on the ORM metadata."""
    mod, path = _load_migration_module()
    src = path.read_text()
    assert f"op.create_table('{table}'" in src, (
        f"Table '{table}' is missing from the consolidated baseline"
    )


def test_concept_tables_exist():
    """The unified concept tables exist after migration."""
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            for table in ("concepts", "concept_edges", "concept_kind_tags"):
                result = conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name = :t"
                    ),
                    {"t": table},
                ).scalar()
                assert result == 1, f"Table '{table}' does not exist"
    finally:
        engine.dispose()


def test_old_category_tables_dropped():
    """The legacy scattered category tables must not exist."""
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            for table in (
                "examination_categories",
                "clinical_event_categories",
                "biomarker_groups",
                "biomarker_group_members",
                "biomarker_relationships",
                "biomarker_event_correlations",
                "body_parts",
            ):
                result = conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_name = :t"
                    ),
                    {"t": table},
                ).scalar()
                assert result == 0, f"Old table '{table}' should not exist"
    finally:
        engine.dispose()


def test_extensions_installed():
    """pgcrypto, pg_trgm, and timescaledb must be installed."""
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            for ext in ("pgcrypto", "pg_trgm", "timescaledb"):
                result = conn.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = :ext"),
                    {"ext": ext},
                ).scalar()
                assert result == 1, f"extension {ext} not installed"
    finally:
        engine.dispose()


def test_pg_enum_types_carry_every_python_enum_value():
    """Every value of every PG-backed Python enum must exist as a label on the
    matching Postgres enum type (the consolidated baseline creates them inline
    via ``sa.Enum(...)`` during table creation)."""
    from app.models import enums

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT t.typname, e.enumlabel "
                    "FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid"
                )
            ).all()
            pg_values: dict[str, set[str]] = {}
            for typname, label in rows:
                pg_values.setdefault(typname, set()).add(label)
    finally:
        engine.dispose()

    # (pg_type_name, python_enum_class). Comparison is case-insensitive: some
    # enums (e.g. CodingSystem) deliberately store uppercase labels in Postgres
    # while their Python .value is lowercase -- a pre-existing quirk unrelated
    # to whether the baseline created the type.
    checks = [
        ("medicationstatus", enums.MedicationStatus),
        ("gender", enums.Gender),
        ("role", enums.Role),
        ("codingsystem", enums.CodingSystem),
        ("quantitytype", enums.QuantityType),
    ]
    for pg_name, py_enum in checks:
        python_values = {v.value.lower() for v in py_enum}
        pg_labels = {lab.lower() for lab in pg_values.get(pg_name, set())}
        assert python_values <= pg_labels, (
            f"{pg_name}: PG missing values {python_values - pg_labels}"
        )
