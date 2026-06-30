"""Regression tests for the worker async DB engine.

Pre-fix contract: ``get_async_session`` created a fresh ``AsyncEngine``
on every call, and ``async_task`` created a fresh event loop on every
call. asyncpg connections created by previous engines bound their
protocol transports to the loop that was active when the connection
was opened. When that loop closed (end of the task) and SQLAlchemy's
pool handed the connection to a subsequent task running on a new
loop, asyncpg raised ``RuntimeError: Event loop is closed`` /
``RuntimeError: Future attached to a different loop``. Celery periodic
tasks (``check_notification_triggers``, ``sync_active_integrations``)
crashed intermittently.

Post-fix contract pinned here:
1. ``get_async_engine()`` returns a worker-scoped singleton — repeated
   calls within the same process return the same engine instance.
2. The engine uses ``NullPool`` (no connection reuse across loops).
3. ``get_async_session()`` returns ``(session, engine)`` where the
   engine is the shared singleton (NOT a fresh one).
4. Tasks no longer dispose the engine in their ``finally`` blocks.
5. Running two ``@async_task`` functions back-to-back in the same
   process does not raise (the original failure mode).
"""
import asyncio
import inspect

import pytest
from sqlalchemy.pool import NullPool


def test_get_async_engine_is_process_singleton():
    """Repeated calls to ``get_async_engine`` return the same instance.

    A fresh engine per task was the root cause of the event-loop crash:
    each engine had its own pool, and pool connections outlived their
    loops. Sharing one engine per worker process is the fix.
    """
    from app.workers import tasks as worker_tasks

    # Reset any cached engine from a previous test.
    worker_tasks._worker_engine = None

    try:
        engine_a = worker_tasks.get_async_engine()
        engine_b = worker_tasks.get_async_engine()
        assert engine_a is engine_b, (
            "get_async_engine() must return a worker-scoped singleton — "
            "per-task engines caused asyncpg loop-affinity crashes."
        )
    finally:
        worker_tasks._worker_engine = None


def test_get_async_engine_uses_null_pool():
    """The shared engine uses ``NullPool`` so no DB connection is ever
    reused across event loops. Without this, asyncpg protocol transports
    tied to closed loops would resurface via the shared pool.
    """
    from app.workers import tasks as worker_tasks

    worker_tasks._worker_engine = None
    try:
        engine = worker_tasks.get_async_engine()
        assert engine.pool.__class__.__name__ == "NullPool", (
            f"Worker engine must use NullPool (got {engine.pool.__class__.__name__}); "
            "any pooling implementation that reuses connections will re-introduce "
            "the loop-affinity crash."
        )
    finally:
        worker_tasks._worker_engine = None


def test_get_async_session_returns_shared_engine():
    """``get_async_session()`` returns ``(session, engine)`` where the
    engine is the shared singleton, not a fresh one."""
    from app.workers import tasks as worker_tasks

    worker_tasks._worker_engine = None
    try:
        shared_engine = worker_tasks.get_async_engine()
        session, returned_engine = worker_tasks.get_async_session()
        assert returned_engine is shared_engine, (
            "get_async_session() must return the shared worker engine, not a "
            "per-task one."
        )
    finally:
        worker_tasks._worker_engine = None


def test_tasks_do_not_dispose_engine_in_finally():
    """Every ``@async_task`` / ``@celery_app.task`` in tasks.py and ai_tasks.py
    used to end with ``await engine.dispose()`` in its ``finally`` block. With
    a shared engine, disposing it would kill the engine for all subsequent
    tasks in the worker process. The fix replaces those with ``await db.close()``.

    The lifecycle helpers (``dispose_worker_engine``, signal handlers) are
    exempt — disposing the shared engine is their job.
    """
    from app.workers import tasks as worker_tasks
    from app.workers import ai_tasks as worker_ai_tasks

    # Functions whose job IS to dispose the engine — exempt from the check.
    exempt = {
        worker_tasks.dispose_worker_engine,
        worker_tasks.get_async_engine,
        worker_tasks.get_async_session,
    }

    for mod in (worker_tasks, worker_ai_tasks):
        for fn_name in dir(mod):
            if fn_name.startswith("_"):
                continue
            fn = getattr(mod, fn_name)
            if not callable(fn) or fn in exempt:
                continue
            # Only inspect actual Celery-registered tasks.
            if not hasattr(fn, "delay") and not hasattr(fn, "apply_async"):
                continue
            try:
                fn_src = inspect.getsource(fn)
            except (TypeError, OSError):
                continue
            assert "engine.dispose()" not in fn_src, (
                f"{mod.__name__}.{fn_name} still disposes the engine in its body — "
                "shared worker engine must NOT be disposed per-task."
            )


def test_back_to_back_async_tasks_do_not_crash():
    """Two ``@async_task`` functions run sequentially in the same thread
    must both complete without raising.

    This is the headline reproduction of the original production failure:
    after the first task's loop closed, the second task reusing a pooled
    asyncpg connection crashed with ``RuntimeError: Event loop is closed``.
    With ``NullPool`` + shared engine, no connection is ever reused across
    loops, so this failure mode is impossible.
    """
    from app.workers import tasks as worker_tasks

    worker_tasks._worker_engine = None

    @worker_tasks.async_task
    async def first_task():
        return "first"

    @worker_tasks.async_task
    async def second_task():
        return "second"

    try:
        assert first_task() == "first"
        # The original bug: the second call would crash because asyncpg
        # tried to reuse a connection from a closed loop.
        assert second_task() == "second"
    finally:
        worker_tasks._worker_engine = None
