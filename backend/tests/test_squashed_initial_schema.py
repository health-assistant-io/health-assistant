"""Tests for the consolidated schema baseline + its follow-up chain.

Verifies:
- There is exactly ONE root baseline migration (``down_revision is None``);
  additional incremental follow-up migrations are allowed on top of it.
- The baseline creates the full expected table set (derived from the ORM
  metadata so it cannot drift).
- Installs the required extensions.
- Leaves the post-consolidation invariants: unified concept tables present,
  the legacy scattered category tables gone.
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


def _load_migration_module(revision_attr: str = "down_revision", want=None):
    """Load a migration module by an attribute value. By default loads the
    baseline (the unique migration whose ``down_revision is None``)."""
    files = _migration_files()
    baseline = None
    for path in files:
        spec = importlib.util.spec_from_file_location(Path(path).stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if getattr(mod, revision_attr, object()) is want:
            baseline = (mod, Path(path))
            break
    assert baseline is not None, (
        f"no baseline migration (down_revision is None) found in {files}"
    )
    return baseline


def test_there_is_exactly_one_root_baseline():
    """The chain must have exactly one root baseline (down_revision is None).
    Incremental follow-up migrations on top of it are fine."""
    roots = []
    for path in _migration_files():
        spec = importlib.util.spec_from_file_location(Path(path).stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if mod.down_revision is None:
            roots.append(path)
    assert len(roots) == 1, (
        f"expected exactly one root baseline migration, found {roots}"
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
