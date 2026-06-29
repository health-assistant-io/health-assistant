"""Regression tests for audit item C2 — re-extraction savepoint.

Pre-fix contract: ``_persist_results`` deleted all of an exam's existing
Observations + Medications BEFORE re-running the LLM extraction. If the
re-extraction produced fewer or invalid observations, previously-correct
data was permanently gone — no savepoint, no provenance, no audit log.

Post-fix contract pinned here:
1. The delete + recreate is wrapped in ``async with self.db.begin_nested():``
   (a SAVEPOINT).
2. Any exception inside the savepoint releases it with a rollback,
   restoring the prior Observations + Medications.
3. The outer transaction stays open for the caller.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.pipeline.service import MedicalProcessingService


def _exam():
    exam = MagicMock()
    exam.id = uuid.uuid4()
    exam.tenant_id = uuid.uuid4()
    exam.patient_id = uuid.uuid4()
    exam.examination_date = None
    return exam


def _parsed_data():
    """A minimal parsed_data object the service can iterate."""
    pd = MagicMock()
    pd.known_biomarkers = []
    pd.unknown_biomarkers = []
    pd.known_medications = []
    pd.unknown_medications = []
    return pd


# ---------------------------------------------------------------------------
# C2: savepoint protects against re-extraction failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_results_uses_savepoint():
    """``_persist_results`` must wrap its work in ``begin_nested()`` (a
    Postgres SAVEPOINT) so failures roll back to the pre-delete state."""
    db = MagicMock()
    db.begin_nested = MagicMock()
    savepoint = AsyncMock()
    savepoint.__aenter__ = AsyncMock(return_value=savepoint)
    savepoint.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested.return_value = savepoint

    db.execute = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    # Service stub: AIProviderService init not needed for _persist_results.
    inst = MedicalProcessingService.__new__(MedicalProcessingService)
    inst.db = db

    # Empty bio/med/unit maps so the inner loops are no-ops.
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=empty_result)

    await inst._persist_results(_exam(), _parsed_data(), [], {}, {})

    db.begin_nested.assert_called_once(), (
        "_persist_results must wrap its delete + recreate in begin_nested() "
        "(SAVEPOINT) so re-extraction failures roll back to pre-delete state"
    )


@pytest.mark.asyncio
async def test_persist_results_savepoint_rolls_back_on_failure():
    """If the body of the savepoint raises, the savepoint is rolled back
    (released with an error) and the exception propagates."""
    db = MagicMock()
    savepoint = AsyncMock()
    savepoint.__aenter__ = AsyncMock(return_value=savepoint)
    savepoint.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=savepoint)

    # The first execute call (the delete) raises — simulate a downstream
    # failure mid-re-extraction.
    db.execute = AsyncMock(side_effect=RuntimeError("LLM extraction blew up"))
    db.add = MagicMock()

    inst = MedicalProcessingService.__new__(MedicalProcessingService)
    inst.db = db

    with pytest.raises(RuntimeError):
        await inst._persist_results(_exam(), _parsed_data(), [], {}, {})

    # The savepoint's __aexit__ was awaited with the exception → True would
    # swallow; False would propagate. Verify __aexit__ was invoked.
    savepoint.__aexit__.assert_awaited()


@pytest.mark.asyncio
async def test_persist_results_savepoint_releases_cleanly_on_success():
    """On success, the savepoint is released cleanly (committed)."""
    db = MagicMock()
    savepoint = AsyncMock()
    savepoint.__aenter__ = AsyncMock(return_value=savepoint)
    savepoint.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=savepoint)

    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=empty_result)
    db.add = MagicMock()

    inst = MedicalProcessingService.__new__(MedicalProcessingService)
    inst.db = db

    await inst._persist_results(_exam(), _parsed_data(), [], {}, {})

    # __aexit__ awaited with no exception (None, None, None).
    savepoint.__aexit__.assert_awaited()
    args = savepoint.__aexit__.await_args
    # When no exception, all three args are None.
    assert all(a is None for a in (args.args or ())), (
        "Savepoint __aexit__ should be called with no-exception args on success"
    )


# ---------------------------------------------------------------------------
# C2: failure during the savepoint must NOT leave the deletion committed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_results_failure_does_not_commit_delete(monkeypatch):
    """Concrete end-to-end check: a failure mid-re-extraction rolls the
    savepoint back so the prior state is preserved."""
    executed = []

    class _FakeBeginNested:
        def __init__(self):
            self.rolled_back = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            # True = swallow; False = propagate. We propagate.
            if exc_type is not None:
                self.rolled_back = True
            return False

    class _FakeSession:
        def begin_nested(self):
            sp = _FakeBeginNested()
            self.last_savepoint = sp
            return sp

        async def execute(self, stmt, *a, **kw):
            executed.append(stmt)
            # Simulate a failure on the SECOND execute call (the Medication
            # delete) — corresponds to a downstream LLM extraction blowing
            # up after the Observation delete already ran.
            if len(executed) == 2:
                raise RuntimeError("simulated mid-re-extraction failure")
            res = MagicMock()
            res.scalars.return_value.all.return_value = []
            return res

        def add(self, obj):
            pass

        async def commit(self):
            pass

    fake_db = _FakeSession()
    inst = MedicalProcessingService.__new__(MedicalProcessingService)
    inst.db = fake_db

    with pytest.raises(RuntimeError):
        await inst._persist_results(_exam(), _parsed_data(), [], {}, {})

    # The savepoint was rolled back (because the body raised).
    assert fake_db.last_savepoint.rolled_back is True, (
        "Savepoint must roll back when the re-extraction body raises — "
        "otherwise the prior Observations/Medications are lost (audit C2)"
    )
    # The first delete (Observation) was issued but rolled back.
    assert len(executed) >= 2, "expected at least the two delete statements"
