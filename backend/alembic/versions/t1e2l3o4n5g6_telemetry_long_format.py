"""telemetry: long-format hypertable

Rebuilds the ``telemetry_data`` hypertable in the canonical TimescaleDB /
InfluxDB / Prometheus **long format**: one row per ``(timestamp, device,
slug)`` with explicit ``value Float`` + ``unit Text`` columns, replacing the
asymmetric wide+JSONB hybrid (dedicated ``heart_rate``/``steps``/``calories``
Float columns + a JSONB ``data`` catch-all for everything else).

Why long-format:
- Adding a new telemetry biomarker is a **row-only** change (flip
  ``BiomarkerDefinition.is_telemetry = True``) — no DDL, no service-layer
  branching.
- Continuous aggregates become **generic**: one ``GROUP BY slug`` definition
  handles every current and future metric.
- Range queries (``WHERE value > 120``) get a real B-tree on
  ``(tenant_id, slug, timestamp)`` instead of per-metric JSONB functional
  indexes.

Bundled improvements while the table is being rebuilt:
- ``patient_id`` is now persisted on every row (populated at insert by the
  integration pipeline), killing the fragile
  ``device_id → UserIntegration → user_id → Patient`` attribution chain in
  the telemetry↔FHIR migration path.
- Three generic continuous aggregates (hourly / daily / monthly) replace the
  two hardcoded ones. The monthly CAgg covers the ``last-12-months`` /
  ``all-time`` analytics buckets that previously hit the raw hypertable.

Greenfield constraint: this project has no external users. The
``TelemetryDataPoint`` upload contract and ``telemetry_data`` schema are
**breaking changes** — no backwards-compatibility shims.

Revision ID: t1e2l3o4n5g6
Revises: s1e2t3u4p5w6
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


revision = "t1e2l3o4n5g6"
down_revision = "s1e2t3u4p5w6"
branch_labels = None
depends_on = None


def _has_timescaledb() -> bool:
    """Return True when the TimescaleDB extension is available on the server.

    Mirrors the guard in the consolidated baseline so this migration also
    succeeds on plain-PG dev/test databases where the extension is
    unavailable (the table is created as a regular table in that case).
    """
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    return bool(result)


def _drop_old_caggs() -> None:
    """Tear down the legacy dedicated-column continuous aggregates."""
    # Policies first, then the views themselves.
    op.execute(
        "SELECT remove_continuous_aggregate_policy('telemetry_daily', "
        "if_exists => true)"
    )
    op.execute(
        "SELECT remove_continuous_aggregate_policy('telemetry_hourly', "
        "if_exists => true)"
    )
    op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetry_daily CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetry_hourly CASCADE")


def _create_long_format_table() -> None:
    """Create the long-format ``telemetry_data`` table."""
    op.create_table(
        "telemetry_data",
        # No FK — TimescaleDB hypertables don't reliably support them.
        sa.Column("tenant_id", PG_UUID(as_uuid=True), nullable=True),
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("patient_id", PG_UUID(as_uuid=True), nullable=True),
        # Mixin columns (AuditMixin + VersionedMixin).
        sa.Column("created_by", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", PG_UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", "timestamp"),
    )
    with op.batch_alter_table("telemetry_data", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_telemetry_data_device_id"), ["device_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_telemetry_data_tenant_id"), ["tenant_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_telemetry_data_patient_id"), ["patient_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_telemetry_data_timestamp"), ["timestamp"], unique=False
        )
        # Hot path: tenant + metric + time range (analytics_service).
        batch_op.create_index(
            "ix_telemetry_data_tenant_slug_ts",
            ["tenant_id", "slug", "timestamp"],
            unique=False,
        )
        # Per-device timeline (telemetry_service reads).
        batch_op.create_index(
            "ix_telemetry_data_tenant_device_ts",
            ["tenant_id", "device_id", "timestamp"],
            unique=False,
        )


def _create_generic_cagg(bucket: str, name: str, start_offset: str,
                         end_offset: str, schedule: str) -> None:
    """Create one generic continuous aggregate + its refresh policy.

    ``GROUP BY slug`` makes every CAgg cover all current and future telemetry
    biomarkers — no DDL when a new biomarker is flagged ``is_telemetry=True``.
    """
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS {name}
        WITH (timescaledb.continuous) AS
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
    )
    op.execute(
        f"SELECT add_continuous_aggregate_policy('{name}', "
        f"start_offset => INTERVAL '{start_offset}', "
        f"end_offset => INTERVAL '{end_offset}', "
        f"schedule_interval => INTERVAL '{schedule}', "
        f"if_not_exists => true)"
    )


def upgrade() -> None:
    _drop_old_caggs()
    # CASCADE drops the hypertable + compression/retention policies in one go.
    op.execute("DROP TABLE IF EXISTS telemetry_data CASCADE")

    _create_long_format_table()

    if _has_timescaledb():
        op.execute(
            "SELECT create_hypertable('telemetry_data', 'timestamp', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        )
        # Segment by the high-cardinality query-pruning keys; order by time
        # desc so both tenant-wide and per-device queries prune chunks.
        op.execute(
            "ALTER TABLE telemetry_data SET ("
            "timescaledb.compress, "
            "timescaledb.compress_segmentby = 'tenant_id, device_id, slug', "
            "timescaledb.compress_orderby = 'timestamp DESC'"
            ")"
        )
        op.execute(
            "SELECT add_compression_policy('telemetry_data', "
            "INTERVAL '7 days', if_not_exists => true)"
        )
        op.execute(
            "SELECT add_retention_policy('telemetry_data', "
            "INTERVAL '2 years', if_not_exists => true)"
        )
        _create_generic_cagg(
            "1 hour", "telemetry_hourly",
            start_offset="3 days", end_offset="1 hour", schedule="1 hour",
        )
        _create_generic_cagg(
            "1 day", "telemetry_daily",
            start_offset="7 days", end_offset="1 day", schedule="1 day",
        )
        _create_generic_cagg(
            "1 month", "telemetry_monthly",
            start_offset="12 months", end_offset="1 month", schedule="1 day",
        )


def downgrade() -> None:
    # Drop TimescaleDB objects first, then the base table. The legacy
    # wide+JSONB schema is NOT reconstructed — downgrading requires a full
    # re-run of the consolidated baseline (greenfield project, no data to
    # preserve).
    if _has_timescaledb():
        op.execute(
            "SELECT remove_continuous_aggregate_policy('telemetry_monthly', "
            "if_exists => true)"
        )
        op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetry_monthly CASCADE")
        op.execute(
            "SELECT remove_continuous_aggregate_policy('telemetry_daily', "
            "if_exists => true)"
        )
        op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetry_daily CASCADE")
        op.execute(
            "SELECT remove_continuous_aggregate_policy('telemetry_hourly', "
            "if_exists => true)"
        )
        op.execute("DROP MATERIALIZED VIEW IF EXISTS telemetry_hourly CASCADE")
    op.execute("DROP TABLE IF EXISTS telemetry_data CASCADE")
