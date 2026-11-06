"""Tests for the squashed schema baseline (``0001_baseline``) + its chain.

Verifies:
- The chain has exactly ONE root baseline (``down_revision is None``), the
  baseline is ``0001_baseline``, and links are sequential ``NNNN_slug`` ids.
- The baseline creates the full table set — derived from the ORM metadata so
  it cannot drift, and with **no** post-baseline allowlist (the squash
  absorbed every follow-up).
- Installs the required extensions.
- Leaves the post-consolidation invariants: unified concept tables present,
  the retired scattered category tables gone.
"""

import glob
import importlib.util
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import settings
from app.models.base import Base

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"

#: Tables introduced after the baseline squash, with the revision that owns
#: them. The baseline keeps the pre-squash shape; a table that appears here must
#: be created by exactly that revision.
POST_BASELINE_TABLES = {
    "ai_task_defs": "0003_ai_task_registry.py",
    "ai_call_logs": "0003_ai_task_registry.py",
    "proposal_kinds": "0004_proposals_unified.py",
    "proposals": "0004_proposals_unified.py",
    "telemetry_devices": "0005_telemetry_unified.py",
    "telemetry_anomalies": "0005_telemetry_unified.py",
    "integration_credentials": "0006_connector_framework.py",
    "integration_sync_state": "0006_connector_framework.py",
    "erasure_requests": "0007_data_lifecycle.py",
    "retention_policies": "0007_data_lifecycle.py",
    "extension_sources": "0008_extension_sources.py",
    "extension_source_versions": "0008_extension_sources.py",
    "extension_consents": "0008_extension_sources.py",
    "extension_isolation_posture": "0008_extension_sources.py",
    "calendar_bindings": "0009_calendar_bindings.py",
    "calendar_event_links": "0009_calendar_bindings.py",
    "calendar_binding_credentials": "0009_calendar_bindings.py",
    "calendar_sync_logs": "0009_calendar_bindings.py",
}


def _migration_files():
    return sorted(p for p in glob.glob(str(VERSIONS_DIR / "*.py")) if not p.endswith("__init__.py"))


def _load_migration_module(revision_attr: str = "down_revision", want=None):
    """Load the root baseline (the unique migration whose ``down_revision``
    is ``None``)."""
    files = _migration_files()
    baseline = None
    for path in files:
        spec = importlib.util.spec_from_file_location(Path(path).stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if getattr(mod, revision_attr, object()) is want:
            baseline = (mod, Path(path))
            break
    assert baseline is not None, f"no baseline migration (down_revision is None) found in {files}"
    return baseline


def test_there_is_exactly_one_root_baseline():
    """The chain must have exactly one root baseline."""
    roots = []
    for path in _migration_files():
        spec = importlib.util.spec_from_file_location(Path(path).stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if mod.down_revision is None:
            roots.append(path)
    assert len(roots) == 1, f"expected exactly one root baseline migration, found {roots}"


def test_baseline_is_0001_and_chain_ids_are_sequential():
    """The root is ``0001_baseline`` and every id is ``NNNN[_slug]``."""
    mod, path = _load_migration_module()
    assert mod.revision == "0001_baseline"
    assert path.name == "0001_baseline.py"
    for migration in _migration_files():
        stem = Path(migration).stem
        assert re.fullmatch(r"\d{4}(_[a-z0-9_]+)?", stem), (
            f"{stem} is not a sequential NNNN_slug revision id"
        )


@pytest.mark.parametrize("table", sorted(Base.metadata.tables))
def test_every_model_table_is_created_by_a_migration(table):
    """Every table on the ORM metadata is created by some revision.

    The squash absorbed every pre-2026-10-21 migration, so there is no
    post-baseline allowlist: the baseline must create each table EXCEPT the ones
    a later sequential revision introduces (currently ``ai_task_defs`` and
    ``ai_call_logs``, created by ``0003_ai_task_registry``). A table missing from
    every revision means model⇄migration drift (the drift gate in
    ``test_schema_drift_gate`` catches the reverse).
    """
    if table == "telemetry_data":
        _mod, path = _load_migration_module()
        assert "op.create_table(" in path.read_text() and "telemetry_data" in path.read_text()
        return

    created_in = [
        Path(migration).name
        for migration in _migration_files()
        if re.search(
            rf"op\.create_table\(\s*['\"]{re.escape(table)}['\"]",
            Path(migration).read_text(),
        )
    ]
    assert created_in, f"Table '{table}' is not created by any migration"
    if created_in != ["0001_baseline.py"]:
        assert table in POST_BASELINE_TABLES, (
            f"Table '{table}' is created by {created_in} but is not a declared "
            "post-baseline table — either fold it into the baseline or add it to "
            "POST_BASELINE_TABLES with its owning revision."
        )
        assert created_in == [POST_BASELINE_TABLES[table]], (
            f"Table '{table}' is created by {created_in}, expected {[POST_BASELINE_TABLES[table]]}"
        )


def test_baseline_carries_no_cagg_shape():
    """CAGG ladder creation belongs to ``0002_telemetry`` (plan 3 §2.3)."""
    _mod, path = _load_migration_module()
    src = path.read_text()
    assert "CREATE MATERIALIZED VIEW" not in src
    assert "add_continuous_aggregate_policy" not in src


def test_concept_tables_exist():
    """The unified concept tables exist after migration."""
    sync_url = settings.HA_DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            for table in ("concepts", "concept_edges", "concept_kind_tags"):
                result = conn.execute(
                    text("SELECT count(*) FROM information_schema.tables WHERE table_name = :t"),
                    {"t": table},
                ).scalar()
                assert result == 1, f"Table '{table}' does not exist"
    finally:
        engine.dispose()


def test_retired_category_tables_dropped():
    """The retired scattered category tables must not exist."""
    sync_url = settings.HA_DATABASE_URL.replace("+asyncpg", "+psycopg2")
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
                    text("SELECT count(*) FROM information_schema.tables WHERE table_name = :t"),
                    {"t": table},
                ).scalar()
                assert result == 0, f"Old table '{table}' should not exist"
    finally:
        engine.dispose()


def test_extensions_installed():
    """pgcrypto, pg_trgm, and timescaledb must be installed."""
    sync_url = settings.HA_DATABASE_URL.replace("+asyncpg", "+psycopg2")
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
