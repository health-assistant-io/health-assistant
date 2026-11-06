"""The family instance-status surface (ADR-0040 D7/D21 — the vocabulary
is family law, the persistence stays health-owned).

Every surface that renders an instance's sync state speaks ONE shape:

* ``status`` — ``ok | degraded | failing | stale``
* ``last_success_at`` — RFC3339 timestamp of the last successful sync
* ``consecutive_failures`` — integer >= 0
* ``conditions`` — ``["needs-config"]`` when the stored config no longer
  validates against the extension's manifest configSchema (never silently
  defaulted)

Derivation (host-profile policy — cooldowns, cadence and thresholds stay
health's): ``failing`` after two consecutive failed syncs, ``degraded``
after one, ``ok`` when the last sync succeeded and is fresh, ``stale``
when the last success is older than the staleness window (default 24h —
generous by design; the beat retries far sooner and cooldowns explain
longer gaps only up to their clamp).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

STALENESS_WINDOW = timedelta(hours=24)

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_FAILING = "failing"
STATUS_STALE = "stale"


def _needs_config(integration: Any) -> bool:
    """The stored config must still satisfy the manifest's required
    configSchema fields (D21 — invalid after an update is a surfaced
    condition, never a silent default)."""
    from app.extensions.registry import integration_registry

    manifest = integration_registry.get_manifest(integration.provider) or {}
    required = ((manifest.get("configSchema") or {}).get("required")) or []
    config = integration.user_config or {}
    return any(field not in config or config[field] in (None, "") for field in required)


def instance_status_surface(
    integration: Any,
    sync_logs: list[Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute the family status surface for one instance.

    ``sync_logs`` are the instance's log rows ordered most-recent-first
    (the detail endpoint already fetches them); pass ``[]`` when none
    exist (the instance simply has no sync history yet — ``ok`` with no
    ``last_success_at``).
    """
    now = now or datetime.now(UTC)
    conditions: list[str] = []
    if _needs_config(integration):
        conditions.append("needs-config")

    last_success_at: datetime | None = None
    consecutive_failures = 0
    for log in sync_logs:
        if getattr(log, "status", None) == "success":
            started = log.started_at
            last_success_at = started if last_success_at is None else last_success_at
            break
        consecutive_failures += 1

    if consecutive_failures >= 2:
        status = STATUS_FAILING
    elif consecutive_failures == 1:
        status = STATUS_DEGRADED
    elif last_success_at is None:
        status = STATUS_OK
    else:
        started = last_success_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        status = STATUS_STALE if (now - started) > STALENESS_WINDOW else STATUS_OK

    surface: dict[str, Any] = {
        "status": status,
        "last_success_at": last_success_at.isoformat() if last_success_at else None,
        "consecutive_failures": consecutive_failures,
    }
    if conditions:
        surface["conditions"] = conditions
    return surface
