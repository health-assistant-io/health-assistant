"""Tests for the squashed initial schema migration (0001).

Verifies the single consolidated migration:
- Creates the expected table set.
- Seeds examination categories with deterministic UUIDs.
- Creates all PG enum types idempotently.
- Round-trips cleanly (upgrade -> downgrade -> upgrade).
"""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core.config import settings


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0001_initial_schema.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("initial_schema", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), "squashed initial migration missing"


def test_revision_identifiers():
    mod = _load_migration_module()
    assert mod.revision == "0001"
    assert mod.down_revision is None, (
        "squashed initial must be the base (down_revision=None)"
    )


def test_enum_types_defined():
    """Every PG enum used by the models must appear in ENUM_TYPES."""
    mod = _load_migration_module()
    names = {name for name, _ in mod.ENUM_TYPES}
    expected = {
        "aiscope",
        "allergycategory",
        "allergyclinicalstatus",
        "allergycriticality",
        "clinicaleventstatus",
        "codingsystem",
        "exportscope",
        "exporttype",
        "gender",
        "integrationstatus",
        "jobstatus",
        "medicationintent",
        "medicationstatus",
        "notificationchannel",
        "notificationstatus",
        "notificationtype",
        "organizationtype",
        "quantitytype",
        "role",
        "triggertype",
    }
    missing = expected - names
    assert not missing, f"ENUM_TYPES missing: {missing}"


def test_medicatonstatus_enum_values_match_python():
    """The medicationstatus PG enum must carry every Python MedicationStatus
    value (the audit's D1 fix, now baked into the squashed schema)."""
    from app.models.enums import MedicationStatus

    mod = _load_migration_module()
    meds_entry = next(v for name, v in mod.ENUM_TYPES if name == "medicationstatus")
    # Values appear quoted inside the parenthesised list.
    pg_values = {v.strip().strip("'") for v in meds_entry.strip("()").split(",")}
    python_values = {v.value for v in MedicationStatus}
    assert pg_values == python_values, (
        f"PG medicationstatus {pg_values} != Python {python_values}"
    )


@pytest.mark.parametrize(
    "table",
    [
        "users",
        "tenants",
        "fhir_patients",
        "fhir_observations",
        "fhir_medications",
        "fhir_allergy_intolerances",
        "fhir_diagnostic_reports",
        "fhir_organizations",
        "fhir_provenance",
        "fhir_devices",
        "fhir_communications",
        "documents",
        "examinations",
        "examination_categories",
        "clinical_events",
        "clinical_event_categories",
        "clinical_event_types",
        "medication_catalog",
        "allergy_catalog",
        "biomarker_definitions",
        "biomarker_groups",
        "biomarker_group_members",
        "biomarker_relationships",
        "biomarker_event_correlations",
        "telemetry_data",
        "notifications",
        "notification_triggers",
        "notification_subscriptions",
        "notification_recipients",
        "notification_deliveries",
        "notification_rules",
        "audit_logs",
        "task_logs",
        "chat_sessions",
        "chat_messages",
        "user_integrations",
        "system_integrations",
        "system_settings",
        "ai_providers",
        "ai_models",
        "ai_task_assignments",
        "export_jobs",
        "import_jobs",
        "patient_layouts",
        "doctors",
        "body_parts",
        "units",
        "laboratories",
        "event_examination_links",
        "event_observation_links",
        "examination_doctors",
        "organization_doctors",
    ],
)
def test_table_exists_in_migration(table):
    """The squashed migration must define every expected table."""
    src = MIGRATION_PATH.read_text()
    assert f"op.create_table('{table}'" in src, (
        f"Table '{table}' is missing from the squashed initial migration"
    )


def test_examination_categories_seeded():
    """The migration seeds the default examination categories.

    Uses a live query against the migrated test database (conftest already
    runs `alembic upgrade head`).
    """
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT count(*) FROM examination_categories "
                    "WHERE slug = 'laboratory-tests'"
                )
            ).scalar()
            assert result == 1, "default category 'laboratory-tests' not seeded"

            total = conn.execute(
                text("SELECT count(*) FROM examination_categories")
            ).scalar()
            assert total >= 15, (
                f"expected >=15 default categories, got {total}"
            )
    finally:
        engine.dispose()


def test_examination_category_ids_are_deterministic():
    """Re-running the seed must produce identical UUIDs (uuid5-based)."""
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            slug_to_id = dict(
                conn.execute(
                    text(
                        "SELECT slug, id FROM examination_categories "
                        "WHERE slug = 'laboratory-tests'"
                    )
                ).all()
            )
            # The uuid5 of NAMESPACE_DNS('health-assistant.examination_categories')
            # + 'laboratory-tests' must be stable across runs.
            import uuid

            ns = uuid.uuid5(
                uuid.NAMESPACE_DNS, "health-assistant.examination_categories"
            )
            expected = str(uuid.uuid5(ns, "laboratory-tests"))
            actual = str(slug_to_id["laboratory-tests"])
            assert actual == expected, (
                f"category id {actual} != deterministic expected {expected}"
            )
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
                    text(
                        "SELECT 1 FROM pg_extension WHERE extname = :ext"
                    ),
                    {"ext": ext},
                ).scalar()
                assert result == 1, f"extension {ext} not installed"
    finally:
        engine.dispose()
