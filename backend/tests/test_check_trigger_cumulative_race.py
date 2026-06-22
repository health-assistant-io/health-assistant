"""Regression tests for audit item C3 — ``_check_trigger_cumulative`` race.

Pre-fix contract: the function read the pending-doc count and fired
``cumulative_extraction.delay()`` only if no docs remained. Concurrent OCR
completions (typical with multi-doc upload) all saw the same pending count
→ either nobody fired cumulative, or everybody fired it (doubles LLM cost
+ races biomarker auto-create).

Post-fix contract pinned here:
1. ``_check_trigger_cumulative`` acquires a per-exam advisory lock
   (``pg_try_advisory_xact_lock``) before checking the pending count.
2. If the lock can't be acquired, the function returns without firing
   cumulative_extraction (another OCR completion is already mid-check).
3. The lock is keyed on the exam id (stable per exam).
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers import tasks as worker_tasks


def _doc(exam_id=None, owner_id=None):
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.examination_id = exam_id or uuid.uuid4()
    doc.owner_id = owner_id or uuid.uuid4()
    return doc


# ---------------------------------------------------------------------------
# C3: advisory lock acquired before pending-count check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_trigger_cumulative_acquires_advisory_lock():
    """The function MUST call pg_try_advisory_xact_lock before the
    pending-count SELECT."""
    exam_id = uuid.uuid4()
    doc = _doc(exam_id=exam_id)

    # db.execute is called with several statements. Capture them.
    captured_sql = []

    async def _execute(stmt, *a, **kw):
        rendered = str(stmt).lower()
        captured_sql.append(rendered)
        # The first call selects the doc by id.
        if "fhir_documents" in rendered and "select" in rendered and len(captured_sql) == 1:
            r = MagicMock()
            r.scalar_one_or_none.return_value = doc
            return r
        # The second call is the advisory lock.
        if "pg_try_advisory_xact_lock" in rendered:
            r = MagicMock()
            r.scalar.return_value = True  # lock acquired
            return r
        # Subsequent calls: empty pending docs + empty completed docs.
        r = MagicMock()
        r.scalars.return_value.first.return_value = None
        r.scalars.return_value.all.return_value = []
        return r

    db = MagicMock()
    db.execute = _execute

    with patch.object(worker_tasks.cumulative_extraction, "delay") as delayed:
        await worker_tasks._check_trigger_cumulative(db, doc.id)

    # Verify the advisory lock was issued.
    assert any("pg_try_advisory_xact_lock" in s for s in captured_sql), (
        "_check_trigger_cumulative must call pg_try_advisory_xact_lock to "
        "serialize concurrent OCR completions (audit C3)"
    )
    # And the lock call precedes the pending-count check.
    lock_idx = next(i for i, s in enumerate(captured_sql) if "pg_try_advisory_xact_lock" in s)
    # The pending-check uses `.in_(["processing", "uploaded"])` for status —
    # that literal string is unique to the pending-check query (the
    # completed-check uses "completed"/"failed").
    pending_idxs = [
        i
        for i, s in enumerate(captured_sql)
        if "processing" in s and "uploaded" in s and "include_in_extraction" in s
    ]
    pending_idx = pending_idxs[0] if pending_idxs else None
    if pending_idx is not None:
        assert lock_idx < pending_idx, (
            f"Advisory lock (idx {lock_idx}) must be acquired BEFORE the "
            f"pending-count check (idx {pending_idx})"
        )
    # Since no pending docs and no completed docs, cumulative_extraction
    # is still fired (the "no text" warning path).
    delayed.assert_called_once()


@pytest.mark.asyncio
async def test_check_trigger_cumulative_skips_when_lock_not_acquired():
    """If pg_try_advisory_xact_lock returns False, another OCR completion
    is already mid-check. We must NOT fire cumulative_extraction."""
    exam_id = uuid.uuid4()
    doc = _doc(exam_id=exam_id)

    call_count = {"pending_check": 0}

    async def _execute(stmt, *a, **kw):
        rendered = str(stmt).lower()
        # First call: select the doc.
        if "fhir_documents" in rendered and "select" in rendered and call_count["pending_check"] == 0:
            r = MagicMock()
            r.scalar_one_or_none.return_value = doc
            call_count["pending_check"] += 1
            return r
        # Second call: advisory lock — return False (not acquired).
        if "pg_try_advisory_xact_lock" in rendered:
            r = MagicMock()
            r.scalar.return_value = False
            return r
        # Should never get here on the skip path.
        call_count["pending_check"] += 1
        r = MagicMock()
        r.scalars.return_value.first.return_value = None
        return r

    db = MagicMock()
    db.execute = _execute

    with patch.object(worker_tasks.cumulative_extraction, "delay") as delayed:
        result = await worker_tasks._check_trigger_cumulative(db, doc.id)

    delayed.assert_not_called(), (
        "cumulative_extraction.delay() must NOT fire when the advisory lock "
        "couldn't be acquired (audit C3 TOCTOU fix)"
    )


@pytest.mark.asyncio
async def test_check_trigger_cumulative_no_exam_returns_early():
    """If the doc has no examination_id, return early without firing."""
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.examination_id = None

    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=doc))
    )

    with patch.object(worker_tasks.cumulative_extraction, "delay") as delayed:
        await worker_tasks._check_trigger_cumulative(db, doc.id)

    delayed.assert_not_called()


@pytest.mark.asyncio
async def test_check_trigger_cumulative_doc_not_found_returns_early():
    """If the doc doesn't exist, return early without firing."""
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )

    with patch.object(worker_tasks.cumulative_extraction, "delay") as delayed:
        await worker_tasks._check_trigger_cumulative(db, uuid.uuid4())

    delayed.assert_not_called()


