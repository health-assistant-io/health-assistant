"""Tests for ``app.services.document_service.ingest_document_bytes`` (C.1).

The canonical ingestion path that the integration engine uses (C.2) and
that the existing ``upload_document`` UI wrapper now delegates to. These
tests exercise the real-DB + real-filesystem path so the file-write +
DB-row + OCR-dispatch contract is covered end-to-end.
"""
import io
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.document_model import DocumentModel
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.services import document_service


@pytest_asyncio.fixture
async def tenant_user_patient():
    """Create an isolated tenant + user + patient for the document.

    Returns ``(tenant_id, user_id, patient_id)``.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    patient_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        db.add(
            TenantModel(
                id=tenant_id,
                name="Doc Bytes T.",
                slug=f"docbytes-{tenant_id.hex[:8]}",
            )
        )
        await db.flush()
        db.add(
            UserModel(
                id=user_id,
                email=f"docbytes-{user_id.hex[:6]}@test.local",
                tenant_id=tenant_id,
                role="ADMIN",
            )
        )
        await db.flush()
        db.add(
            Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name={"family": "Test", "given": ["Doc"]},
                gender="UNKNOWN",
            )
        )
        await db.commit()

    return tenant_id, user_id, patient_id


# ---------------------------------------------------------------------------
# ingest_document_bytes — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_document_bytes_writes_file_and_db_row(
    tenant_user_patient,
):
    """The canonical happy path: bytes go to disk under
    ``UPLOAD_DIR/<tenant_id>/``, a DocumentModel row lands in the DB with
    the right metadata, and the function returns the refreshed row."""
    tenant_id, user_id, _patient_id = tenant_user_patient
    content = b"%PDF-1.4\nfake pdf body\n%%EOF\n"

    async with AsyncSessionLocal() as db:
        # Patch the OCR task dispatch so we don't need a live Celery broker.
        with patch("app.workers.ai_tasks.ocr_document") as mock_ocr:
            from app.workers.ai_tasks import ocr_document as _task

            _task.apply_async = mock_ocr.apply_async
            doc = await document_service.ingest_document_bytes(
                filename="lab_report.pdf",
                content=content,
                content_type="application/pdf",
                tenant_id=tenant_id,
                patient_id=None,
                owner_id=user_id,
                db=db,
                include_in_extraction=False,
            )

        assert doc.id is not None
        assert doc.filename == "lab_report.pdf"
        assert doc.tenant_id == tenant_id
        assert doc.owner_id == user_id
        assert doc.status == "uploaded"
        assert doc.progress == 0
        assert doc.include_in_extraction is False

        # The on-disk file lives under UPLOAD_DIR/<tenant_id>/<uuid>.pdf
        file_path = Path(doc.file_path)
        assert file_path.exists()
        assert file_path.read_bytes() == content
        assert file_path.parent.name == str(tenant_id)
        assert file_path.suffix == ".pdf"


@pytest.mark.asyncio
async def test_ingest_document_bytes_links_examination_and_category(
    tenant_user_patient,
):
    """``examination_id`` + ``category_concept_id`` are honored when
    provided (the engine wiring uses these for the integration pull path).
    """
    from app.models.enums import ConceptKind
    from app.services.concept_service import ConceptService

    tenant_id, user_id, patient_id = tenant_user_patient

    async with AsyncSessionLocal() as db:
        # Build the linked concept + examination.
        svc = ConceptService(db)
        concept = await svc.create_concept(
            slug=f"lab-report-{uuid.uuid4().hex[:8]}",
            name="Lab Report",
            kind=ConceptKind.DOCUMENT_CATEGORY,
            tenant_id=tenant_id,
            role="ADMIN",
            actor=None,
        )
        from app.models.examination_model import ExaminationModel

        exam = ExaminationModel(
            tenant_id=tenant_id,
            patient_id=patient_id,
            examination_date=date(2026, 7, 21),
        )
        db.add(exam)
        await db.flush()

        doc = await document_service.ingest_document_bytes(
            filename="report.pdf",
            content=b"fake pdf",
            content_type="application/pdf",
            tenant_id=tenant_id,
            patient_id=patient_id,
            owner_id=user_id,
            db=db,
            examination_id=exam.id,
            category_concept_id=concept.id,
            include_in_extraction=False,
        )

        assert doc.examination_id == exam.id
        assert doc.category_concept_id == concept.id
        assert doc.patient_id == patient_id


# ---------------------------------------------------------------------------
# OCR dispatch contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_document_bytes_dispatches_ocr_when_requested(
    tenant_user_patient,
):
    """When ``include_in_extraction=True``, the service dispatches the
    ``ocr_document`` Celery task with the 4-arg form
    ``(document_id, file_path, tenant_id, owner_id)``. The dispatch is
    best-effort: a broker-down failure is swallowed."""
    tenant_id, user_id, _patient_id = tenant_user_patient

    async with AsyncSessionLocal() as db:
        with patch(
            "app.workers.ai_tasks.ocr_document.apply_async",
            return_value=type("T", (), {"id": "fake-task-id"}),
        ) as mock_apply:
            await document_service.ingest_document_bytes(
                filename="scan.png",
                content=b"\x89PNG\r\n\x1a\n fake png",
                content_type="image/png",
                tenant_id=tenant_id,
                patient_id=None,
                owner_id=user_id,
                db=db,
                include_in_extraction=True,
            )
            assert mock_apply.called, (
                "ocr_document.apply_async must be called when "
                "include_in_extraction=True"
            )
            dispatched_args = mock_apply.call_args.kwargs.get("args") or \
                mock_apply.call_args.args
            assert len(dispatched_args) == 4
            # The 4-arg form: id, path, tenant, owner.
            assert dispatched_args[2] == str(tenant_id)
            assert dispatched_args[3] == str(user_id)


@pytest.mark.asyncio
async def test_ingest_document_bytes_skips_ocr_when_not_requested(
    tenant_user_patient,
):
    """When ``include_in_extraction=False``, no OCR task is dispatched."""
    tenant_id, user_id, _patient_id = tenant_user_patient

    async with AsyncSessionLocal() as db:
        with patch(
            "app.workers.ai_tasks.ocr_document.apply_async"
        ) as mock_apply:
            await document_service.ingest_document_bytes(
                filename="notes.txt",
                content=b"just plain notes, no extraction needed",
                content_type="text/plain",
                tenant_id=tenant_id,
                patient_id=None,
                owner_id=user_id,
                db=db,
                include_in_extraction=False,
            )
            assert not mock_apply.called, (
                "ocr_document.apply_async must NOT be called when "
                "include_in_extraction=False"
            )


@pytest.mark.asyncio
async def test_ingest_document_bytes_swallows_broker_down_failure(
    tenant_user_patient,
):
    """A broker-down ``apply_async`` failure must not propagate — the
    document is already persisted by the time the dispatch runs, and the
    caller (engine or UI) can re-trigger extraction via
    ``trigger_extraction`` later. The service logs + continues."""
    tenant_id, user_id, _patient_id = tenant_user_patient

    async with AsyncSessionLocal() as db:
        with patch(
            "app.workers.ai_tasks.ocr_document.apply_async",
            side_effect=ConnectionError("broker unreachable"),
        ):
            # Must not raise.
            doc = await document_service.ingest_document_bytes(
                filename="resilient.pdf",
                content=b"%PDF-1.4 fake",
                content_type="application/pdf",
                tenant_id=tenant_id,
                patient_id=None,
                owner_id=user_id,
                db=db,
                include_in_extraction=True,
            )

        # Row still landed.
        assert doc.id is not None
        fetched = (
            await db.execute(
                select(DocumentModel).where(DocumentModel.id == doc.id)
            )
        ).scalar_one()
        assert fetched.status == "uploaded"  # NOT "processing" — OCR never ran


# ---------------------------------------------------------------------------
# Extension gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_document_bytes_rejects_disallowed_extension(
    tenant_user_patient,
):
    """The extension allowlist applies to the integration path too — a
    provider that returns an ``.exe`` (or any non-medical type) is
    rejected with HTTPException(400)."""
    from fastapi import HTTPException

    tenant_id, user_id, _patient_id = tenant_user_patient

    async with AsyncSessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            await document_service.ingest_document_bytes(
                filename="malicious.exe",
                content=b"MZ\x90\x00",
                content_type="application/octet-stream",
                tenant_id=tenant_id,
                patient_id=None,
                owner_id=user_id,
                db=db,
                include_in_extraction=False,
            )
        assert exc_info.value.status_code == 400
        assert "Unsupported file type" in exc_info.value.detail


# ---------------------------------------------------------------------------
# upload_document (the UploadFile wrapper) — regression coverage
# ---------------------------------------------------------------------------


class _FakeUploadFile:
    """Stand-in for Starlette ``UploadFile`` — only the attributes the
    wrapper reads."""

    def __init__(self, *, filename: str, content: bytes, content_type: str):
        self.filename = filename
        self.content_type = content_type
        self._buffer = io.BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


@pytest.mark.asyncio
async def test_upload_document_delegates_to_ingest_document_bytes(
    tenant_user_patient,
):
    """``upload_document`` (the UploadFile wrapper) reads the upload in
    capped chunks and delegates the rest to ``ingest_document_bytes``.
    This test builds a fake UploadFile and asserts the same DB row shape
    the bytes-path test above asserts."""
    tenant_id, user_id, _patient_id = tenant_user_patient
    content = b"%PDF-1.4 hello\n%%EOF\n"

    async with AsyncSessionLocal() as db:
        with patch("app.workers.ai_tasks.ocr_document.apply_async"):
            file = _FakeUploadFile(
                filename="wrapped.pdf",
                content=content,
                content_type="application/pdf",
            )
            doc = await document_service.upload_document(
                file,
                patient_id=None,
                owner_id=user_id,
                tenant_id=tenant_id,
                db=db,
                include_in_extraction=False,
            )

        assert doc.filename == "wrapped.pdf"
        assert Path(doc.file_path).read_bytes() == content
