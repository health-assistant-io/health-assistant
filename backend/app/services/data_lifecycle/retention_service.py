"""Retention jobs read policy rows — the one source of retention windows.

`retention_policies` (seeded by :mod:`app.services.data_lifecycle.retention_policies`) is the
policy; this module is its only executor. Two shapes:

* **table rows** win over class rows, even when disabled — an operator's
  "no pruning for this table" is a decision, not a fallback;
* **class rows** apply to every table of that lifecycle class without its own
  row.

The cutoff is always ``now - days`` compared against the row's declared
``timestamp_column``; jobs never embed a constant. ``delete`` removes rows,
``anonymize`` keeps the row and clears its JSONB diff payload (the audit
retention shape). Two deliberate adapters exist:

* ``proposals`` prunes terminal rows only — a pending proposal is work in
  flight, not history;
* ``audit_events`` anonymizes its diff columns instead of deleting
  the proof-of-existence row.

Orphan cleanup (rows whose owning tenant/patient/user is gone) is a separate
named job: :func:`app.services.data_lifecycle.retention_service.purge_orphaned_lifecycle_rows`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_lifecycle import LifecycleClass, operational_tables, transient_tables
from app.models.base import Base
from app.models.data_lifecycle_model import RetentionPolicy
from app.models.enums import ProposalStatus, RetentionAction, RetentionScope

logger = logging.getLogger(__name__)

#: Runtime-managed tables with a retention row but no mapped model.
CHECKPOINT_TABLE = "checkpoints"

#: Where a policy's row payload lives for the ``anonymize`` action.
_ANONYMIZE_COLUMNS = ("old_value", "new_value")  # gate-allow: *, audit diff


@dataclass(frozen=True)
class EffectivePolicy:
    """One table's resolved retention rule."""

    table: str
    action: RetentionAction
    days: int
    timestamp_column: str
    enabled: bool
    source: str
    policy_id: str | None

    def cutoff(self, now: datetime | None = None) -> datetime:
        return (now or datetime.now(UTC)) - timedelta(days=self.days)


def default_cutoff(days: int, now: datetime | None = None) -> datetime:
    """``now - days`` — the single cutoff formula for every job."""
    return (now or datetime.now(UTC)) - timedelta(days=days)


async def load_policies(db: AsyncSession) -> list[RetentionPolicy]:
    result = await db.execute(select(RetentionPolicy).order_by(RetentionPolicy.scope))
    return list(result.scalars().all())


def _policy_lookup(policies: list[RetentionPolicy]) -> dict[tuple[str, str], RetentionPolicy]:
    return {(policy.scope.value, policy.scope_value): policy for policy in policies}


async def effective_policy(
    db: AsyncSession, table: str, lifecycle_class: LifecycleClass | str
) -> EffectivePolicy:
    """Resolve one table's rule: table row first, then class row."""
    policies = await load_policies(db)
    lookup = _policy_lookup(policies)
    table_row = lookup.get((RetentionScope.TABLE.value, table))
    if table_row is not None:
        return EffectivePolicy(
            table=table,
            action=table_row.action,
            days=table_row.days,
            timestamp_column=table_row.timestamp_column,
            enabled=table_row.enabled,
            source=RetentionScope.TABLE.value,
            policy_id=str(table_row.id),
        )
    class_row = lookup.get((RetentionScope.CLASS.value, LifecycleClass(lifecycle_class).value))
    if class_row is None:
        raise LookupError(f"no retention policy covers table {table!r}")
    return EffectivePolicy(
        table=table,
        action=class_row.action,
        days=class_row.days,
        timestamp_column=class_row.timestamp_column,
        enabled=class_row.enabled,
        source=RetentionScope.CLASS.value,
        policy_id=str(class_row.id),
    )


async def policy_days(
    db: AsyncSession, *, table: str, scope_value: str | None = None, default: int
) -> int:
    """Read one table policy's ``days`` (used by adapters like checkpoint prune)."""
    result = await db.execute(
        select(RetentionPolicy).where(
            RetentionPolicy.scope == RetentionScope.TABLE,
            RetentionPolicy.scope_value == (scope_value or table),
        )
    )
    row = result.scalar_one_or_none()
    if row is None or not row.enabled:
        return default
    return row.days


def managed_tables() -> tuple[str, ...]:
    """Every mapped table bound to a retention class (operational + transient)."""
    return tuple(sorted(operational_tables() | transient_tables()))


def _timestamp_column_available(table: str, column: str) -> bool:
    model_table = Base.metadata.tables.get(table)
    return model_table is not None and column in model_table.columns


