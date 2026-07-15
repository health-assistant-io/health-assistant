"""Shared value-conversion helpers (audit C8).

``_uuid`` / ``_now`` / ``_parse_dt`` were re-implemented across five service
modules (notification_service, notification_targets, notification_rule_service,
export_service, import_service, integration_sync_service). They are
centralised here; the service modules import the public names (aliased back to
their private ``_uuid`` / ``_now`` / ``_parse_dt`` so no call site changes).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID


def to_uuid(value: Any) -> Optional[UUID]:
    """Coerce a str/UUID/None into a ``UUID`` (or ``None`` if not coercible)."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def utcnow() -> datetime:
    """Timezone-aware UTC now — the project's canonical "now"."""
    return datetime.now(timezone.utc)


def parse_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string into a timezone-aware ``datetime``.

    Falsy values and existing ``datetime`` instances pass through unchanged
    (matching the historical per-module helpers); a trailing ``Z`` is
    normalised to ``+00:00`` for ``fromisoformat``. Returns ``None`` on any
    parse failure.
    """
    if not value or isinstance(value, datetime):
        return value
    try:
        s = str(value)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
