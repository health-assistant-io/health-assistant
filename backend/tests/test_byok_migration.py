"""Migration b1y2o3k4s5e6 round-trip: preset_key + audio_input → stt caps.

Family-migration discipline: a data-migration test (legacy ``audio_input``
rows are rewritten to ``stt``) plus a tested downgrade round-trip. Runs the
real alembic chain against the test database and restores head afterwards.
"""

import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

REVISION = "b1y2o3k4s5e6"
PARENT = "r1e2a3l4t5i6"


def _alembic_cfg() -> Config:
    return Config("alembic.ini")


def _connect():
    from app.core.config import settings

    engine = sa.create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg2"))
    return engine


def _seed_legacy(engine) -> tuple[str, str]:
    provider_id = str(uuid.uuid4())
    model_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO ai_providers (id, name, scope, provider_type, api_base) "
                "VALUES (:id, 'Legacy row', 'SYSTEM', 'openai', 'https://api.openai.com/v1')"
            ),
            {"id": provider_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ai_models (id, provider_id, name, model_name, capabilities) "
                "VALUES (:id, :pid, 'Whisper', 'whisper-1', CAST(:caps AS jsonb))"
            ),
            {"id": model_id, "pid": provider_id, "caps": '["text", "audio_input"]'},
        )
    return provider_id, model_id


def _cleanup(engine, provider_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text("DELETE FROM ai_models WHERE provider_id = :pid"),
            {"pid": provider_id},
        )
        conn.execute(
            sa.text("DELETE FROM ai_providers WHERE id = :pid"),
            {"pid": provider_id},
        )


def _caps_of(engine, model_id: str) -> list:
    with engine.connect() as conn:
        raw = conn.execute(
            sa.text("SELECT capabilities FROM ai_models WHERE id = :id"),
            {"id": model_id},
        ).scalar()
    return raw if isinstance(raw, list) else []


def _preset_key_column_exists(engine) -> bool:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'ai_providers' AND column_name = 'preset_key'"
            )
        ).fetchall()
    return bool(rows)


@pytest.mark.usefixtures("run_migrations")
def test_byok_migration_round_trip():
    cfg = _alembic_cfg()
    command.downgrade(cfg, PARENT)
    engine = _connect()
    provider_id, model_id = _seed_legacy(engine)
    try:
        assert not _preset_key_column_exists(engine)
        assert _caps_of(engine, model_id) == ["text", "audio_input"]

        command.upgrade(cfg, REVISION)

        assert _preset_key_column_exists(engine)
        assert _caps_of(engine, model_id) == ["text", "stt"]

        command.downgrade(cfg, PARENT)

        assert not _preset_key_column_exists(engine)
        assert _caps_of(engine, model_id) == ["text", "audio_input"]

        command.upgrade(cfg, "head")

        assert _caps_of(engine, model_id) == ["text", "stt"]
    finally:
        _cleanup(engine, provider_id)
        engine.dispose()
    command.upgrade(cfg, "head")
