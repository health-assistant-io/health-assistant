"""
Retry OCR task endpoint - properly formatted
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, String, cast, or_
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel

router = APIRouter(prefix="/task-monitor", tags=["Task Monitoring"])


@router.get("/documents/processing")
async def get_processing_documents(
    patient_id: Optional[UUID] = None,
    status: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """
    Get documents stuck in processing status

    Debug endpoint for identifying stalled OCR tasks

    Security:
    - Requires authentication
    - Returns only non-sensitive metadata
    - No file content or API keys exposed
    """
    query = select(DocumentModel).where(
        DocumentModel.status.in_(["processing", "uploaded"])
    )

    if patient_id:
        query = query.where(DocumentModel.patient_id == patient_id)

    if status:
        query = query.where(DocumentModel.status == status)

    query = query.order_by(DocumentModel.created_at.desc())
    query = query.limit(limit)

    result = await db.execute(query)
    documents = result.scalars().all()

    return [
        {
            "id": str(doc.id),
            "examination_id": str(doc.examination_id) if doc.examination_id else None,
            "filename": doc.filename,
            "status": doc.status,
            "progress": doc.progress,
            "created_at": doc.created_at.isoformat(),
            "age_minutes": (
                datetime.now() - doc.created_at.replace(tzinfo=None)
            ).total_seconds()
            / 60
            if doc.created_at
            else 0,
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
    """
    Get examinations stuck in extraction

    Debug endpoint for identifying stalled NLP tasks

    Security:
    - Requires authentication
    - Returns only non-sensitive metadata
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

    if patient_id:
        query = query.where(ExaminationModel.patient_id == patient_id)

    if status:
        query = query.where(ExaminationModel.extraction_status == status)

    query = query.order_by(ExaminationModel.created_at.desc())
    query = query.limit(limit)

    result = await db.execute(query)
    examinations = result.scalars().all()

    return [
        {
            "id": str(exam.id),
            "category": exam.category_entity.name if exam.category_entity else None,
            "status": exam.extraction_status,
            "progress": exam.extraction_progress,
            "created_at": exam.created_at.isoformat(),
            "age_minutes": (
                datetime.now() - exam.created_at.replace(tzinfo=None)
            ).total_seconds()
            / 60
            if exam.created_at
            else 0,
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
    """
    Retry OCR processing for failed/stalled document

    Debug action: Resets status and triggers Celery OCR task

    Security:
    - Requires authentication
    - Validates document exists
    - Only allows retry for non-completed docs
    """
    from app.models.document_model import DocumentModel
    from sqlalchemy import update

    result = await db.execute(
        select(DocumentModel).where(DocumentModel.id == document_id)
    )
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
    """
    Retry examination extraction for failed/stalled exam

    Debug action: Resets status to trigger retry

    Security:
    - Requires authentication
    - Validates examination exists
    """
    from app.models.examination_model import ExaminationModel
    from sqlalchemy import update

    result = await db.execute(
        select(ExaminationModel).where(ExaminationModel.id == examination_id)
    )
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
    """
    Get task processing statistics

    Monitoring endpoint for system health

    Security:
    - Aggregate data only
    - No sensitive information
    """
    from sqlalchemy import func

    # Document stats
    doc_result = await db.execute(
        select(
            DocumentModel.status, func.count(DocumentModel.id).label("count")
        ).group_by(DocumentModel.status)
    )
    doc_stats = {status: count for status, count in doc_result.all()}

    # Examination stats
    exam_result = await db.execute(
        select(
            ExaminationModel.extraction_status,
            func.count(ExaminationModel.id).label("count"),
        ).group_by(ExaminationModel.extraction_status)
    )
    exam_stats = {status: count for status, count in exam_result.all()}

    # Stalled tasks (processing for > 10 minutes)
    stalled_time = datetime.now() - timedelta(minutes=10)
    stalled_doc_result = await db.execute(
        select(func.count(DocumentModel.id)).where(
            DocumentModel.status == "processing",
            DocumentModel.created_at < stalled_time.replace(tzinfo=None),
        )
    )
    stalled_docs = stalled_doc_result.scalar()

    stalled_exam_result = await db.execute(
        select(func.count(ExaminationModel.id)).where(
            ExaminationModel.extraction_status.in_(["processing", "aggregating"]),
            ExaminationModel.created_at < stalled_time.replace(tzinfo=None),
        )
    )
    stalled_exams = stalled_exam_result.scalar()

    return {
        "documents": {
            "by_status": doc_stats,
            "stalled": stalled_docs,
        },
        "examinations": {
            "by_status": exam_stats,
            "stalled": stalled_exams,
        },
        "timestamp": datetime.utcnow().isoformat(),
    }
