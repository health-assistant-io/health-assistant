"""Readiness probe (``/ready``, plan 64 §1.4 / ADR-0062 v7 §4).

Liveness (``/health``) answers "is the process up"; readiness answers "may
ingress send traffic". Mounted at the root in ``app.main`` (outside
``/api/v1``, unauthenticated by construction: the session enforcement and
route-policy gate only ever touch ``/api/*`` paths).

Severity model (plan 64 §1.4):

**Hard checks** — any failure is a **503**:

- ``boot`` — the lifespan startup completed (``app.state.boot_complete``);
- ``db`` — the database is reachable **and at this release's Alembic head**
  (the one-shot migrate job finished; a half-migrated instance must never
  take traffic);
- ``storage`` — *required* object storage answers (a cloud profile or
  ``HA_OBJECT_STORAGE_REQUIRED=true``). Optional storage only degrades and
  never flips the status (same degrade-open semantics as ``/health``).

**Soft signals** — reported in ``soft``, never flip the status: Redis
reachability, Celery worker liveness, broker queue backlog. Jobs delay;
requests keep flowing, so ingress stays on.

Negative tests: ``tests/test_ready_endpoint.py`` (DB down → 503, behind
head → 503, boot incomplete → 503, required storage down → 503, Redis down
→ still 200).
"""

from __future__ import annotations

import contextlib
import time
from functools import lru_cache
from typing import Any

import anyio
from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.storage.health import STATUS_UNAVAILABLE

router = APIRouter(tags=["system"])

# The soft probes are cached: an ingress hitting /ready every few seconds
# must not turn into a celery `inspect ping` broadcast every time.
SOFT_TTL_SECONDS = 30.0
SOFT_PING_TIMEOUT_SECONDS = 2.0
_soft_cache: dict[str, Any] = {"at": 0.0, "workers": "unknown", "queue_backlog": None}


async def db_revision() -> str | None:
    """The migration head the database reports — read from the
    ``migration_state`` marker, NOT ``alembic_version``.

    ``0016_rls`` revokes ``alembic_version`` from the runtime role (the app
    must never read the version row), so every alembic run ends by writing
    the reached head into ``migration_state`` (app-readable; see
    ``alembic/env.py``). ``None`` when the marker is missing (fresh database
    without a migrate yet) or unreadable.

    Module-level so the negative tests can simulate a database outage (or a
    database behind the migration head) without a real server.
    """
    from app.core.database import DATABASE_AVAILABLE, engine

    if not DATABASE_AVAILABLE or engine is None:
        return None
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text("SELECT head_revision FROM migration_state WHERE id = 1")
                )
            ).first()
    except Exception:
        return None
    return str(row[0]) if row else None


@lru_cache(maxsize=1)
def head_revision() -> str:
    """The single Alembic head this release expects.

    Read from the migration files (which ship with the image), never from
    the database — an unreachable DB must not hide *which* revision the app
    expects.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    if head is None:  # pragma: no cover - the chain always has a head
        raise RuntimeError("alembic chain has no head")
    return head


async def _db_check() -> dict[str, Any]:
    revision = await db_revision()
    at_head = False
    if revision is not None:
        try:
            at_head = revision == head_revision()
        except Exception:  # pragma: no cover - alembic files ship with the image
            at_head = False
    detail = None if at_head else "db not reachable or not at this release's migration head"
    return {"ok": at_head, "revision": revision, "detail": detail}


def _storage_snapshot(app_state: Any) -> dict[str, Any]:
    """Sync (bounded) storage probe — runs on a worker thread via anyio."""
    probe = getattr(app_state, "storage_health", None)
    if probe is None:
        config = getattr(app_state, "storage_config", None)
        return {
            # No probe (lifespan not entered): only a hard failure when the
            # deployment declares storage required.
            "ok": not bool(getattr(config, "required", False)),
            "required": bool(getattr(config, "required", False)),
            "backend": getattr(config, "backend", "unknown"),
            "status": "unknown",
            "detail": None,
        }
    snapshot = probe.snapshot()
    required = bool(snapshot.required)
    return {
        # Hard only when object storage is required (a cloud profile or
        # OBJECT_STORAGE_REQUIRED=true); otherwise a degraded provider is a
        # soft signal that never flips the status (plan 64 §1.4).
        "ok": snapshot.status != STATUS_UNAVAILABLE or not required,
        "required": required,
        "backend": snapshot.backend,
        "status": snapshot.status,
        "detail": snapshot.detail,
    }


async def _soft_signals() -> dict[str, Any]:
    """Redis + Celery worker/queue state — reported, never gatekeeping."""
    from app.core.redis import redis_client

    try:
        redis_state = "connected" if await redis_client.ping() else "not_available"
    except Exception:
        redis_state = "not_available"

    now = time.monotonic()
    if now - _soft_cache["at"] > SOFT_TTL_SECONDS:
        # The celery inspect broadcast is a control-plane round-trip; keep it
        # on the cached path. The LLEN backlog read is cheap enough to run
        # every request (asyncio Redis client — stays on the event loop).
        workers = await anyio.to_thread.run_sync(_probe_workers)
        backlog = None
        with contextlib.suppress(Exception):
            backlog = int(await redis_client.llen("celery"))
        _soft_cache.update(at=now, workers=workers, queue_backlog=backlog)
    return {
        "redis": redis_state,
        "workers": _soft_cache["workers"],
        "queue_backlog": _soft_cache["queue_backlog"],
    }


def _probe_workers() -> str:
    """Celery worker liveness (soft, best-effort inspect broadcast)."""
    try:
        from app.workers.celery_app import celery_app

        ping = celery_app.control.inspect(timeout=SOFT_PING_TIMEOUT_SECONDS).ping()
        return "online" if ping else "no-reply"
    except Exception:
        return "unknown"


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    app_state = request.app.state
    checks = {
        "boot": {"ok": bool(getattr(app_state, "boot_complete", False)), "detail": None},
        "db": await _db_check(),
        "storage": await anyio.to_thread.run_sync(_storage_snapshot, app_state),
    }
    ready_ = all(check["ok"] for check in checks.values())
    body: dict[str, Any] = {
        "status": "ready" if ready_ else "not-ready",
        "checks": checks,
        # Soft signals never flip the status (plan 64 §1.4).
        "soft": await _soft_signals(),
    }
    if not ready_:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body
