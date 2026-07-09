"""Tests for audit item D8: tenant_id foreign keys.

Every tenant-owned table (except telemetry_data, a TimescaleDB hypertable
where FKs aren't supported) must have a foreign key constraint to
``tenants.id`` with ``ON DELETE CASCADE`` so deleting a tenant purges all
their data instead of orphaning it.
"""
import pytest
from sqlalchemy import create_engine, text

from app.core.config import settings


# Tables that carry tenant_id but legitimately lack an FK.
# - telemetry_data: TimescaleDB hypertable; FKs not supported.
# - telemetry_daily, telemetry_hourly: materialized views over telemetry_data.
# - catalog_audit_log: append-only audit trail. ``tenant_id`` is a denormalized
#   actor-context field (alongside ``user_id`` / ``user_email``) and is
#   deliberately FK-less so the trail SURVIVES tenant/user deletion — which is
#   the entire reason those fields are denormalized.
TABLES_WITHOUT_FK = {
    "telemetry_data",
    "telemetry_daily",
    "telemetry_hourly",
    "catalog_audit_log",
}


def _get_tenant_tables():
    """Return {table_name: has_fk} for every table with a tenant_id column."""
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT
                        c.table_name,
                        CASE WHEN tc.constraint_name IS NOT NULL
                             THEN True ELSE False END AS has_fk
                    FROM information_schema.columns c
                    LEFT JOIN information_schema.key_column_usage kcu
                        ON kcu.table_name = c.table_name
                        AND kcu.column_name = 'tenant_id'
                    LEFT JOIN information_schema.table_constraints tc
                        ON kcu.constraint_name = tc.constraint_name
                        AND tc.constraint_type = 'FOREIGN KEY'
                    WHERE c.column_name = 'tenant_id'
                        AND c.table_schema = 'public'
                    ORDER BY c.table_name
                    """
                )
            ).all()
            return {row[0]: row[1] for row in rows}
    finally:
        engine.dispose()


def test_tenant_tables_have_fk():
    """Every tenant-owned table (except the documented exceptions) must
    have a tenant_id FK to tenants.id."""
    table_map = _get_tenant_tables()
    missing = {
        t for t, has_fk in table_map.items()
        if not has_fk and t not in TABLES_WITHOUT_FK
    }
    assert not missing, (
        f"Tables missing tenant_id FK: {sorted(missing)}. "
        f"Expected exceptions: {sorted(TABLES_WITHOUT_FK)}"
    )


def test_telemetry_data_has_no_fk():
    """TelemetryDataModel must NOT have a tenant_id FK (TimescaleDB
    hypertable limitation). Application-level cleanup handles purging."""
    table_map = _get_tenant_tables()
    assert table_map.get("telemetry_data") is False, (
        "telemetry_data must not have a tenant_id FK — TimescaleDB hypertables "
        "do not reliably support FK constraints"
    )


def test_tenant_fk_uses_on_delete_cascade():
    """Every tenant_id FK must use ON DELETE CASCADE so tenant deletion
    purges all owned rows instead of raising a constraint violation."""
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT tc.table_name
                    FROM information_schema.referential_constraints rc
                    JOIN information_schema.table_constraints tc
                        ON rc.constraint_name = tc.constraint_name
                    JOIN information_schema.key_column_usage kcu
                        ON kcu.constraint_name = rc.constraint_name
                    WHERE kcu.column_name = 'tenant_id'
                        AND rc.delete_rule <> 'CASCADE'
                    """
                )
            ).all()
            non_cascade = [row[0] for row in rows]
            assert not non_cascade, (
                f"Tables with tenant_id FK NOT using CASCADE: {non_cascade}"
            )
    finally:
        engine.dispose()


def test_tenant_mixin_model_has_fk_declaration():
    """The TenantMixin in base.py must declare the FK so every inheriting
    model gets it by default. Verified via a concrete model that inherits
    only TenantMixin + UUIDMixin (no override)."""
    from app.models.notification_rule import NotificationRule

    col = NotificationRule.__table__.columns.get("tenant_id")
    assert col is not None, (
        "NotificationRule (TenantMixin inheritor) must have tenant_id"
    )
    fks = list(col.foreign_keys)
    assert len(fks) == 1, (
        f"NotificationRule.tenant_id must have exactly one FK (from TenantMixin), got {len(fks)}"
    )
    fk = fks[0]
    assert fk.column.table.name == "tenants", (
        f"FK target must be tenants.id, got {fk.column.table.name}"
    )
    assert fk.ondelete == "CASCADE", (
        f"FK must use ON DELETE CASCADE, got ondelete={fk.ondelete}"
    )


def test_telemetry_model_overrides_without_fk():
    """TelemetryDataModel must override tenant_id WITHOUT an FK."""
    from app.models.telemetry_model import TelemetryDataModel

    col = TelemetryDataModel.__table__.columns["tenant_id"]
    fks = list(col.foreign_keys)
    assert len(fks) == 0, (
        f"TelemetryDataModel.tenant_id must NOT have an FK (hypertable), "
        f"got {len(fks)}"
    )
