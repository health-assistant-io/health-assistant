"""Seeded retention-policy defaults (constitution rule 10 — policy as data).

`retention_policies` rows are the one source of retention windows. This module
owns the default set and the idempotent boot seeder, mirroring the other seeded
lookups (`ai_task_defs`, `proposal_kinds`):

* ids are deterministic (uuid5 over ``scope/scope_value``) so a row is
  derivable without a lookup and seeding is reproducible;
* seeding **inserts missing rows only** — an operator edit to ``days``,
  ``action`` or ``enabled`` survives the next boot;
* every ``operational``/``transient`` table in the lifecycle registry is
  covered: the class rows are the fallback, the table rows are overrides.

Disabled rows are deliberate: registry-bounded vocabularies
(``ai_task_defs``, ``proposal_kinds``, ``biomarker_allowed_states``) and the
policy table itself carry no pruning window, and the disabled row records that
decision instead of leaving the table unclassified.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_lifecycle_model import RetentionPolicy
from app.models.enums import RetentionAction, RetentionScope

RETENTION_NAMESPACE = uuid5(NAMESPACE_URL, "https://neuronection.com/data/retention-policies")

#: ``(scope, scope_value, action, days, timestamp_column, enabled)``.
#:
#: The first two rows are the class fallbacks; every remaining row is a table
#: override. ``checkpoints`` is a runtime-managed table (LangGraph), not a
#: mapped model — the checkpoint-prune job reads its row directly.
RETENTION_DEFAULTS: tuple[tuple[str, str, str, int, str, bool], ...] = (
    (RetentionScope.CLASS, "operational", RetentionAction.DELETE, 90, "created_at", True),
    (RetentionScope.CLASS, "transient", RetentionAction.DELETE, 7, "created_at", True),
    (RetentionScope.TABLE, "ai_call_logs", RetentionAction.DELETE, 180, "started_at", True),
    (RetentionScope.TABLE, "ai_task_defs", RetentionAction.DELETE, 0, "created_at", False),
    (RetentionScope.TABLE, "audit_events", RetentionAction.ANONYMIZE, 3650, "created_at", True),
    (RetentionScope.TABLE, "auth_sessions", RetentionAction.DELETE, 1, "absolute_expires_at", True),
    (
        RetentionScope.TABLE,
        "biomarker_allowed_states",
        RetentionAction.DELETE,
        0,
        "created_at",
        False,
    ),
    (RetentionScope.TABLE, "catalog_audit_log", RetentionAction.DELETE, 3650, "created_at", True),
    (RetentionScope.TABLE, "checkpoints", RetentionAction.DELETE, 30, "updated_at", True),
    (RetentionScope.TABLE, "export_jobs", RetentionAction.DELETE, 365, "created_at", True),
    (RetentionScope.TABLE, "import_jobs", RetentionAction.DELETE, 365, "created_at", True),
    (RetentionScope.TABLE, "integration_debug_logs", RetentionAction.DELETE, 30, "timestamp", True),
    (
        RetentionScope.TABLE,
        "integration_sync_logs",
        RetentionAction.DELETE,
        180,
        "completed_at",
        True,
    ),
    (
        RetentionScope.TABLE,
        "integration_sync_state",
        RetentionAction.DELETE,
        365,
        "updated_at",
        True,
    ),
    (RetentionScope.TABLE, "mobile_push_targets", RetentionAction.DELETE, 365, "updated_at", True),
    (
        RetentionScope.TABLE,
        "notification_deliveries",
        RetentionAction.DELETE,
        180,
        "created_at",
        True,
    ),
    (
        RetentionScope.TABLE,
        "notification_recipients",
        RetentionAction.DELETE,
        365,
        "created_at",
        True,
    ),
    (
        RetentionScope.TABLE,
        "notification_subscriptions",
        RetentionAction.DELETE,
        365,
        "created_at",
        True,
    ),
    (RetentionScope.TABLE, "notifications", RetentionAction.DELETE, 180, "created_at", True),
    (RetentionScope.TABLE, "proposal_kinds", RetentionAction.DELETE, 0, "created_at", False),
    (RetentionScope.TABLE, "proposals", RetentionAction.DELETE, 90, "created_at", True),
    (RetentionScope.TABLE, "retention_policies", RetentionAction.DELETE, 0, "created_at", False),
    (RetentionScope.TABLE, "task_logs", RetentionAction.DELETE, 90, "created_at", True),
    (RetentionScope.TABLE, "telemetry_anomalies", RetentionAction.DELETE, 730, "detected_at", True),
    (RetentionScope.TABLE, "telemetry_data", RetentionAction.DELETE, 730, "timestamp", True),
    (RetentionScope.TABLE, "telemetry_devices", RetentionAction.DELETE, 3650, "created_at", True),
)


def retention_policy_id(scope: str, scope_value: str) -> UUID:
    """The deterministic id for one policy row."""
    return uuid5(RETENTION_NAMESPACE, f"retention_policies/{scope}/{scope_value}")


def policy_rows() -> list[dict]:
    """The defaults as ``retention_policies`` rows (idempotent seed input)."""
    return [
        {
            "id": retention_policy_id(scope, scope_value),
            "scope": scope,
            "scope_value": scope_value,
            "action": action,
            "days": days,
            "timestamp_column": timestamp_column,
            "enabled": enabled,
        }
        for scope, scope_value, action, days, timestamp_column, enabled in RETENTION_DEFAULTS
    ]


async def seed_retention_policies(db: AsyncSession, *, commit: bool = False) -> dict[str, int]:
    """Insert missing policy rows (operator edits are never overwritten)."""
    rows = policy_rows()
    existing = set((await db.execute(select(RetentionPolicy.id))).scalars().all())
    missing = [row for row in rows if row["id"] not in existing]
    if missing:
        await db.execute(pg_insert(RetentionPolicy).values(missing).on_conflict_do_nothing())
    if commit:
        await db.commit()
    return {
        "added": len(missing),
        "updated": 0,
        "skipped": len(rows) - len(missing),
        "errors": 0,
    }
