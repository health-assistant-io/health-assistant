from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, cast, String

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.models.fhir.patient import Patient
from app.models.examination_model import ExaminationModel
from app.models.document_model import DocumentModel
from app.models.clinical_event import ClinicalEvent
from app.services.catalog_search_service import search_catalogs

router = APIRouter()


@router.get("")
async def global_search(
    q: str = Query(..., min_length=2),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """
    Perform a global search across all main entities for the current tenant.

    Patient/examination/document/clinical-event blocks are inline (tenant-scoped
    ILIKE); the catalog portion (biomarker, medication, allergy, anatomy,
    concept, …) is delegated to the registry-driven ``search_catalogs``
    dispatcher so every registered catalog appears automatically.
    """
    tenant_id = current_user.tenant_id
    search_pattern = f"%{q}%"
    results = []

    # 1. Search Patients
    patients_result = await db.execute(
        select(Patient)
        .where(
            Patient.tenant_id == tenant_id,
            or_(
                cast(Patient.name, String).ilike(search_pattern),
                Patient.mrn.ilike(search_pattern),
            ),
        )
        .limit(5)
    )
    patients = patients_result.scalars().all()

    for p in patients:
        name_obj = p.name
        if isinstance(name_obj, list) and len(name_obj) > 0:
            name_obj = name_obj[0]

        given = name_obj.get("given", []) if name_obj else []
        family = name_obj.get("family", "") if name_obj else ""
        full_name = f"{' '.join(given)} {family}".strip()

        results.append(
            {
                "id": str(p.id),
                "type": "patient",
                "title": full_name or "Unknown Patient",
                "subtitle": f"MRN: {p.mrn}" if p.mrn else "Patient Record",
            }
        )

    # 2. Search Examinations
    examinations_result = await db.execute(
        select(ExaminationModel)
        .where(
            ExaminationModel.tenant_id == tenant_id,
            or_(
                ExaminationModel.notes.ilike(search_pattern),
                ExaminationModel.patient_notes.ilike(search_pattern),
                ExaminationModel.impressions.ilike(search_pattern),
            ),
        )
        .limit(5)
    )
    examinations = examinations_result.scalars().all()

    for e in examinations:
        date_str = (
            e.examination_date.isoformat() if e.examination_date else "Unknown Date"
        )
        results.append(
            {
                "id": str(e.id),
                "type": "examination",
                "title": e.category or "Examination",
                "subtitle": f"{date_str} - {e.notes[:50]}..." if e.notes else date_str,
            }
        )

    # 3. Search Documents
    documents_result = await db.execute(
        select(DocumentModel)
        .where(
            DocumentModel.tenant_id == tenant_id,
            DocumentModel.filename.ilike(search_pattern),
        )
        .limit(5)
    )
    documents = documents_result.scalars().all()

    for d in documents:
        results.append(
            {
                "id": str(d.id),
                "type": "document",
                "title": d.filename,
                "subtitle": "Document",
            }
        )

    # 4. Search Clinical Events
    events_result = await db.execute(
        select(ClinicalEvent)
        .where(
            ClinicalEvent.tenant_id == tenant_id,
            or_(
                ClinicalEvent.title.ilike(search_pattern),
                ClinicalEvent.description.ilike(search_pattern),
            ),
        )
        .limit(5)
    )
    events = events_result.scalars().all()

    for ev in events:
        results.append(
            {
                "id": str(ev.id),
                "type": "event",
                "title": ev.title,
                "subtitle": "Clinical Event",
            }
        )

    # 5. Search all clinical catalogs via the unified dispatcher (trigram,
    #    tenant-scoped). Replaces the hardcoded medication + biomarker blocks —
    #    anatomy, concepts, allergies, etc. now appear automatically as they
    #    register. See app.catalogs + catalog_search_service.search_catalogs.
    catalog_hits = await search_catalogs(db, tenant_id, q, limit_per_type=5)
    for hit in catalog_hits:
        results.append(
            {
                "id": hit["id"],
                "type": hit["type"],
                "title": hit["label"],
                "subtitle": f"{hit['type'].title()} Catalog",
            }
        )

    return {"results": results}