@pytest.mark.asyncio
async def test_check_trigger_cumulative_skips_when_pending_docs():
    """If pending docs remain, don't fire cumulative_extraction."""
    exam_id = uuid.uuid4()
    doc = _doc(exam_id=exam_id)
    pending_doc = _doc(exam_id=exam_id)

    state = {"calls": 0}

    async def _execute(stmt, *a, **kw):
        rendered = str(stmt).lower()
        state["calls"] += 1
        # First: select doc by id
        if state["calls"] == 1:
            r = MagicMock()
            r.scalar_one_or_none.return_value = doc
            return r
        # Second: advisory lock
        if "pg_try_advisory_xact_lock" in rendered:
            r = MagicMock()
            r.scalar.return_value = True
            return r
        # Third: pending-docs check — return a non-empty match.
        r = MagicMock()
        r.scalars.return_value.first.return_value = pending_doc
        return r

    db = MagicMock()
    db.execute = _execute

    with patch.object(worker_tasks.cumulative_extraction, "delay") as delayed:
        await worker_tasks._check_trigger_cumulative(db, doc.id)

    delayed.assert_not_called()


@pytest.mark.asyncio
async def test_check_trigger_cumulative_fires_when_no_pending_and_docs_with_text():
    """If no pending docs and at least one completed doc with text → fire."""
    exam_id = uuid.uuid4()
    doc = _doc(exam_id=exam_id)
    completed_doc = MagicMock()
    completed_doc.id = uuid.uuid4()
    completed_doc.extracted_text = "Glucose 110 mg/dL"

    state = {"calls": 0}

    async def _execute(stmt, *a, **kw):
        rendered = str(stmt).lower()
        state["calls"] += 1
        if state["calls"] == 1:
            r = MagicMock()
            r.scalar_one_or_none.return_value = doc
            return r
        if "pg_try_advisory_xact_lock" in rendered:
            r = MagicMock()
            r.scalar.return_value = True
            return r
        if state["calls"] == 3:
            # pending check — empty
            r = MagicMock()
            r.scalars.return_value.first.return_value = None
            return r
        if state["calls"] == 4:
            # completed docs — non-empty with text
            r = MagicMock()
            r.scalars.return_value.all.return_value = [completed_doc]
            return r
        return MagicMock()

    db = MagicMock()
    db.execute = _execute

    with patch.object(worker_tasks.cumulative_extraction, "delay") as delayed:
        await worker_tasks._check_trigger_cumulative(db, doc.id)

    delayed.assert_called_once()
    args, kwargs = delayed.call_args
    assert args[0] == str(exam_id)
