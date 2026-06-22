"""
Task-monitor endpoints — operational visibility on OCR + extraction jobs.

All endpoints are **tenant-scoped** (audit item B1). A non-``SYSTEM_ADMIN``
caller only ever sees rows whose ``tenant_id`` matches their token, and any
tenant-scoped retry (``/documents/retry/{id}``, ``/examinations/retry/{id}``)
returns ``404`` if the row belongs to a different tenant — so an attacker
cannot probe for the existence of other tenants' resources.

``SYSTEM_ADMIN`` is the deliberate exception: the role is reserved for the
platform operator and bypasses the tenant filter for global monitoring.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import RoleChecker, get_current_user
from app.models.document_model import DocumentModel
from app.models.enums import Role
from app.models.examination_model import ExaminationModel
from app.schemas.user import TokenData

router = APIRouter(prefix="/task-monitor", tags=["Task Monitoring"])


def _apply_tenant_filter(stmt, model, current_user: TokenData):
    """Restrict ``stmt`` to the caller's tenant unless they are SYSTEM_ADMIN."""
    if current_user.role == Role.SYSTEM_ADMIN.value:
        return stmt
    return stmt.where(model.tenant_id == current_user.tenant_id)


@router.get("/documents/processing")
async def get_processing_documents(
    patient_id: Optional[UUID] = None,
    status: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get documents stuck in processing status.

    Debug endpoint for identifying stalled OCR tasks. Returns only
    non-sensitive metadata (filename, status, progress, age, last error);
    no file content or API keys exposed. Results are tenant-scoped.
    """
    query = select(DocumentModel).where(
        DocumentModel.status.in_(["processing", "uploaded"])
    )
    query = _apply_tenant_filter(query, DocumentModel, current_user)

    if patient_id:
        query = query.where(DocumentModel.patient_id == patient_id)

    if status:
        query = query.where(DocumentModel.status == status)

    query = query.order_by(DocumentModel.created_at.desc()).limit(limit)

    result = await db.execute(query)
    documents = result.scalars().all()

    return [
        {
            "id": str(doc.id),
            "tenant_id": str(doc.tenant_id) if doc.tenant_id else None,
            "examination_id": str(doc.examination_id) if doc.examination_id else None,
            "filename": doc.filename,
            "status": doc.status,
            "progress": doc.progress,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "age_minutes": (
                (datetime.now(timezone.utc) - doc.created_at).total_seconds() / 60
                if doc.created_at
                else 0
            ),
            "error_message": doc.error_message,
        }
        for doc in documents
    ]


@router.get("/examinations/processing")
async def get_processing_examinations(
    patient_id: Optional[UUID] = None,
    status: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get examinations stuck in extraction.

    Debug endpoint for identifying stalled NLP tasks. Returns only
    non-sensitive metadata. Tenant-scoped.
    """
    query = (
        select(ExaminationModel)
        .options(selectinload(ExaminationModel.category_entity))
        .where(
            ExaminationModel.extraction_status.in_(
                ["processing", "aggregating", "analyzing_text"]
            )
        )
    )
    query = _apply_tenant_filter(query, ExaminationModel, current_user)

    if patient_id:
        query = query.where(ExaminationModel.patient_id == patient_id)

    if status:
        query = query.where(ExaminationModel.extraction_status == status)

    query = query.order_by(ExaminationModel.created_at.desc()).limit(limit)

    result = await db.execute(query)
    examinations = result.scalars().all()

    return [
        {
            "id": str(exam.id),
            "tenant_id": str(exam.tenant_id) if exam.tenant_id else None,
            "category": exam.category_entity.name if exam.category_entity else None,
            "status": exam.extraction_status,
            "progress": exam.extraction_progress,
            "created_at": exam.created_at.isoformat() if exam.created_at else None,
            "age_minutes": (
                (datetime.now(timezone.utc) - exam.created_at).total_seconds() / 60
                if exam.created_at
                else 0
            ),
            "error_message": exam.error_message,
        }
        for exam in examinations
    ]


@router.post("/documents/retry/{document_id}")
async def retry_document_ocr(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Retry OCR processing for a failed/stalled document.

    Resets status and re-enqueues the Celery OCR task. Tenant-scoped — a
    cross-tenant retry returns 404 (no information leak).
    """
    query = select(DocumentModel).where(DocumentModel.id == document_id)
    query = _apply_tenant_filter(query, DocumentModel, current_user)
    result = await db.execute(query)
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Allow retry for any non-completed status
    if doc.status == "completed":
        raise HTTPException(
            status_code=400, detail="Document already completed - cannot retry"
        )

    # Reset status to trigger retry
    await db.execute(
        update(DocumentModel)
        .where(DocumentModel.id == document_id)
        .values(status="uploaded", progress=0, error_message=None)
    )
    await db.commit()

    # Trigger Celery OCR task
    from app.workers.tasks import ocr_document

    ocr_document.delay(str(document_id), doc.file_path, str(doc.tenant_id))

    return {"message": "Document OCR will be retried", "document_id": str(document_id)}


@router.post("/examinations/retry/{examination_id}")
async def retry_examination_extraction(
    examination_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Retry examination extraction for a failed/stalled exam.

    Resets status to trigger retry. Tenant-scoped — a cross-tenant retry
    returns 404.
    """
    query = select(ExaminationModel).where(ExaminationModel.id == examination_id)
    query = _apply_tenant_filter(query, ExaminationModel, current_user)
    result = await db.execute(query)
    exam = result.scalar_one_or_none()

    if not exam:
        raise HTTPException(status_code=404, detail="Examination not found")

    # Reset status to trigger retry
    await db.execute(
        update(ExaminationModel)
        .where(ExaminationModel.id == examination_id)
        .values(extraction_status=None, extraction_progress=0)
    )
    await db.commit()

    return {
        "message": "Examination extraction will be retried",
        "examination_id": str(examination_id),
    }


@router.get("/stats")
async def get_task_statistics(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get task processing statistics.

    Aggregate counts only — no per-row PHI. Tenant-scoped for ordinary
    users; SYSTEM_ADMIN sees the global picture (the role is reserved for
    the platform operator).
    """
    from sqlalchemy import func

    doc_stmt = select(
        DocumentModel.status, func.count(DocumentModel.id).label("count")
    )
    doc_stmt = _apply_tenant_filter(doc_stmt, DocumentModel, current_user)
    doc_stmt = doc_stmt.group_by(DocumentModel.status)
    doc_result = await db.execute(doc_stmt)
    doc_stats = {status: count for status, count in doc_result.all()}

    exam_stmt = select(
        ExaminationModel.extraction_status,
        func.count(ExaminationModel.id).label("count"),
    )
    exam_stmt = _apply_tenant_filter(exam_stmt, ExaminationModel, current_user)
    exam_stmt = exam_stmt.group_by(ExaminationModel.extraction_status)
    exam_result = await db.execute(exam_stmt)
    exam_stats = {status: count for status, count in exam_result.all()}

    # Stalled tasks (processing for > 10 minutes)
    stalled_time = datetime.now(timezone.utc) - timedelta(minutes=10)

    stalled_doc_stmt = select(func.count(DocumentModel.id)).where(
        DocumentModel.status == "processing",
        DocumentModel.created_at < stalled_time,
    )
    stalled_doc_stmt = _apply_tenant_filter(
        stalled_doc_stmt, DocumentModel, current_user
    )
    stalled_docs = (await db.execute(stalled_doc_stmt)).scalar() or 0

    stalled_exam_stmt = select(func.count(ExaminationModel.id)).where(
        ExaminationModel.extraction_status.in_(["processing", "aggregating"]),
        ExaminationModel.created_at < stalled_time,
    )
    stalled_exam_stmt = _apply_tenant_filter(
        stalled_exam_stmt, ExaminationModel, current_user
    )
    stalled_exams = (await db.execute(stalled_exam_stmt)).scalar() or 0

    return {
        "documents": {
            "by_status": doc_stats,
            "stalled": stalled_docs,
        },
        "examinations": {
            "by_status": exam_stats,
            "stalled": stalled_exams,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
