"""Regression test for cumulative trigger empty-text guard (C11).

Pre-fix contract: ``_check_trigger_cumulative`` always fired
``cumulative_extraction.delay()`` when all docs finished OCR — even when
every doc had empty ``extracted_text``. The else branch (no text) had a
warning log followed by ``cumulative_extraction.delay(...)`` anyway.
Result: wasted LLM calls on blank-page / failed-OCR exams, and the exam
ended up with hallucinated or empty ``impressions``.

Post-fix contract pinned here: when all included docs finish OCR with no
extracted text, the else branch marks the exam completed (extraction_status
= "completed", impressions = "") and DOES NOT call
``cumulative_extraction.delay``.
"""
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_c11_else_branch_does_not_trigger_cumulative_extraction():
    """The empty-text else branch must not enqueue cumulative_extraction.

    Static source check (matches the style of existing audit regression
    tests): the else branch previously contained a redundant
    `cumulative_extraction.delay(...)` call. The fix replaces it with an
    exam-status update.
    """
    from app.workers import ai_tasks

    src = inspect.getsource(ai_tasks._check_trigger_cumulative)
    # Locate the else branch.
    assert "else:" in src, "Expected an else branch in _check_trigger_cumulative."
    else_idx = src.index("else:")
    else_block = src[else_idx:]
    # The else branch must NOT enqueue cumulative_extraction.
    assert "cumulative_extraction.delay" not in else_block, (
        "Empty-text else branch must not enqueue cumulative_extraction — that "
        "wastes an LLM call on blank / failed-OCR input."
    )
    # The else branch must mark the exam completed.
    assert 'extraction_status="completed"' in else_block, (
        "Empty-text else branch should mark the exam completed so the UI "
        "shows it done without an LLM call."
    )


@pytest.mark.asyncio
async def test_c11_empty_text_path_marks_exam_completed(monkeypatch):
    """End-to-end check of the empty-text branch: all included docs finished
    with empty extracted_text → exam UPDATE staged, no .delay() call.

    Uses mocked DB to avoid needing a real Postgres for this logic test.
    """
    from app.workers import ai_tasks
    from app.models.document_model import DocumentModel

    # Build a doc that's part of an exam where all included docs finished
    # with empty text.
    exam_id = "00000000-0000-0000-0000-000000000001"
    doc_id = "00000000-0000-0000-0000-000000000002"

    fake_doc = MagicMock()
    fake_doc.id = doc_id
    fake_doc.examination_id = exam_id
    fake_doc.owner_id = None

    finished_doc = MagicMock()
    finished_doc.extracted_text = ""  # empty text
    finished_doc.status = "completed"

    # Mock DB: each execute() returns a pre-canned scalar / scalars result.
    async def fake_execute(stmt, *args, **kwargs):
        # Inspect the SQL text to decide what to return.
        sql_text = str(stmt)
        if "pg_try_advisory_xact_lock" in sql_text:
            r = MagicMock()
            r.scalar.return_value = True  # lock acquired
            return r
        if "DocumentModel.id ==" in sql_text or "WHERE \"documents\".\"id\" =" in sql_text:
            # The first lookup: get the doc that triggered the check.
            r = MagicMock()
            r.scalar_one_or_none.return_value = fake_doc
            return r
        if "processing, uploaded" in sql_text:
            # Pending-doc check: return no pending docs.
            r = MagicMock()
            scalars = MagicMock()
            scalars.first.return_value = None
            r.scalars.return_value = scalars
            return r
        if "completed, failed" in sql_text:
            # Finished-doc lookup: return one doc with empty text.
            r = MagicMock()
            scalars = MagicMock()
            scalars.all.return_value = [finished_doc]
            r.scalars.return_value = scalars
            return r
        # UPDATE statements (the exam-status update in the else branch).
        r = MagicMock()
        return r

    db = MagicMock()
    db.execute = AsyncMock(side_effect=fake_execute)
    db.commit = AsyncMock()

    # Spy on cumulative_extraction.delay — it must NOT be called.
    delay_spy = MagicMock()
    monkeypatch.setattr(ai_tasks.cumulative_extraction, "delay", delay_spy)

    # Capture UPDATE statements to verify the exam-status update happens.
    update_calls = []

    async def capture_execute(stmt, *args, **kwargs):
        sql_text = str(stmt)
        update_calls.append(sql_text)
        return await fake_execute(stmt, *args, **kwargs)

    db.execute = AsyncMock(side_effect=capture_execute)

    await ai_tasks._check_trigger_cumulative(db, doc_id)

    # cumulative_extraction.delay must NOT have been called.
    assert delay_spy.call_count == 0, (
        "Empty-text path must not enqueue cumulative_extraction — LLM call "
        "would be wasted on blank input."
    )

    # An UPDATE against examinations must have been staged.
    assert any(
        ("UPDATE examinations" in sql or "examination" in sql.lower())
        and "UPDATE" in sql.upper()
        for sql in update_calls
    ), f"Expected an UPDATE on the exam table; got: {update_calls}"
