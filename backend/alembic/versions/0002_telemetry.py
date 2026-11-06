"""Telemetry aggregate ladder — created once, frozen.

The single analytical contract (plan 3 §2.3, decision frozen 2026-10-21):

* ``telemetry_hourly``  — ``time_bucket('1 hour')``,  realtime
* ``telemetry_daily``   — ``time_bucket('1 day')``,   realtime
* ``telemetry_monthly`` — ``time_bucket('1 month')``, realtime

Every view groups by ``(tenant_id, device_id, patient_id, slug)`` and computes
``avg/min/max/sum/count`` over the long-format ``telemetry_data`` hypertable,
so a new telemetry biomarker is a row-only change (constitution rule 8) — no
DDL and no per-metric view.

Also declared here, once:

* ``timescaledb.materialized_only = false`` on every view — realtime
  aggregation, i.e. buckets newer than the refresh watermark are served by
  unioning the live hypertable chunks. Dashboards never read stale data while
  the background refresh materializes history.
* A refresh policy per view (hourly: 3 d lookback; daily: 7 d; monthly: 12 mo).
* The hypertable's retention policy (``drop_chunks`` after 2 years) — the
  bound belongs to the ladder, not to the base table.
* The explicit 7-day chunk interval on ``telemetry_data`` (policy change, not
  schema change, if it ever needs revisiting).

The baseline (``0001_baseline``) deliberately carries no CAGG shape: the ladder
is a plan-3 decision, so it lands in exactly one revision (peer review
2026-10-21 item 7 — kills the plan-2↔plan-3 circularity). A CAGG created one
time is not debt; the ad-hoc policy flips of the pre-squash chain are gone.

TimescaleDB objects are invisible to the autogenerate drift gate, so this
ladder is guarded by its own tests (policy presence per view) instead.

Downgrade removes the retention + refresh policies and drops the three views,
restoring the plain (compressed) hypertable that ``0001_baseline`` created.

Revision ID: 0002_telemetry
Revises: 0001_baseline
Create Date: 2026-10-21

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_telemetry"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHUNK_INTERVAL = "7 days"
RETENTION_INTERVAL = "2 years"

# (view, bucket, refresh start_offset, refresh end_offset, schedule)
_LADDER = (
    ("telemetry_hourly", "1 hour", "3 days", "1 hour", "1 hour"),
    ("telemetry_daily", "1 day", "7 days", "1 day", "1 day"),
    ("telemetry_monthly", "1 month", "12 months", "1 month", "1 day"),
)

_VIEW_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS {view}
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket('{bucket}', timestamp) AS bucket,
    tenant_id,
    device_id,
    patient_id,
    slug,
    AVG(value) AS avg_val,
    MIN(value) AS min_val,
    MAX(value) AS max_val,
    SUM(value) AS sum_val,
    COUNT(*)   AS sample_count
FROM telemetry_data
GROUP BY 1, 2, 3, 4, 5
WITH NO DATA
"""


def _has_timescaledb() -> bool:
    """Return True when the TimescaleDB extension is available on the server."""
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    return bool(result)


def upgrade() -> None:
    """Create the frozen aggregate ladder and its policies."""
    if not _has_timescaledb():
        return

    op.execute(f"SELECT set_chunk_time_interval('telemetry_data', INTERVAL '{CHUNK_INTERVAL}')")

    for view, bucket, start_offset, end_offset, schedule in _LADDER:
        op.execute(_VIEW_SQL.format(view=view, bucket=bucket))
        op.execute(
            f"SELECT add_continuous_aggregate_policy('{view}', "
            f"start_offset => INTERVAL '{start_offset}', "
            f"end_offset => INTERVAL '{end_offset}', "
            f"schedule_interval => INTERVAL '{schedule}', "
            f"if_not_exists => true)"
        )

    op.execute(
        "SELECT add_retention_policy('telemetry_data', "
        f"INTERVAL '{RETENTION_INTERVAL}', if_not_exists => true)"
    )


def downgrade() -> None:
    """Remove the policies and drop the ladder, restoring the plain table."""
    if not _has_timescaledb():
        return

    op.execute("SELECT remove_retention_policy('telemetry_data', if_exists => true)")
    for view, _bucket, _start, _end, _schedule in _LADDER:
        op.execute(f"SELECT remove_continuous_aggregate_policy('{view}', if_exists => true)")
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view} CASCADE")
