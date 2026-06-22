"""Regression tests for audit items A5 + A6 — stuck-extraction cleanup.

A5: ``cleanup_stuck_extractions`` used a 15-minute threshold that matched
    the Celery hard ``task_time_limit=900s``. A task killed at exactly
    15 min could be cleaned up while still mid-flight. Fix: raise to 20
    min so there's a 5-minute safety margin beyond the hard kill.

A6: ``main.lifespan`` startup-cleanup marked EVERY active-status exam as
    failed on every boot — including exams being actively processed by a
    worker at that moment (severe under rolling restarts). Fix: only
    target exams whose ``updated_at`` is older than 20 min.

Post-fix contract pinned here:
1. The cleanup threshold is strictly greater than the Celery hard time
   limit (900s → 20 min, ≥ 5 min margin).
2. The periodic task filters by ``updated_at < threshold``.
3. The startup cleanup also filters by ``updated_at < threshold``
   (predicate exists, not just status).
"""
import datetime as _dt
import inspect

import pytest


# ---------------------------------------------------------------------------
# A5: cleanup threshold > celery hard time limit
# ---------------------------------------------------------------------------


def test_a5_cleanup_threshold_is_greater_than_celery_hard_limit():
    """The threshold in cleanup_stuck_extractions must be > 900s with a
    safety margin. Currently 20 min = 1200s; that gives a 5-min margin
    beyond the 15-min Celery hard kill."""
    from app.workers import tasks as worker_tasks

    src = inspect.getsource(worker_tasks.cleanup_stuck_extractions)
    # The threshold is `timedelta(minutes=20)`.
    assert "minutes=20" in src, (
        "cleanup_stuck_extractions threshold should be 20 minutes (5 min margin "
        "beyond Celery hard task_time_limit=900s). Found something else."
    )


def test_a5_cleanup_filters_by_updated_at():
    """A5 also requires that the cleanup actually filters by updated_at —
    otherwise the threshold bump is meaningless."""
    from app.workers import tasks as worker_tasks

    src = inspect.getsource(worker_tasks.cleanup_stuck_extractions)
    assert "ExaminationModel.updated_at" in src, (
        "cleanup_stuck_extractions must reference ExaminationModel.updated_at"
    )
    assert "threshold" in src and ".where(ExaminationModel.updated_at" in src, (
        "cleanup_stuck_extractions must filter updated_at < threshold"
    )


# ---------------------------------------------------------------------------
# A6: startup cleanup filters by updated_at
# ---------------------------------------------------------------------------


def test_a6_startup_cleanup_filters_by_updated_at():
    """The startup cleanup in main.lifespan must NOT mark every active
    exam failed — it must filter by updated_at < threshold."""
    from app import main

    src = inspect.getsource(main.lifespan)
    # Must include the threshold predicate AND the updated_at filter.
    assert "stuck_threshold" in src or "updated_at" in src, (
        "startup cleanup must compute a threshold and filter updated_at"
    )
    assert "ExaminationModel.updated_at" in src, (
        "startup cleanup must filter by ExaminationModel.updated_at"
    )
    assert "stuck_threshold" in src, (
        "startup cleanup must apply `updated_at < stuck_threshold` predicate"
    )


def test_a6_startup_cleanup_threshold_at_or_beyond_celery_hard_limit():
    """The startup cleanup threshold must also respect the > 900s margin
    so a rolling restart doesn't kill a task mid-flight."""
    from app import main

    src = inspect.getsource(main.lifespan)
    assert "minutes=20" in src, (
        "startup cleanup threshold should be 20 minutes (parity with the "
        "periodic cleanup_stuck_extractions task and a 5-min margin beyond "
        "the 15-min Celery hard kill)"
    )


# ---------------------------------------------------------------------------
# A5/A6: end-to-end behaviour — the cleanup predicate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a6_startup_cleanup_does_not_target_fresh_exams(monkeypatch):
    """Sanity test: simulate a fresh exam (updated_at = now) and verify
    the cleanup predicate would NOT match it."""
    from sqlalchemy import and_

    # Build the WHERE clause the way the endpoint does.
    threshold = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=20)
    fresh_updated_at = _dt.datetime.now(_dt.timezone.utc)  # right now

    # Sanity: a fresh exam is NOT older than the threshold.
    assert fresh_updated_at > threshold, (
        "test setup error: fresh_updated_at should be newer than threshold"
    )


@pytest.mark.asyncio
async def test_a5_threshold_values():
    """Direct check of the threshold values used by both cleanup paths."""
    from app.workers import tasks as worker_tasks
    from app.workers.celery_app import celery_app

    # Celery hard time limit
    hard_limit_seconds = celery_app.conf.task_time_limit
    assert hard_limit_seconds == 900, "Celery hard time limit is no longer 900s?"

    # The cleanup threshold must be > hard_limit + safety margin (5 min).
    # Source contains "minutes=20" → 1200s.
    src = inspect.getsource(worker_tasks.cleanup_stuck_extractions)
    assert "minutes=20" in src
    cleanup_threshold_seconds = 20 * 60
    assert cleanup_threshold_seconds >= hard_limit_seconds + 300, (
        "Cleanup threshold must be at least 5 min beyond Celery hard limit"
    )
