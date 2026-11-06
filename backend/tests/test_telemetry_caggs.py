"""CAGG ladder guard (plan 3 §2.3, created once by ``0002_telemetry``).

TimescaleDB objects are invisible to the autogenerate drift gate, so the
ladder is verified here: every view exists, serves realtime buckets
(``materialized_only = false``), has a refresh policy, and the hypertable
carries the frozen 7-day chunk interval and exactly one retention policy.
"""

import datetime as _dt

import pytest
from sqlalchemy import create_engine, text

from app.core.config import settings

VIEWS = ("telemetry_hourly", "telemetry_daily", "telemetry_monthly")


def _engine():
    return create_engine(settings.HA_DATABASE_URL.replace("+asyncpg", "+psycopg2"))


@pytest.fixture(scope="module")
def has_timescaledb():
    engine = _engine()
    try:
        with engine.connect() as conn:
            return bool(
                conn.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
                ).scalar()
            )
    finally:
        engine.dispose()


def test_ladder_views_exist_and_are_realtime(has_timescaledb):
    if not has_timescaledb:
        pytest.skip("TimescaleDB not available")

    engine = _engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT view_name, materialized_only "
                    "FROM timescaledb_information.continuous_aggregates"
                )
            ).all()
    finally:
        engine.dispose()

    state = dict(rows)
    assert set(VIEWS) <= set(state), f"ladder incomplete: {sorted(state)}"
    not_realtime = sorted(name for name in VIEWS if state[name])
    assert not not_realtime, f"views not serving realtime buckets: {not_realtime}"


def test_every_view_has_a_refresh_policy(has_timescaledb):
    if not has_timescaledb:
        pytest.skip("TimescaleDB not available")

    engine = _engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT j.hypertable_name "
                    "FROM timescaledb_information.jobs j "
                    "WHERE j.proc_name = 'policy_refresh_continuous_aggregate'"
                )
            ).all()
    finally:
        engine.dispose()

    covered = {row[0] for row in rows}
    missing = sorted(set(VIEWS) - covered)
    assert not missing, f"views without a refresh policy: {missing}"


def test_hypertable_has_the_frozen_chunk_interval_and_one_retention_policy(has_timescaledb):
    if not has_timescaledb:
        pytest.skip("TimescaleDB not available")

    engine = _engine()
    try:
        with engine.connect() as conn:
            interval = conn.execute(
                text(
                    "SELECT time_interval FROM timescaledb_information.dimensions "
                    "WHERE hypertable_name = 'telemetry_data' AND dimension_number = 1"
                )
            ).scalar()
            retention = conn.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.jobs "
                    "WHERE proc_name = 'policy_retention' AND hypertable_name = 'telemetry_data'"
                )
            ).scalar()
    finally:
        engine.dispose()

    assert interval == _dt.timedelta(days=7), f"chunk interval is {interval!r}, expected 7 days"
    assert retention == 1, f"expected exactly one retention policy, found {retention}"
