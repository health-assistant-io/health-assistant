"""Telemetry unification: device registry, canonical units, anomaly events.

One revision (plan 3 W1/W2/W4):

**``telemetry_devices``** replaces the free-string ``telemetry_data.device_id``.
A device is a row identified by ``(tenant, patient, source, external_ref)``, so
new hardware is an INSERT, never DDL (constitution rule 8). The hypertable keeps
a UUID ``device_id`` with **no FK** — TimescaleDB cannot enforce referential
integrity across chunks — and the same tenant/patient cleanup contract applies.

**Canonical units with provenance.** ``telemetry_data.value``/``unit`` become
the canonical measurement (normalized at ingest against the biomarker's
preferred unit); ``raw_value``/``raw_unit`` keep exactly what the source sent.
A conversion-table fix can therefore recompute the canonical pair from stored
provenance without asking devices to re-ingest (peer review 2026-10-21 item 9).

**``telemetry_anomalies``** is the persisted output of the one CAgg-based
detection engine. The dedup key is
``UNIQUE (tenant_id, patient_id, device_id, slug, window_start, kind)`` —
patient **and** device are in the key, so two patients or two devices sharing a
slug never collide and silently lose an event (peer review item 1). ``kind`` is
the closed spike/drop vocabulary; ``payload`` is a versioned diagnostics
document.

Data policy (owner doctrine, no users): the pre-unification free-string device
ids cannot be mapped to UUID identities, so ``telemetry_data`` is truncated
during the rewrite. Downgrade restores the previous DDL shape and maps device
ids back through ``telemetry_devices.external_ref``; deliberately discarded
rows are not resurrected.

TimescaleDB mechanics: changing the type of a column a continuous aggregate
selects forces the ladder to be dropped and recreated. This is not churn — the
ladder shape is still the frozen ``0002_telemetry`` decision, and this revision
re-installs it verbatim after the base-table rewrite (refresh policies,
retention policy, 7-day chunks, compression).

Revision ID: 0005_telemetry_unified
Revises: 0004_proposals_unified
Create Date: 2026-10-22

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_telemetry_unified"
down_revision: str | Sequence[str] | None = "0004_proposals_unified"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHUNK_INTERVAL = "7 days"
RETENTION_INTERVAL = "2 years"

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
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    return bool(result)


def _drop_ladder() -> None:
    if not _has_timescaledb():
        return
    op.execute("SELECT remove_retention_policy('telemetry_data', if_exists => true)")
    op.execute("SELECT remove_compression_policy('telemetry_data', if_exists => true)")
    op.execute("ALTER TABLE telemetry_data SET (timescaledb.compress = false)")
    for view, _bucket, _start, _end, _schedule in _LADDER:
        op.execute(f"SELECT remove_continuous_aggregate_policy('{view}', if_exists => true)")
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view} CASCADE")


def _create_ladder() -> None:
    if not _has_timescaledb():
        return
    op.execute(f"SELECT set_chunk_time_interval('telemetry_data', INTERVAL '{CHUNK_INTERVAL}')")
    op.execute(
        "ALTER TABLE telemetry_data SET ("
        "timescaledb.compress, "
        "timescaledb.compress_segmentby = 'tenant_id, device_id, slug', "
        "timescaledb.compress_orderby = 'timestamp DESC'"
        ")"
    )
    op.execute(
        "SELECT add_compression_policy('telemetry_data', INTERVAL '7 days', if_not_exists => true)"
    )
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


def _create_devices() -> None:
    op.create_table(
        "telemetry_devices",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("external_ref", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_telemetry_devices_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_telemetry_devices_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telemetry_devices")),
        sa.UniqueConstraint(
            "tenant_id",
            "patient_id",
            "source",
            "external_ref",
            name=op.f("uq_telemetry_devices_device_identity"),
        ),
    )
    for name, columns in (
        ("ix_telemetry_devices_created_at", ["created_at"]),
        ("ix_telemetry_devices_patient_id", ["patient_id"]),
        ("ix_telemetry_devices_tenant_id", ["tenant_id"]),
        ("ix_telemetry_devices_updated_at", ["updated_at"]),
    ):
        op.create_index(name, "telemetry_devices", columns, unique=False)


def _create_anomalies() -> None:
    op.create_table(
        "telemetry_anomalies",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("z_score", sa.Float(), nullable=False),
        sa.Column("delta", sa.Float(), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "payload_schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "kind IN ('spike', 'drop')",
            name=op.f("ck_telemetry_anomalies_telemetry_anomaly_kind"),
        ),
        sa.CheckConstraint(
            "payload_schema_version > 0",
            name=op.f("ck_telemetry_anomalies_payload_schema_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["fhir_patients.id"],
            name=op.f("fk_telemetry_anomalies_patient_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_telemetry_anomalies_tenant_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telemetry_anomalies")),
        sa.UniqueConstraint(
            "tenant_id",
            "patient_id",
            "device_id",
            "slug",
            "window_start",
            "kind",
            name=op.f("uq_telemetry_anomalies_event"),
        ),
    )
    for name, columns in (
        ("ix_telemetry_anomalies_created_at", ["created_at"]),
        ("ix_telemetry_anomalies_device_id", ["device_id"]),
        ("ix_telemetry_anomalies_patient_id", ["patient_id"]),
        ("ix_telemetry_anomalies_tenant_id", ["tenant_id"]),
        ("ix_telemetry_anomalies_tenant_patient_slug", ["tenant_id", "patient_id", "slug"]),
        ("ix_telemetry_anomalies_tenant_resolved", ["tenant_id", "resolved_at"]),
        ("ix_telemetry_anomalies_updated_at", ["updated_at"]),
    ):
        op.create_index(name, "telemetry_anomalies", columns, unique=False)


def upgrade() -> None:
    _create_devices()
    _create_anomalies()

    # The ladder selects ``device_id``; a type change forces a rebuild.
    _drop_ladder()
    op.execute("TRUNCATE TABLE telemetry_data")
    op.execute("ALTER TABLE telemetry_data RENAME COLUMN device_id TO device_ref")
    op.add_column("telemetry_data", sa.Column("device_id", sa.UUID(), nullable=True))
    op.add_column("telemetry_data", sa.Column("raw_value", sa.Float(), nullable=True))
    op.add_column("telemetry_data", sa.Column("raw_unit", sa.String(length=64), nullable=True))
    op.execute("ALTER TABLE telemetry_data ALTER COLUMN device_id SET NOT NULL")
    op.execute("ALTER TABLE telemetry_data ALTER COLUMN raw_value SET NOT NULL")
    op.drop_column("telemetry_data", "device_ref")
    op.create_index(
        op.f("ix_telemetry_data_device_id"), "telemetry_data", ["device_id"], unique=False
    )
    op.create_index(
        "ix_telemetry_data_tenant_device_ts",
        "telemetry_data",
        ["tenant_id", "device_id", "timestamp"],
        unique=False,
    )
    _create_ladder()


def downgrade() -> None:
    _drop_ladder()
    op.add_column("telemetry_data", sa.Column("device_ref", sa.String(length=255), nullable=True))
    op.execute(
        """
        UPDATE telemetry_data t
        SET device_ref = d.external_ref
        FROM telemetry_devices d
        WHERE d.id = t.device_id
        """
    )
    op.drop_column("telemetry_data", "device_id")
    op.drop_column("telemetry_data", "raw_value")
    op.drop_column("telemetry_data", "raw_unit")
    op.alter_column(
        "telemetry_data",
        "device_ref",
        new_column_name="device_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_index(
        op.f("ix_telemetry_data_device_id"), "telemetry_data", ["device_id"], unique=False
    )
    op.create_index(
        "ix_telemetry_data_tenant_device_ts",
        "telemetry_data",
        ["tenant_id", "device_id", "timestamp"],
        unique=False,
    )
    _create_ladder()

    op.drop_table("telemetry_anomalies")
    op.drop_table("telemetry_devices")
