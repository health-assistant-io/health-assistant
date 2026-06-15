"""add timescaledb continuous aggregates

Revision ID: 1a829b7421ae
Revises: 1c0601cf051a
Create Date: 2026-06-15 16:51:14.764826

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1a829b7421ae'
down_revision: Union[str, Sequence[str], None] = '1c0601cf051a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Hourly continuous aggregate
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_hourly
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket('1 hour', timestamp) AS bucket,
            tenant_id,
            device_id,
            AVG(heart_rate) as heart_rate_avg,
            MIN(heart_rate) as heart_rate_min,
            MAX(heart_rate) as heart_rate_max,
            AVG(steps) as steps_avg,
            MIN(steps) as steps_min,
            MAX(steps) as steps_max,
            AVG(calories) as calories_avg,
            MIN(calories) as calories_min,
            MAX(calories) as calories_max
        FROM telemetry_data
        GROUP BY bucket, tenant_id, device_id
        WITH NO DATA;
    """)

    op.execute("""
        SELECT add_continuous_aggregate_policy('telemetry_hourly',
            start_offset => INTERVAL '3 days',
            end_offset => INTERVAL '1 hour',
            schedule_interval => INTERVAL '1 hour',
            if_not_exists => true);
    """)

    # Daily continuous aggregate
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_daily
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket('1 day', timestamp) AS bucket,
            tenant_id,
            device_id,
            AVG(heart_rate) as heart_rate_avg,
            MIN(heart_rate) as heart_rate_min,
            MAX(heart_rate) as heart_rate_max,
            AVG(steps) as steps_avg,
            MIN(steps) as steps_min,
            MAX(steps) as steps_max,
            AVG(calories) as calories_avg,
            MIN(calories) as calories_min,
            MAX(calories) as calories_max
        FROM telemetry_data
        GROUP BY bucket, tenant_id, device_id
        WITH NO DATA;
    """)

    op.execute("""
        SELECT add_continuous_aggregate_policy('telemetry_daily',
            start_offset => INTERVAL '7 days',
            end_offset => INTERVAL '1 day',
            schedule_interval => INTERVAL '1 day',
            if_not_exists => true);
    """)

def downgrade() -> None:
    op.execute("SELECT remove_continuous_aggregate_policy('telemetry_daily', if_exists => true);")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetry_daily;")
    
    op.execute("SELECT remove_continuous_aggregate_policy('telemetry_hourly', if_exists => true);")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetry_hourly;")
