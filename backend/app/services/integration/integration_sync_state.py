"""First-class sync cursors and sync health (plan 8 W1/W3).

One truth per concern: ``user_integrations.user_config`` keeps configuration;
cursors and sync health are rows in ``integration_sync_state``. This module is
the only reader/writer of that table.

Providers stay synchronous: the engine (and every other boundary that runs a
provider) calls :func:`hydrate` once, the provider's ``get_cursor`` /
``set_cursor`` calls hit an in-memory buffer attached to the ORM instance, and
:func:`flush` persists the dirty entries in the caller's transaction. The
buffer is a per-request cache, never a second store — nothing reads a cursor
that was not hydrated from a row, and nothing is durable until ``flush``.

Backoff is data: :func:`record_failure` writes ``next_retry_at`` from the
provider's ``Retry-After`` (when supplied) or exponential backoff, whichever
is later; :func:`backoff_gate` answers the beat's "should this integration be
skipped right now?" question from the rows.
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    IntegrationCursorKind,
    IntegrationSyncErrorClass,
    IntegrationSyncStream,
)
from app.models.user_integration import IntegrationSyncState

logger = logging.getLogger(__name__)

CACHE_ATTR = "_integration_sync_state"

#: First retry delay; doubles per consecutive failure.
BASE_BACKOFF_SECONDS = 60
#: Upper bound for a computed retry delay (a provider may still ask for more
#: via ``Retry-After``; the caller's value wins when it is larger).
MAX_BACKOFF_SECONDS = 3600


@dataclass
class _Entry:
    stream: str
    cursor_key: str
    cursor_kind: str
    cursor_value: str | None
    dirty: bool = False


def _stream_value(stream: IntegrationSyncStream | str) -> str:
    return stream.value if isinstance(stream, IntegrationSyncStream) else str(stream)


def _kind_value(kind: IntegrationCursorKind | str) -> str:
    return kind.value if isinstance(kind, IntegrationCursorKind) else str(kind)


def _encode(value: Any, kind: str) -> str | None:
    if value is None:
        return None
    if kind == IntegrationCursorKind.COMMIT.value:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if isinstance(value, datetime):
        return value.isoformat()
    if kind == IntegrationCursorKind.TIMESTAMP.value:
        return str(value)
    return str(value)


def _decode(raw: str | None, kind: str) -> Any:
    if raw is None:
        return None
    if kind == IntegrationCursorKind.COMMIT.value:
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw
    return raw


def _cache(integration: Any) -> dict[tuple[str, str], _Entry]:
    cache = getattr(integration, CACHE_ATTR, None)
    if cache is None:
        cache = {}
        try:
            setattr(integration, CACHE_ATTR, cache)
        except AttributeError:  # pragma: no cover - exotic stand-ins
            return cache
    return cache


def _key(stream: IntegrationSyncStream | str, cursor_key: str | None) -> tuple[str, str]:
    return (_stream_value(stream), cursor_key or "")


async def hydrate(db: AsyncSession, integration: Any) -> None:
    """Load this integration's sync-state rows into the instance buffer.

    Idempotent per instance: a boundary that hydrates twice pays one query.
    """
    if getattr(integration, CACHE_ATTR, None) is not None:
        return
    if getattr(integration, "id", None) is None:
        return
    result = await db.execute(
        select(IntegrationSyncState).where(
            IntegrationSyncState.user_integration_id == integration.id
        )
    )
    cache: dict[tuple[str, str], _Entry] = {}
    for row in result.scalars().all():
        stream = _stream_value(row.stream)
        cache[(stream, row.cursor_key)] = _Entry(
            stream=stream,
            cursor_key=row.cursor_key,
            cursor_kind=_kind_value(row.cursor_kind),
            cursor_value=row.cursor_value,
        )
    with contextlib.suppress(AttributeError):  # pragma: no cover - stand-ins
        setattr(integration, CACHE_ATTR, cache)


def get_cursor(
    integration: Any,
    stream: IntegrationSyncStream | str,
    *,
    key: str | None = None,
    default: Any = None,
) -> Any:
    """Return the buffered cursor value (``None``/absent → ``default``)."""
    entry = _cache(integration).get(_key(stream, key))
    if entry is None:
        return default
    decoded = _decode(entry.cursor_value, entry.cursor_kind)
    return default if decoded is None else decoded


def set_cursor(
    integration: Any,
    stream: IntegrationSyncStream | str,
    value: Any,
    *,
    key: str | None = None,
    kind: IntegrationCursorKind | str = IntegrationCursorKind.TIMESTAMP,
) -> None:
    """Buffer a cursor write for the next :func:`flush`."""
    stream_value = _stream_value(stream)
    kind_value = _kind_value(kind)
    cache = _cache(integration)
    cache[_key(stream_value, key)] = _Entry(
        stream=stream_value,
        cursor_key=key or "",
        cursor_kind=kind_value,
        cursor_value=_encode(value, kind_value),
        dirty=True,
    )


def clear_cursor(
    integration: Any,
    stream: IntegrationSyncStream | str,
    *,
    key: str | None = None,
) -> None:
    """Reset a cursor to empty (the next pull starts from the beginning)."""
    entry = _cache(integration).get(_key(stream, key))
    kind = entry.cursor_kind if entry else IntegrationCursorKind.TIMESTAMP.value
    set_cursor(integration, stream, None, key=key, kind=kind)


def clear_all(integration: Any) -> list[str]:
    """Reset every buffered cursor; returns the ``stream[:key]`` labels cleared."""
    cache = getattr(integration, CACHE_ATTR, None) or {}
    cleared: list[str] = []
    for entry in cache.values():
        label = f"{entry.stream}:{entry.cursor_key}" if entry.cursor_key else entry.stream
        cleared.append(label)
        entry.cursor_value = None
        entry.dirty = True
    return sorted(cleared)


async def flush(db: AsyncSession, integration: Any) -> int:
    """Persist every dirty buffered cursor as an upsert; returns rows touched."""
    cache = getattr(integration, CACHE_ATTR, None) or {}
    dirty = [entry for entry in cache.values() if entry.dirty]
    if not dirty:
        return 0
    for entry in dirty:
        values = {
            "tenant_id": integration.tenant_id,
            "user_integration_id": integration.id,
            "stream": entry.stream,
            "cursor_key": entry.cursor_key,
            "cursor_kind": entry.cursor_kind,
            "cursor_value": entry.cursor_value,
        }
        stmt = (
            pg_insert(IntegrationSyncState)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_integration_sync_state_stream_cursor",
                set_={
                    "cursor_kind": values["cursor_kind"],
                    "cursor_value": values["cursor_value"],
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        await db.execute(stmt)
        entry.dirty = False
    await db.flush()
    return len(dirty)


def backoff_seconds(consecutive_failures: int) -> int:
    """Exponential backoff for a failure streak (data, not code state)."""
    exponent = max(int(consecutive_failures) - 1, 0)
    return min(BASE_BACKOFF_SECONDS * (2**exponent), MAX_BACKOFF_SECONDS)


async def restore_identity(db: AsyncSession, integration: Any) -> None:
    """Re-load the instance's identity after a rollback expired the object.

    A failed sync path must still write typed rows (failure log + health), but
    the rollback expires every ORM attribute and async lazy IO is illegal
    outside a greenlet. One refresh restores the ids the writers need.
    """
    with contextlib.suppress(Exception):
        await db.refresh(integration)


async def _rows(db: AsyncSession, integration: Any) -> list[IntegrationSyncState]:
    result = await db.execute(
        select(IntegrationSyncState).where(
            IntegrationSyncState.user_integration_id == integration.id
        )
    )
    return list(result.scalars().all())


async def record_success(
    db: AsyncSession,
    integration: Any,
    stream: IntegrationSyncStream | str,
    *,
    key: str | None = None,
    now: datetime | None = None,
) -> None:
    """Stamp a successful pull on one stream: heal the row, reset the streak.

    Also persists any buffered cursor for the same ``(stream, key)`` pair, so
    "the cursor advanced" and "the pull succeeded" are one atomic fact.
    """
    now = now or datetime.now(UTC)
    stream_value = _stream_value(stream)
    entry = _cache(integration).get((stream_value, key or ""))
    values: dict[str, Any] = {
        "tenant_id": integration.tenant_id,
        "user_integration_id": integration.id,
        "stream": stream_value,
        "cursor_key": key or "",
        "cursor_kind": (entry.cursor_kind if entry else IntegrationCursorKind.TIMESTAMP.value),
        "cursor_value": (entry.cursor_value if entry else None),
        "last_success_at": now,
        "last_error_at": None,
        "last_error_class": None,
        "consecutive_failures": 0,
        "next_retry_at": None,
        "updated_at": now,
    }
    stmt = (
        pg_insert(IntegrationSyncState)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_integration_sync_state_stream_cursor",
            set_={
                "cursor_kind": values["cursor_kind"],
                "cursor_value": values["cursor_value"],
                "last_success_at": now,
                "last_error_at": None,
                "last_error_class": None,
                "consecutive_failures": 0,
                "next_retry_at": None,
                "updated_at": now,
            },
        )
    )
    await db.execute(stmt)
    if entry is not None:
        entry.dirty = False


async def record_failure(
    db: AsyncSession,
    integration: Any,
    error_class: IntegrationSyncErrorClass | str,
    *,
    retry_after_seconds: float | None = None,
    stream: IntegrationSyncStream | str | None = None,
    now: datetime | None = None,
) -> datetime:
    """Record a typed failure on every stream row; returns ``next_retry_at``.

    ``Retry-After`` (when the upstream sent one) wins over the computed
    exponential delay; the delay is never shorter than the backoff for the
    streak. A first failure creates a default ``observations`` row so the
    scheduler always has a retry gate to read.
    """
    now = now or datetime.now(UTC)
    error_value = (
        error_class.value
        if isinstance(error_class, IntegrationSyncErrorClass)
        else str(error_class)
    )
    rows = await _rows(db, integration)
    if stream is not None:
        stream_value = _stream_value(stream)
        rows = [row for row in rows if _stream_value(row.stream) == stream_value] or [
            IntegrationSyncState(
                tenant_id=integration.tenant_id,
                user_integration_id=integration.id,
                stream=stream_value,
                cursor_key="",
            )
        ]
    elif not rows:
        rows = [
            IntegrationSyncState(
                tenant_id=integration.tenant_id,
                user_integration_id=integration.id,
                stream=IntegrationSyncStream.OBSERVATIONS.value,
                cursor_key="",
            )
        ]

    next_retry_at = now
    for row in rows:
        failures = int(row.consecutive_failures or 0) + 1
        delay = float(backoff_seconds(failures))
        if retry_after_seconds is not None:
            delay = max(delay, float(retry_after_seconds))
        row.consecutive_failures = failures
        row.last_error_at = now
        row.last_error_class = error_value
        row.next_retry_at = now + timedelta(seconds=delay)
        next_retry_at = max(next_retry_at, row.next_retry_at)
        if row.id is None:
            db.add(row)
    # A failed run must not advance cursors: drop any buffered (unflushed)
    # cursor writes so a retry resumes where the last success left off.
    for entry in (getattr(integration, CACHE_ATTR, None) or {}).values():
        entry.dirty = False
    await db.flush()
    return next_retry_at


async def backoff_gate(
    db: AsyncSession,
    integration_ids: list[UUID],
    *,
    now: datetime | None = None,
) -> dict[UUID, datetime]:
    """Map integration id → earliest ``next_retry_at`` still in the future."""
    if not integration_ids:
        return {}
    now = now or datetime.now(UTC)
    result = await db.execute(
        select(
            IntegrationSyncState.user_integration_id,
            IntegrationSyncState.next_retry_at,
        ).where(
            IntegrationSyncState.user_integration_id.in_(integration_ids),
            IntegrationSyncState.next_retry_at.is_not(None),
            IntegrationSyncState.next_retry_at > now,
        )
    )
    gate: dict[UUID, datetime] = {}
    for integration_id, next_retry_at in result.all():
        current = gate.get(integration_id)
        if current is None or next_retry_at < current:
            gate[integration_id] = next_retry_at
    return gate


async def stream_health(db: AsyncSession, integration: Any) -> list[dict[str, Any]]:
    """Read-model projection: cursor + health per stream row."""
    rows = await _rows(db, integration)
    return [
        {
            "stream": _stream_value(row.stream),
            "cursor_key": row.cursor_key,
            "cursor_kind": _kind_value(row.cursor_kind),
            "cursor_value": row.cursor_value,
            "last_success_at": row.last_success_at,
            "last_error_at": row.last_error_at,
            "last_error_class": (
                row.last_error_class.value
                if isinstance(row.last_error_class, IntegrationSyncErrorClass)
                else row.last_error_class
            ),
            "consecutive_failures": row.consecutive_failures,
            "next_retry_at": row.next_retry_at,
        }
        for row in rows
    ]
