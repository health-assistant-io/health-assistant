"""Telemetry caggs: enable realtime aggregation

Fixes the biomarker-trends telemetry path (2026-08-18):

1. The trends query violated TimescaleDB's rule that the
   ``time_bucket_gapfill`` expression must appear in the GROUP BY at top
   level — ``GROUP BY bucket`` resolved to the source column, so every
   telemetry trends query failed with
   ``no top level time_bucket_gapfill in group by clause`` and the service
   silently fell back to raw FHIR observations. (Fixed in
   ``analytics_service.get_biomarker_trends`` — SQL + explicit param casts.)
2. The three continuous aggregates were created ``WITH NO DATA`` +
   ``materialized_only = true``: any data newer than the refresh watermark
   is invisible through them (telemetry_monthly had zero rows while the raw
   hypertable held 260k). Realtime aggregation (cagg ∪ live raw buckets) is
   the TimescaleDB-recommended dashboard pattern; refresh policies continue
   to materialize history in the background.
"""

from alembic import op

revision = "r1e2a3l4t5i6"
down_revision = "m1o2b3i4l5e6"
branch_labels = None
depends_on = None

_CAGGS = ("telemetry_hourly", "telemetry_daily", "telemetry_monthly")


def _has_timescaledb() -> bool:
    conn = op.get_bind()
    return bool(
        conn.exec_driver_sql(
            "SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"
        ).scalar()
    )


def upgrade() -> None:
    if not _has_timescaledb():
        return
    for view in _CAGGS:
        op.execute(
            f"ALTER MATERIALIZED VIEW {view} "
            "SET (timescaledb.materialized_only = false)"
        )


def downgrade() -> None:
    if not _has_timescaledb():
        return
    for view in _CAGGS:
        op.execute(
            f"ALTER MATERIALIZED VIEW {view} "
            "SET (timescaledb.materialized_only = true)"
        )
