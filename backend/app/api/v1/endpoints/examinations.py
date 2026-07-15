from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import List, Optional
from uuid import UUID
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.examination_model import ExaminationModel
from app.models.clinical_event import EventExaminationLink
from app.models.user_model import UserModel
from app.models.doctor_model import DoctorModel
from app.ai.pipeline.service import MedicalProcessingService
from sqlalchemy.orm import selectinload
from app.schemas.examination import (
    ExaminationCreate,
    ExaminationUpdate,
    ExaminationResponse,
    ExaminationSummaryResponse,
    ExaminationStatusResponse,
    ExaminationExtractRequest,
    ExaminationBulkDeleteRequest,
)
from app.models.enums import Role
from app.services.access import check_patient_access, check_examination_access
import logging

logger = logging.getLogger(__name__)

from app.schemas.user import TokenData

router = APIRouter(prefix="/examinations", tags=["examinations"])


async def get_user_tenant(user_id, db: AsyncSession):
    result = await db.execute(
        select(UserModel).where(
            UserModel.id == UUID(user_id) if isinstance(user_id, str) else user_id
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.tenant_id


@router.post("", response_model=ExaminationResponse)
async def create_examination(
    examination_in: ExaminationCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Validate patient exists before attempting to create examination
    from app.models.fhir.patient import Patient

    if examination_in.patient_id:
        patient_check = await db.execute(
            select(Patient).where(Patient.id == examination_in.patient_id)
        )
        patient = patient_check.scalar_one_or_none()
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Patient with ID {examination_in.patient_id} not found. Please create the patient first or use a valid patient ID.",
            )

    # Resolve category if provided
    category_concept_id = examination_in.category_concept_id
    if not category_concept_id and examination_in.category:
        processing_service = MedicalProcessingService(db)
        category_entity = await processing_service.resolve_category(
            examination_in.category, current_user.tenant_id
        )
        category_concept_id = category_entity.id

    # Check for potential duplicates (same patient, date, and category/notes)
    existing_query = select(ExaminationModel).where(
        ExaminationModel.tenant_id == current_user.tenant_id,
        ExaminationModel.patient_id == examination_in.patient_id,
        ExaminationModel.examination_date == examination_in.examination_date,
        ExaminationModel.category_concept_id == category_concept_id,
        ExaminationModel.notes == examination_in.notes,
    )
    existing_result = await db.execute(existing_query)
    existing_exam = existing_result.scalars().first()

    if existing_exam and not examination_in.auto_extract_metadata:
        # If it already exists, just return the existing one instead of creating a duplicate
        # We skip this for auto_extract_metadata because multiple placeholder exams might be created in bulk
        logger.info(
            f"Duplicate examination detected for patient {examination_in.patient_id}, returning existing record."
        )
        return existing_exam

    examination = ExaminationModel(
        patient_id=examination_in.patient_id,
        examination_date=examination_in.examination_date,
        notes=examination_in.notes,
        patient_notes=examination_in.patient_notes,
        category_concept_id=category_concept_id,
        organization_id=examination_in.organization_id,
        auto_extract_metadata=examination_in.auto_extract_metadata,
        tenant_id=current_user.tenant_id,
        created_by=current_user.user_id,
    )

    if examination_in.doctor_ids:
        result = await db.execute(
            select(DoctorModel).where(
                DoctorModel.id.in_(examination_in.doctor_ids),
                DoctorModel.tenant_id == current_user.tenant_id,
            )
        )
        examination.doctors = list(result.scalars().all())

    db.add(examination)
    await db.commit()

    # Reload with relationships
    result = await db.execute(
        select(ExaminationModel)
        .where(ExaminationModel.id == examination.id)
        .options(
            selectinload(ExaminationModel.doctors),
            selectinload(ExaminationModel.organization),
            selectinload(ExaminationModel.category_concept),
        )
    )
    return result.scalar_one()


@router.put("/{examination_id}", response_model=ExaminationResponse)
async def update_examination(
    examination_id: str,
    examination_in: ExaminationUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    examination = await check_examination_access(examination_id, current_user, db)

    update_data = examination_in.model_dump(exclude_unset=True)
    doctor_ids = update_data.pop("doctor_ids", None)

    # Handle category update (string to ID resolution)
    if "category" in update_data:
        category_name = update_data.pop("category")
        if category_name:
            processing_service = MedicalProcessingService(db)
            category_entity = await processing_service.resolve_category(
                category_name, current_user.tenant_id
            )
            examination.category_concept_id = category_entity.id
        else:
            examination.category_concept_id = None

    # Track if date is changing to update linked clinical records
    date_changed = (
        "examination_date" in update_data
        and update_data["examination_date"] != examination.examination_date
    )

    for key, value in update_data.items():
        setattr(examination, key, value)

    # If examination date changed, synchronize all linked clinical observations
    if date_changed:
        from app.models.fhir import Observation, Medication
        from sqlalchemy import update
        import datetime

        new_date = update_data["examination_date"]
        # Convert date to datetime for FHIR models that use DateTime
        if new_date:
            new_datetime = datetime.datetime.combine(
                new_date, datetime.time.min, tzinfo=datetime.timezone.utc
            )

            # Update Observations
            await db.execute(
                update(Observation)
                .where(Observation.examination_id == examination.id)
                .values(effective_datetime=new_datetime)
            )

            # Update Medications start_date (Medication start_date is Date, not DateTime)
            await db.execute(
                update(Medication)
                .where(Medication.examination_id == examination.id)
                .values(start_date=new_date)
            )
        else:
            logger.warning(
                f"Examination {examination.id} date set to None. Skipping sync for observations."
            )

        # Update Medications start_date (Medication start_date is Date, not DateTime)
        await db.execute(
            update(Medication)
            .where(Medication.examination_id == examination.id)
            .values(start_date=new_date)
        )

    if doctor_ids is not None:
        if doctor_ids:
            doc_result = await db.execute(
                select(DoctorModel).where(
                    DoctorModel.id.in_(doctor_ids),
                    DoctorModel.tenant_id == current_user.tenant_id,
                )
            )
            examination.doctors = list(doc_result.scalars().all())
        else:
            examination.doctors = []

    await db.commit()

    # Reload with relationships after commit. ``populate_existing=True`` is
    # required because the exam was already loaded earlier in this request
    # (check_examination_access) with category_concept eagerly loaded (as None
    # before the update). Without it, the identity map returns that same
    # instance and selectinload skips the already-"loaded" relationship — so a
    # newly-set category_concept_id would serialize as category_concept=None.
    result = await db.execute(
        select(ExaminationModel)
        .where(ExaminationModel.id == examination.id)
        .options(
            selectinload(ExaminationModel.doctors),
            selectinload(ExaminationModel.organization),
            selectinload(ExaminationModel.category_concept),
        )
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


@router.get("/categories", response_model=List[str])
async def list_examination_categories(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.concept_model import Concept
    from app.models.enums import ConceptKind
    from app.services.concept_service import concepts_with_kind

    # Examination categories now live in the unified concepts table
    result = await db.execute(
        select(Concept.name).where(
            concepts_with_kind(ConceptKind.EXAMINATION_CATEGORY),
            or_(
                Concept.tenant_id == current_user.tenant_id,
                Concept.tenant_id.is_(None),
            ),
        )
    )
    return sorted(result.scalars().all())


@router.get("", response_model=List[ExaminationSummaryResponse])
async def list_examinations(
    patient_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(ExaminationModel)
        .where(ExaminationModel.tenant_id == current_user.tenant_id)
        .options(
            selectinload(ExaminationModel.doctors),
            selectinload(ExaminationModel.documents),
            selectinload(ExaminationModel.category_concept),
            selectinload(ExaminationModel.organization),
            selectinload(ExaminationModel.observations),
            selectinload(ExaminationModel.medications),
            # Bidirectional: surface the health journeys this visit belongs to.
            selectinload(ExaminationModel.event_links).selectinload(
                EventExaminationLink.event
            ),
        )
    )

    if patient_id:
        await check_patient_access(patient_id, current_user, db)
        query = query.where(ExaminationModel.patient_id == UUID(patient_id))
    elif current_user.role == Role.USER.value:
        # For standard users, if no patient_id is provided, only show examinations for their patients
        from app.models.fhir.patient import Patient

        patient_ids_query = select(Patient.id).where(
            Patient.user_id == current_user.user_id
        )
        query = query.where(ExaminationModel.patient_id.in_(patient_ids_query))

    query = (
        query.order_by(ExaminationModel.examination_date.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(query)
    examinations = result.scalars().unique().all()

    # Efficiently format response matching ExaminationSummaryResponse schema
    # Pydantic's from_attributes=True will handle the mapping
    # but we need to map documents to document_statuses
    responses = []
    for exam in examinations:
        exam_dict = {
            "id": exam.id,
            "patient_id": exam.patient_id,
            "examination_date": exam.examination_date,
            "notes": exam.notes,
            "patient_notes": exam.patient_notes,
            "category_concept_id": exam.category_concept_id,
            "category": exam.category_concept.name
            if exam.category_concept
            else None,
            "category_concept": exam.category_concept,
            "extraction_status": exam.extraction_status,
            "extraction_progress": exam.extraction_progress,
            "error_message": exam.error_message,
            "diagnoses": exam.diagnoses,
            "impressions": exam.impressions,
            "doctors": exam.doctors,
            "organization": exam.organization,
            "observation_count": len(exam.observations) if exam.observations else 0,
            "medication_count": len(exam.medications) if exam.medications else 0,
            "document_statuses": [
                {
                    "id": doc.id,
                    "status": doc.status,
                    "progress": doc.progress,
                    "include_in_extraction": doc.include_in_extraction,
                }
                for doc in exam.documents
            ],
            "created_at": exam.created_at,
            "updated_at": exam.updated_at,
        }
        responses.append(exam_dict)

    return responses


@router.get("/{examination_id}", response_model=ExaminationResponse)
async def get_examination(
    examination_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_examination_access(examination_id, current_user, db)
    result = await db.execute(
        select(ExaminationModel)
        .where(
            ExaminationModel.id == UUID(examination_id),
            ExaminationModel.tenant_id == current_user.tenant_id,
        )
        .options(
            selectinload(ExaminationModel.doctors),
            selectinload(ExaminationModel.documents),
            selectinload(ExaminationModel.medications),
            selectinload(ExaminationModel.observations),
            selectinload(ExaminationModel.category_concept),
            selectinload(ExaminationModel.organization),
            # Bidirectional: surface the health journeys this visit belongs to.
            selectinload(ExaminationModel.event_links).selectinload(
                EventExaminationLink.event
            ),
        )
    )
    examination = result.scalar_one_or_none()
    # No need to check for not examination here as check_examination_access already did

    exam_dict = examination.to_dict()
    exam_dict["document_statuses"] = [
        {
            "id": doc.id,
            "status": doc.status,
            "progress": doc.progress,
            "include_in_extraction": doc.include_in_extraction,
        }
        for doc in examination.documents
    ]

    return exam_dict


@router.get("/{examination_id}/status", response_model=ExaminationStatusResponse)
async def get_examination_status(
    examination_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1. Fetch examination status with access check
    examination = await check_examination_access(examination_id, current_user, db)

    # 2. Fetch documents status
    from app.models.document_model import DocumentModel

    doc_result = await db.execute(
        select(
            DocumentModel.id,
            DocumentModel.status,
            DocumentModel.progress,
            DocumentModel.include_in_extraction,
        ).where(
            DocumentModel.examination_id == UUID(examination_id),
            DocumentModel.tenant_id == current_user.tenant_id,
        )
    )
    documents = doc_result.all()

    return {
        "id": examination.id,
        "extraction_status": examination.extraction_status,
        "extraction_progress": examination.extraction_progress,
        "error_message": examination.error_message,
        "documents": [
            {
                "id": doc.id,
                "status": doc.status,
                "progress": doc.progress,
                "include_in_extraction": doc.include_in_extraction,
            }
            for doc in documents
        ],
    }


@router.get("/{examination_id}/documents")
async def get_examination_documents(
    examination_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_examination_access(examination_id, current_user, db)
    from app.models.document_model import DocumentModel
    from app.services.document_service import enrich_document_entities
    from sqlalchemy import not_

    # Subquery to find all parent_ids that have children (meaning they have been edited)
    parent_ids_subquery = select(DocumentModel.parent_id).where(
        DocumentModel.examination_id == UUID(examination_id),
        DocumentModel.parent_id.isnot(None),
    )

    result = await db.execute(
        select(DocumentModel).where(
            DocumentModel.examination_id == UUID(examination_id),
            DocumentModel.tenant_id == current_user.tenant_id,
            not_(DocumentModel.id.in_(parent_ids_subquery)),
        )
    )
    documents = result.scalars().all()

    return [await enrich_document_entities(doc.to_dict(), db) for doc in documents]


@router.post("/{examination_id}/extract")
async def extract_examination_data(
    examination_id: str,
    request: Optional[ExaminationExtractRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Manually trigger AI extraction for an examination.
    Supported modes:
    - 'full': OCR for all included docs + LLM analysis (default)
    - 'extract_only': LLM analysis only using existing text
    """
    from app.services.document_service import (
        trigger_cumulative_extraction,
        trigger_full_examination_extraction,
    )

    try:
        # Verify ownership
        await check_examination_access(examination_id, current_user, db)

        mode = request.mode if request else "full"

        if mode == "full":
            job_id = await trigger_full_examination_extraction(examination_id, db)
        else:
            job_id = await trigger_cumulative_extraction(examination_id, db)

        return {"message": "Extraction triggered", "job_id": job_id, "mode": mode}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to trigger extraction: {e}"
        )


from app.schemas.task_log import TaskLogResponse


@router.get("/{examination_id}/logs", response_model=List[TaskLogResponse])
async def get_examination_logs(
    examination_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_examination_access(examination_id, current_user, db)
    from app.models.task_log import TaskLog
    from sqlalchemy import or_

    # Find logs related to this examination or its documents
    # 1. Get document IDs for this examination
    from app.models.document_model import DocumentModel

    doc_res = await db.execute(
        select(DocumentModel.id).where(
            DocumentModel.examination_id == UUID(examination_id)
        )
    )
    doc_ids = doc_res.scalars().all()

    # 2. Query logs
    query = (
        select(TaskLog)
        .where(TaskLog.tenant_id == current_user.tenant_id)
        .where(
            or_(
                TaskLog.resource_id == UUID(examination_id),
                TaskLog.resource_id.in_(doc_ids),
                TaskLog.task_id == examination_id,
                TaskLog.task_id.in_([str(d) for d in doc_ids]),
            )
        )
        .order_by(TaskLog.created_at.asc())
    )

    result = await db.execute(query)
    logs = result.scalars().all()
    return logs


@router.post("/bulk-delete")
async def bulk_delete_examinations(
    request: ExaminationBulkDeleteRequest,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.document_model import DocumentModel
    from app.services.document_service import delete_document
    from app.models.fhir import Observation, Medication
    from sqlalchemy import delete

    # Fetch all examinations to be deleted, verifying ownership
    query = select(ExaminationModel).where(
        ExaminationModel.id.in_(request.examination_ids),
        ExaminationModel.tenant_id == current_user.tenant_id,
    )
    if current_user.role == Role.USER.value:
        from app.models.fhir.patient import Patient

        query = query.join(Patient).where(Patient.user_id == current_user.user_id)

    result = await db.execute(query)
    examinations = result.scalars().all()

    if not examinations:
        return {"message": "No examinations found or access denied", "deleted_count": 0}

    actual_ids = [exam.id for exam in examinations]

    # 1. Physical document deletion for all linked docs
    docs_result = await db.execute(
        select(DocumentModel).where(DocumentModel.examination_id.in_(actual_ids))
    )
    documents = docs_result.scalars().all()
    for doc in documents:
        await delete_document(str(doc.id), db, trigger_cumulative=False)

    # 2. Explicitly delete clinical data
    await db.execute(
        delete(Observation).where(Observation.examination_id.in_(actual_ids))
    )
    await db.execute(
        delete(Medication).where(Medication.examination_id.in_(actual_ids))
    )

    # 3. Delete the examinations
    for exam in examinations:
        await db.delete(exam)

    await db.commit()

    return {
        "message": f"Successfully deleted {len(actual_ids)} examinations and related data",
        "deleted_count": len(actual_ids),
    }


@router.delete("/{examination_id}")
async def delete_examination(
    examination_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    examination = await check_examination_access(examination_id, current_user, db)

    # Delete all associated documents first (and their physical files)
    from app.models.document_model import DocumentModel
    from app.services.document_service import delete_document
    from app.models.fhir import Observation, Medication
    from sqlalchemy import delete

    # 1. Physical document deletion
    docs_result = await db.execute(
        select(DocumentModel).where(DocumentModel.examination_id == examination.id)
    )
    documents = docs_result.scalars().all()
    for doc in documents:
        # Pass trigger_cumulative=False to avoid starting a task for a deleted exam
        await delete_document(str(doc.id), db, trigger_cumulative=False)

    # 2. Explicitly delete clinical data (Observations & Medications)
    # This is also handled by DB CASCADE but explicit is better for "no trash"
    await db.execute(
        delete(Observation).where(Observation.examination_id == examination.id)
    )
    await db.execute(
        delete(Medication).where(Medication.examination_id == examination.id)
    )

    # 3. Delete the examination itself
    await db.delete(examination)
    await db.commit()
    return {
        "message": "Examination and all related clinical data and documents deleted successfully"
    }
