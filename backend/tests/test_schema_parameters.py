"""Schema doctrine gates: naming convention, tombstones, migration-lane DDL.

These pin the decisions the baseline squash made (plans 2/6), so a later
change to any of them is a deliberate, reviewed edit rather than an accident:

* the five-key ``naming_convention`` on ``Base.metadata`` (rule 2);
* the **tombstone set** — exactly which tables carry ``deleted_at`` (rule 10);
* the migration-lane-owned indexes actually exist on the migrated DB
  (``app.core.schema_gate`` exemption list);
* no retired vocabulary in model docstrings/identifiers (rule 11).
"""

import ast
import io
import re
import tokenize
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import settings
from app.core.schema_gate import MIGRATION_MANAGED_INDEXES
from app.models.base import NAMING_CONVENTION, Base


def _sync_engine():
    return create_engine(settings.HA_DATABASE_URL.replace("+asyncpg", "+psycopg2"))


def test_naming_convention_is_the_family_five_key_block():
    """guidelines/data-model.md §2 — this exact five-key block."""
    assert NAMING_CONVENTION == {
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s",
        "pk": "pk_%(table_name)s",
    }
    assert Base.metadata.naming_convention == NAMING_CONVENTION


def test_model_constraints_render_family_prefixes():
    """Every rendered constraint/index name carries pk_/fk_/ck_/uq_/ix_."""
    prefixes = ("pk_", "fk_", "ck_", "uq_", "ix_")
    offenders: list[str] = []
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            name = constraint.name or ""
            if name and not name.startswith(prefixes):
                offenders.append(f"{table.name}: {name}")
        for index in table.indexes:
            name = index.name or ""
            if name and not name.startswith(("ix_", "uq_")):
                offenders.append(f"{table.name}: {name}")
    assert not offenders, "constraints outside the family naming convention:\n" + "\n".join(
        offenders
    )


#: The complete, deliberate tombstone set (plan 6 §2.4 + FHIR R4 410 Gone).
#: These are the FHIR-exposed resources: a deleted resource must answer
#: ``410 Gone`` forever, so proof-of-existence has to outlive the deletion.
#: Everything else hard-deletes. ``audit_events``, proposal resolution traces
#: and export/import job records need no ``deleted_at`` — they are append-only
#: by contract, which is stronger than a tombstone.
TOMBSTONE_TABLES = frozenset(
    {
        "clinical_events",
        "concepts",
        "documents",
        "examinations",
        "fhir_allergy_intolerances",
        "fhir_communications",
        "fhir_devices",
        "fhir_diagnostic_reports",
        "fhir_medications",
        "fhir_observations",
        "fhir_organizations",
        "fhir_patients",
        "patient_immunizations",
    }
)


def test_tombstone_set_is_exactly_the_declared_one():
    """Adding or removing ``deleted_at`` is a doctrine decision."""
    with_deleted_at = {
        table.name for table in Base.metadata.tables.values() if "deleted_at" in table.columns
    }
    assert with_deleted_at == TOMBSTONE_TABLES, (
        "tombstone set drifted — hard delete is the default; only the declared "
        "FHIR-exposed resources carry deleted_at. "
        f"Extra: {sorted(with_deleted_at - TOMBSTONE_TABLES)}; "
        f"Missing: {sorted(TOMBSTONE_TABLES - with_deleted_at)}"
    )


def test_migration_managed_indexes_exist_on_the_database():
    """The autogenerate exemption list cannot hide a missing index."""
    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname = ANY(:names)"
                ),
                {"names": sorted(MIGRATION_MANAGED_INDEXES)},
            ).all()
            present = {row[0] for row in rows}
            # A raw COALESCE index on a hypertable-independent table must be
            # visible in the catalogue; assert the full list.
            missing = MIGRATION_MANAGED_INDEXES - present
    finally:
        engine.dispose()

    assert not missing, (
        "migration-lane indexes missing from the migrated DB "
        f"(0001_baseline raw SQL drifted): {sorted(missing)}"
    )


def test_no_retired_naming_in_model_docstrings_and_comments():
    """Constitution rule 11 — model prose names the current concept only.

    Identifiers are excluded on purpose: ``audit_events.old_value`` is a
    legitimate write-diff column, not a retired *name*. What rule 11 forbids is
    prose that keeps explaining the schema in terms of what it replaced.
    """
    models_dir = Path(__file__).resolve().parents[1] / "app" / "models"
    pattern = re.compile(r"legacy|deprecated|(?<![A-Za-z0-9_])old_[a-z]|_v2\b", re.I)

    offenders: list[str] = []
    for path in sorted(models_dir.rglob("*.py")):
        source = path.read_text()
        prose: list[str] = []
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    prose.append(doc)
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                prose.append(token.string)

        for block in prose:
            for line in block.splitlines():
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(models_dir)}: {line.strip()}")

    assert not offenders, "retired naming left in model docstrings/comments:\n" + "\n".join(
        offenders
    )


def test_tenancy_nullability_is_explicit():
    """User-owned tables are NOT NULL; mixed-scope tables declare it."""
    system_scope_tables = {
        "audit_events",
        # Per-AI-call audit (plan 4 W3): system-scope calls (provider setup
        # probes, system-level tasks) legitimately run outside a tenant — the
        # audit_events precedent. Tenant rows carry tenant_id (CASCADE).
        "ai_call_logs",
        "concepts",
        "concept_edges",
        "doctors",
        "anatomy_structures",
        "allergy_catalog",
        "medication_catalog",
        "vaccine_catalog",
        "biomarker_definitions",
        "clinical_event_types",
        "catalog_audit_log",
        "ai_providers",
        "ai_task_assignments",
        "notifications",
        "notification_recipients",
        "notification_deliveries",
        "notification_triggers",
        "task_logs",
        # Erasure ledger (plan 6 W2): a tenant erasure sets tenant_id NULL so
        # the ledger row outlives the tenant (restore replay needs it).
        "erasure_requests",
        # Imported-extension sources (plan 7): NULL is a system-level source
        # (operator-registered origins usable instance-wide); tenant rows
        # carry tenant_id (CASCADE).
        "extension_sources",
    }

    engine = _sync_engine()
    try:
        inspector = inspect(engine)
        nullable: dict[str, bool] = {}
        for table_name in Base.metadata.tables:
            if table_name not in inspector.get_table_names():
                continue
            for column in inspector.get_columns(table_name):
                if column["name"] == "tenant_id":
                    nullable[table_name] = bool(column["nullable"])
    finally:
        engine.dispose()

    offenders = sorted(
        table
        for table, is_nullable in nullable.items()
        if is_nullable and table not in system_scope_tables
    )
    assert not offenders, f"user-owned tables with nullable tenant_id: {offenders}"

    missing_scope = sorted(system_scope_tables - set(nullable))
    assert not missing_scope, f"declared system-scope tables without tenant_id: {missing_scope}"
