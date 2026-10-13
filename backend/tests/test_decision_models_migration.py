"""Migration d1e2c3i4s5i6 round-trip: decision_transport + phi_allowed.

Family-migration discipline: schema-only migration (the decision capability
is explicit-only — no legacy row can be a decision model, and every
existing assignment stays PHI-excluded by default). Downgrade restores the
previous shape; the chain is restored to head afterwards.
"""

import uuid

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

REVISION = "d1e2c3i4s5i6"
PARENT = "c1h2a3t4b5r6"


def _alembic_cfg() -> Config:
    return Config("alembic.ini")


def _connect():
    from app.core.config import settings

    engine = sa.create_engine(settings.HA_DATABASE_URL.replace("+asyncpg", "+psycopg2"))
    return engine


def _column_exists(engine, table: str, column: str) -> bool:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                f"WHERE table_name = '{table}' AND column_name = '{column}'"
            )
        ).fetchall()
    return bool(rows)


def _phi_allowed_of(engine, assignment_id: str):
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT phi_allowed FROM ai_task_assignments WHERE id = :id"),
            {"id": assignment_id},
        ).scalar()


@pytest.mark.usefixtures("run_migrations")
def test_decision_models_migration_round_trip():
    cfg = _alembic_cfg()
    command.downgrade(cfg, PARENT)
    engine = _connect()
    assignment_id = str(uuid.uuid4())
    try:
        assert not _column_exists(engine, "ai_models", "decision_transport")
        assert not _column_exists(engine, "ai_task_assignments", "phi_allowed")

        command.upgrade(cfg, REVISION)

        assert _column_exists(engine, "ai_models", "decision_transport")
        assert _column_exists(engine, "ai_task_assignments", "phi_allowed")

        # The guard column defaults to FALSE (PHI-excluded) for rows created
        # without an explicit opt-in — the conservative shape (D17).
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO ai_task_assignments (id, task_type, scope, phi_allowed) "
                    "VALUES (:id, 'decision_tool_dispatch', 'SYSTEM', DEFAULT)"
                ),
                {"id": assignment_id},
            )
        assert _phi_allowed_of(engine, assignment_id) is False

        command.downgrade(cfg, PARENT)

        assert not _column_exists(engine, "ai_models", "decision_transport")
        assert not _column_exists(engine, "ai_task_assignments", "phi_allowed")
    finally:
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM ai_task_assignments WHERE id = :id"),
                {"id": assignment_id},
            )
        engine.dispose()
    command.upgrade(cfg, "head")
