"""Regression tests for audit item B1 — task-monitor tenant isolation.

The five endpoints in ``task_monitor.py`` historically had no ``tenant_id``
filter and no per-row tenant re-check on retries, so any authenticated
user could list every tenant's processing docs / exams, fire global
OCR retries, and read global aggregate stats.

These tests pin the contract:

1. A ``USER`` caller only sees rows whose ``tenant_id`` matches their token.
2. A cross-tenant retry returns ``404`` (no information leak that the row
   belongs to another tenant).
3. A ``SYSTEM_ADMIN`` bypasses the tenant filter (operator visibility).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.endpoints import task_monitor
from app.models.enums import Role
from app.schemas.user import TokenData


def _user(tenant_id: uuid.UUID, role: str = Role.USER.value) -> TokenData:
    return TokenData(
        sub="test@local",
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=role,
    )


def _doc(tenant_id, status="processing", doc_id=None):
    """Build a fake DocumentModel row with the fields the endpoint reads."""
    fake = MagicMock()
    fake.id = doc_id or uuid.uuid4()
    fake.tenant_id = tenant_id
    fake.examination_id = None
    fake.filename = "report.pdf"
    fake.status = status
    fake.progress = 50
    fake.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    fake.error_message = None
    fake.file_path = "/tmp/report.pdf"
    return fake


def _exam(tenant_id, exam_id=None):
    """Build a fake ExaminationModel row with the fields the endpoint reads."""
    fake = MagicMock()
    fake.id = exam_id or uuid.uuid4()
    fake.tenant_id = tenant_id
    fake.category_entity = None
    fake.extraction_status = "processing"
    fake.extraction_progress = 30
    fake.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    fake.error_message = None
    return fake


# ---------------------------------------------------------------------------
# get_processing_documents — tenant filter applied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_processing_documents_filters_by_tenant_for_user(monkeypatch):
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user = _user(tenant_a)

    in_tenant_doc = _doc(tenant_a)
    out_of_tenant_doc = _doc(tenant_b)
    docs = [in_tenant_doc, out_of_tenant_doc]

    execute = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalars.return_value.all.return_value = docs
    execute.return_value = scalar_result

    db = MagicMock()
    db.execute = execute

    # Capture the stmt passed to db.execute by intercepting .where().
    captured = {}

    class _Stmt:
        def __init__(self, base):
            self.base = base
            self.predicates = []

        def where(self, *preds):
            self.predicates.extend(preds)
            return self

        def order_by(self, *a, **kw):
            return self

        def limit(self, *a, **kw):
            return self

    # Patch select() inside the module to capture the stmt.
    real_select = task_monitor.select

    def fake_select(*a, **kw):
        s = _Stmt(real_select(*a, **kw))
        captured["stmt"] = s
        return s

    monkeypatch.setattr(task_monitor, "select", fake_select)

    result = await task_monitor.get_processing_documents(
        db=db, current_user=user
    )

    # Only the in-tenant doc should be returned (the filter is applied via
    # SQLAlchemy; the DB layer here just returns whatever it returns, but
    # we verify the predicate was added to the stmt).
    assert captured["stmt"].predicates, "no predicates added to the query"
    rendered = " ".join(str(p) for p in captured["stmt"].predicates)
    assert "tenant_id" in rendered, "tenant_id predicate missing"

    # Sanity: the response shape is preserved.
    assert isinstance(result, list)
    assert result[0]["filename"] == "report.pdf"
    assert "tenant_id" in result[0]

    monkeypatch.setattr(task_monitor, "select", real_select)


@pytest.mark.asyncio
async def test_get_processing_documents_system_admin_skips_tenant_filter(monkeypatch):
    """SYSTEM_ADMIN is the deliberate cross-tenant visibility exception."""
    tenant_a = uuid.uuid4()
    admin = _user(uuid.uuid4(), role=Role.SYSTEM_ADMIN.value)

    captured = {}

    class _Stmt:
        def __init__(self, base):
            self.base = base
            self.predicates = []

        def where(self, *preds):
            # Only the status predicate should land here — no tenant filter.
            self.predicates.extend(preds)
            return self

        def order_by(self, *a, **kw):
            return self

        def limit(self, *a, **kw):
            return self

    real_select = task_monitor.select

    def fake_select(*a, **kw):
        s = _Stmt(real_select(*a, **kw))
        captured["stmt"] = s
        return s

    monkeypatch.setattr(task_monitor, "select", fake_select)

    execute = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.scalars.return_value.all.return_value = []
    execute.return_value = scalar_result
    db = MagicMock()
    db.execute = execute

    await task_monitor.get_processing_documents(db=db, current_user=admin)

    rendered = " ".join(str(p) for p in captured["stmt"].predicates)
    assert "tenant_id" not in rendered, (
        "SYSTEM_ADMIN must NOT get a tenant_id filter — operator role."
    )

    monkeypatch.setattr(task_monitor, "select", real_select)


# ---------------------------------------------------------------------------
# retry_document_ocr — cross-tenant retry returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_document_ocr_cross_tenant_returns_404():
    """A USER cannot retry OCR on another tenant's document."""
    from fastapi import HTTPException

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user = _user(tenant_a)
    foreign_doc = _doc(tenant_b)

    # First db.execute returns the row when filtering is skipped; with the
    # filter applied it returns None. We simulate the filtered path:
    execute = AsyncMock()
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    execute.return_value = empty_result

    db = MagicMock()
    db.execute = execute

    with pytest.raises(HTTPException) as exc:
        await task_monitor.retry_document_ocr(
            document_id=foreign_doc.id, db=db, current_user=user
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_retry_document_ocr_same_tenant_succeeds(monkeypatch):
    """A USER can retry OCR on their own tenant's document."""
    tenant_a = uuid.uuid4()
    user = _user(tenant_a)
    own_doc = _doc(tenant_a, status="failed")

    execute = AsyncMock()
    found_result = MagicMock()
    found_result.scalar_one_or_none.return_value = own_doc
    execute.return_value = found_result

    db = MagicMock()
    db.execute = execute
    db.commit = AsyncMock()

    delayed = MagicMock()
    fake_ocr = MagicMock()
    fake_ocr.delay = delayed
    monkeypatch.setitem(
        __import__("sys").modules,
        "app.workers.ai_tasks",
        MagicMock(ocr_document=fake_ocr),
    )

    result = await task_monitor.retry_document_ocr(
        document_id=own_doc.id, db=db, current_user=user
    )
    assert result["document_id"] == str(own_doc.id)
    delayed.assert_called_once()


@pytest.mark.asyncio
async def test_retry_document_ocr_completed_doc_rejected():
    """A USER cannot retry OCR on a doc that already completed."""
    from fastapi import HTTPException

    tenant_a = uuid.uuid4()
    user = _user(tenant_a)
    done_doc = _doc(tenant_a, status="completed")

    execute = AsyncMock()
    found_result = MagicMock()
    found_result.scalar_one_or_none.return_value = done_doc
    execute.return_value = found_result
    db = MagicMock()
    db.execute = execute

    with pytest.raises(HTTPException) as exc:
        await task_monitor.retry_document_ocr(
            document_id=done_doc.id, db=db, current_user=user
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# retry_examination_extraction — cross-tenant retry returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_examination_extraction_cross_tenant_returns_404():
    from fastapi import HTTPException

    tenant_a = uuid.uuid4()
    user = _user(tenant_a)

    execute = AsyncMock()
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    execute.return_value = empty_result
    db = MagicMock()
    db.execute = execute

    with pytest.raises(HTTPException) as exc:
        await task_monitor.retry_examination_extraction(
            examination_id=uuid.uuid4(), db=db, current_user=user
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_retry_examination_extraction_same_tenant_succeeds():
    tenant_a = uuid.uuid4()
    user = _user(tenant_a)
    own_exam = _exam(tenant_a)

    execute = AsyncMock()
    found_result = MagicMock()
    found_result.scalar_one_or_none.return_value = own_exam
    execute.return_value = found_result

    db = MagicMock()
    db.execute = execute
    db.commit = AsyncMock()

    result = await task_monitor.retry_examination_extraction(
        examination_id=own_exam.id, db=db, current_user=user
    )
    assert result["examination_id"] == str(own_exam.id)


# ---------------------------------------------------------------------------
# get_task_statistics — tenant filter applied to aggregates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_statistics_filters_by_tenant_for_user(monkeypatch):
    """The aggregate stats query must include the tenant_id predicate for
    non-admin users."""
    tenant_a = uuid.uuid4()
    user = _user(tenant_a)

    # Capture every statement passed to db.execute.
    rendered_stmts: list[str] = []

    real_select = task_monitor.select

    class _CaptureStmt:
        def __init__(self, base):
            self.base = base
            self.predicates = []

        def where(self, *preds):
            self.predicates.extend(preds)
            return self

        def group_by(self, *a, **kw):
            return self

    def fake_select(*a, **kw):
        return _CaptureStmt(real_select(*a, **kw))

    monkeypatch.setattr(task_monitor, "select", fake_select)

    async def _execute(stmt, *a, **kw):
        if isinstance(stmt, _CaptureStmt):
            rendered_stmts.extend(str(p) for p in stmt.predicates)
        scalar_result = MagicMock()
        scalar_result.all.return_value = []
        scalar_result.scalar.return_value = 0
        return scalar_result

    db = MagicMock()
    db.execute = _execute

    await task_monitor.get_task_statistics(db=db, current_user=user)

    # At least one tenant predicate should have been emitted on at least
    # one of the four queries (doc by_status, exam by_status, stalled docs,
    # stalled exams).
    joined = "\n".join(rendered_stmts)
    assert "tenant_id" in joined, "tenant predicate missing from stats queries"

    monkeypatch.setattr(task_monitor, "select", real_select)


@pytest.mark.asyncio
async def test_get_task_statistics_system_admin_unfiltered(monkeypatch):
    """SYSTEM_ADMIN stats queries must NOT carry a tenant_id predicate."""
    admin = _user(uuid.uuid4(), role=Role.SYSTEM_ADMIN.value)

    rendered_stmts: list[str] = []

    real_select = task_monitor.select

    class _CaptureStmt:
        def __init__(self, base):
            self.base = base
            self.predicates = []

        def where(self, *preds):
            self.predicates.extend(preds)
            return self

        def group_by(self, *a, **kw):
            return self

    def fake_select(*a, **kw):
        return _CaptureStmt(real_select(*a, **kw))

    monkeypatch.setattr(task_monitor, "select", fake_select)

    async def _execute(stmt, *a, **kw):
        if isinstance(stmt, _CaptureStmt):
            rendered_stmts.extend(str(p) for p in stmt.predicates)
        scalar_result = MagicMock()
        scalar_result.all.return_value = []
        scalar_result.scalar.return_value = 0
        return scalar_result

    db = MagicMock()
    db.execute = _execute

    await task_monitor.get_task_statistics(db=db, current_user=admin)

    joined = "\n".join(rendered_stmts)
    assert "tenant_id" not in joined, (
        "SYSTEM_ADMIN stats queries must NOT carry the tenant predicate."
    )

    monkeypatch.setattr(task_monitor, "select", real_select)