async def apply_policy(
    db: AsyncSession, policy: EffectivePolicy, *, now: datetime | None = None
) -> dict[str, Any]:
    """Execute one resolved policy; returns ``{table, action, days, rows}``."""
    out: dict[str, Any] = {
        "table": policy.table,
        "action": policy.action.value,
        "days": policy.days,
        "rows": 0,
        "skipped": None,
    }
    if not policy.enabled:
        out["skipped"] = "disabled"
        return out
    if policy.table == CHECKPOINT_TABLE:
        out["skipped"] = "runtime-managed (checkpoint prune job)"
        return out
    model_table = Base.metadata.tables.get(policy.table)
    if model_table is None:
        out["skipped"] = "not a mapped table"
        return out
    if not _timestamp_column_available(policy.table, policy.timestamp_column):
        out["skipped"] = f"missing timestamp column {policy.timestamp_column!r}"
        logger.warning("retention policy %s skipped: %s", policy.table, out["skipped"])
        return out

    column = model_table.columns[policy.timestamp_column]
    cutoff = policy.cutoff(now)

    if policy.action is RetentionAction.ANONYMIZE:
        values = {name: None for name in _ANONYMIZE_COLUMNS if name in model_table.columns}
        if not values:
            out["skipped"] = "no anonymizable payload columns"
            return out
        result = await db.execute(update(model_table).where(column < cutoff).values(**values))
        out["rows"] = result.rowcount or 0
        return out

    statement = delete(model_table).where(column < cutoff)
    if policy.table == "proposals" and "status" in model_table.columns:
        statement = statement.where(
            model_table.columns["status"].in_([s.value for s in ProposalStatus.terminal()])
        )
    result = await db.execute(statement)
    out["rows"] = result.rowcount or 0
    return out


async def run_retention(db: AsyncSession, *, now: datetime | None = None) -> dict[str, Any]:
    """Apply every enabled policy for the operational + transient classes.

    One transaction; returns per-table results plus a total. Tables with a
    disabled table-level row are reported as skipped, never processed by the
    class fallback.
    """
    class_by_table: dict[str, LifecycleClass] = dict.fromkeys(
        operational_tables(), LifecycleClass.OPERATIONAL
    )
    class_by_table.update(dict.fromkeys(transient_tables(), LifecycleClass.TRANSIENT))
    results: list[dict[str, Any]] = []
    for table in sorted(class_by_table):
        policy = await effective_policy(db, table, class_by_table[table])
        results.append(await apply_policy(db, policy, now=now))
    await db.commit()
    return {
        "tables": results,
        "total_rows": sum(item["rows"] for item in results),
    }


# ---------------------------------------------------------------------------
# Orphan cleanup (the base.py docstring's promise, made code)
# ---------------------------------------------------------------------------


async def _delete_orphans(
    db: AsyncSession,
    *,
    table: str,
    owner_column: str,
    owner_table: str,
    extra: str = "",
) -> int:
    """Delete rows whose ``owner_column`` has no row in ``owner_table``."""
    sql = (
        f"DELETE FROM {table} t WHERE NOT EXISTS ("
        f"SELECT 1 FROM {owner_table} o WHERE o.id = t.{owner_column})"
    )
    if extra:
        sql = f"{sql} AND {extra}"
    result = await db.execute(text(sql))
    return result.rowcount or 0


async def purge_orphaned_lifecycle_rows(
    db: AsyncSession | None = None,
) -> dict[str, int]:
    """Purge rows whose owning tenant/patient/user no longer exists.

    Hypertable rows carry no FK (TimescaleDB limitation), so deleting a tenant
    or patient leaves them behind; push registrations and device rows have the
    same restore-time risk. This is the named, scheduled job that keeps the
    ``TenantMixin``/hypertable cleanup contract true. Idempotent.
    """
    from app.core.database import AsyncSessionLocal

    async def _run(session: AsyncSession) -> dict[str, int]:
        counts: dict[str, int] = {}
        counts["telemetry_data_tenant"] = await _delete_orphans(
            session,
            table="telemetry_data",
            owner_column="tenant_id",
            owner_table="tenants",
        )
        counts["telemetry_data_patient"] = await _delete_orphans(
            session,
            table="telemetry_data",
            owner_column="patient_id",
            owner_table="fhir_patients",
        )
        counts["telemetry_anomalies_tenant"] = await _delete_orphans(
            session,
            table="telemetry_anomalies",
            owner_column="tenant_id",
            owner_table="tenants",
        )
        counts["telemetry_devices_tenant"] = await _delete_orphans(
            session,
            table="telemetry_devices",
            owner_column="tenant_id",
            owner_table="tenants",
        )
        counts["notification_subscriptions_user"] = await _delete_orphans(
            session,
            table="notification_subscriptions",
            owner_column="user_id",
            owner_table="users",
        )
        counts["mobile_push_targets_user"] = await _delete_orphans(
            session,
            table="mobile_push_targets",
            owner_column="user_id",
            owner_table="users",
        )
        await session.commit()
        return counts

    if db is not None:
        return await _run(db)
    async with AsyncSessionLocal() as session:
        return await _run(session)


def count_rows(model_table, *criteria) -> Any:
    """Small helper for adapters/tests: a ``COUNT(*)`` select for a model table."""
    return select(func.count()).select_from(model_table).where(*criteria)


def table_has_timestamp(table: str, column: str) -> bool:
    """Public probe used by the policy-coverage gate test."""
    return _timestamp_column_available(table, column)


def model_columns(table: str) -> set[str]:
    """Public column probe used by the policy-coverage gate test."""
    model_table = Base.metadata.tables.get(table)
    if model_table is None:
        return set()
    return {column.name for column in sa_inspect(model_table).columns}
