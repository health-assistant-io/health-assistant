"""Model ⇄ DB drift gate (guidelines/data-model.md §3).

Every schema change must be **model-first**: this test compares the live
database (migrated to head by ``conftest.run_migrations``) with
``Base.metadata`` through Alembic's autogenerate comparison and requires an
**empty** diff. A model edited without a migration — or a migration edited to
disagree with its model — turns this red.

Honest scope (plan 2 §2.4): the gate covers model-owned DDL only. TimescaleDB
objects (hypertable conversion, continuous aggregates, policies) and raw
``CREATE INDEX`` shapes in ``MIGRATION_MANAGED_INDEXES`` are invisible to
autogenerate; the ladder is guarded by ``test_telemetry_caggs.py`` and the
index list by ``test_schema_parameters.py``.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import settings
from app.core.schema_gate import include_object
from app.models.base import Base


def _sync_engine():
    return create_engine(settings.HA_DATABASE_URL.replace("+asyncpg", "+psycopg2"))


def test_database_is_at_chain_head():
    """The comparison is only meaningful when the DB is at head."""
    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    assert current == head, f"test DB is at {current!r}, chain head is {head!r}"


def test_model_metadata_matches_database():
    """Autogenerate against the migrated DB must produce an empty diff."""
    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn,
                opts={"compare_type": True, "include_object": include_object},
            )
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    # Human-readable failure: the gate is a debugging tool, not a puzzle.
    rendered = "\n".join(repr(entry) for entry in diff)
    assert diff == [], (
        "model metadata does not match the migrated database — every schema "
        "change must be model-first and carry a migration in the same commit. "
        f"Diff:\n{rendered}"
    )


def test_chain_has_exactly_one_head():
    """A second head makes ``alembic upgrade head`` ambiguous."""
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    assert len(heads) == 1, f"expected exactly one head, found {heads}"


def _alembic_config():
    from alembic.config import Config

    return Config("alembic.ini")
