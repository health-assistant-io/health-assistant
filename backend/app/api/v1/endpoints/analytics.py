from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from sqlalchemy import select
from app.core.security import get_current_user
from app.services.access import check_patient_access
from app.models.enums import Role
from app.services.analytics_service import (
    get_analytics_summary,
    get_biomarker_trends,
    get_dashboard_data,
    get_biomarker_anomalies,
)

from app.schemas.user import TokenData

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/dashboard")
async def get_dashboard_endpoint(
    patient_id: str = Query(None),
    period: str = Query("last-30-days"),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get comprehensive dashboard data"""
    if patient_id:
        await check_patient_access(patient_id, current_user, db)
    elif current_user.role == Role.USER.value:
        # Standard users must specify a patient or they get nothing
        return {"items": [], "total": 0}

    data = await get_dashboard_data(str(current_user.tenant_id), patient_id, period, db)
    return data


@router.get("/summary")
async def get_summary_endpoint(
    patient_id: str = Query(None),
    period: str = Query("last-year"),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get analytics summary"""
    if patient_id:
        await check_patient_access(patient_id, current_user, db)
    elif current_user.role == Role.USER.value:
        return {}

    summary = await get_analytics_summary(
        str(current_user.tenant_id), patient_id, period, db
    )
    return summary


@router.get("/trends")
async def get_trends_endpoint(
    biomarker_codes: str = Query(None),
    period: str = Query("last-6-months"),
    aggregation: str = Query(None),
    patient_id: str = Query(None),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get biomarker trends"""
    if patient_id:
        await check_patient_access(patient_id, current_user, db)
    elif current_user.role == Role.USER.value:
        return []

    trends = await get_biomarker_trends(
        tenant_id=str(current_user.tenant_id),
        biomarker_codes=biomarker_codes,
        period=period,
        aggregation=aggregation,
        patient_id=patient_id,
        db=db,
    )
    return trends


@router.get("/anomalies")
async def get_anomalies_endpoint(
    biomarker_codes: str = Query(None),
    patient_id: str = Query(None),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Detect anomalies in biomarker trends (statistical outliers + reference range violations)"""
    if patient_id:
        await check_patient_access(patient_id, current_user, db)
    elif current_user.role == Role.USER.value:
        return {"anomalies": []}

    result = await get_biomarker_anomalies(
        tenant_id=str(current_user.tenant_id),
        biomarker_codes=biomarker_codes,
        patient_id=patient_id,
        db=db,
    )
    return result


@router.get("/reference-ranges")
async def get_reference_ranges_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Return each visible biomarker's default reference range.

    Sourced from the biomarker catalog (legacy global
    ``reference_range_min``/``max``), not a hardcoded dict — the previous
    static table drifted from the real catalog and duplicated the source of
    truth. Stratified ranges live under
    ``/biomarkers/{id}/reference-ranges``.
    """
    from sqlalchemy import or_

    from app.models.biomarker_model import BiomarkerDefinition, Unit

    result = await db.execute(
        select(
            BiomarkerDefinition.slug,
            BiomarkerDefinition.reference_range_min,
            BiomarkerDefinition.reference_range_max,
            Unit.symbol,
        )
        .outerjoin(Unit, BiomarkerDefinition.preferred_unit_id == Unit.id)
        .where(
            or_(
                BiomarkerDefinition.tenant_id.is_(None),
                BiomarkerDefinition.tenant_id == current_user.tenant_id,
            )
        )
    )
    return {
        slug: {"min": rmin, "max": rmax, "unit": unit}
        for slug, rmin, rmax, unit in result.all()
        if rmin is not None or rmax is not None
    }


from app.services.analytics_service import get_category_analytics


@router.get("/category/{category_name}")
async def get_category_analytics_endpoint(
    category_name: str,
    patient_id: str = Query(None),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get analytics for a specific category"""
    if patient_id:
        await check_patient_access(patient_id, current_user, db)
    elif current_user.role == Role.USER.value:
        return {}

    data = await get_category_analytics(
        str(current_user.tenant_id), category_name, patient_id, db
    )
    return data


@router.get("/available-categories")
async def get_available_categories_endpoint(
    patient_id: str = Query(None),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get list of categories that have data for the user/patient"""
    if patient_id:
        await check_patient_access(patient_id, current_user, db)

    from app.models.document_model import DocumentModel
    from app.models.fhir.patient import Patient

    query = select(DocumentModel.entities).where(
        DocumentModel.tenant_id == current_user.tenant_id,
        DocumentModel.status == "completed",
    )
    if patient_id:
        query = query.where(DocumentModel.patient_id == patient_id)
    elif current_user.role == Role.USER.value:
        # Filter by all user's patients
        patient_ids_query = select(Patient.id).where(
            Patient.user_id == current_user.user_id
        )
        query = query.where(DocumentModel.patient_id.in_(patient_ids_query))

    result = await db.execute(query)
    entities_list = result.scalars().all()

    from app.core.constants import CATEGORY_MAPPING

    available = set()
    for entities in entities_list:
        if entities:
            doc_cat = entities.get("document_category", "").lower()
            if doc_cat:
                for cat_id, cat_name in CATEGORY_MAPPING.items():
                    if (
                        cat_name.lower() in doc_cat
                        or cat_id.replace("-", " ") in doc_cat
                    ):
                        available.add(cat_id)

    return {"categories": list(available)}
