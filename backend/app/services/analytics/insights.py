"""Anomalies, analytics summary and category analytics."""

from app.services.analytics.types import (
    UTC,
    AsyncSession,
    DocumentModel,
    ExaminationModel,
    Medication,
    Observation,
    func,
    select,
)


async def get_biomarker_anomalies(
    tenant_id: str,
    biomarker_codes: str | None = None,
    patient_id: str | None = None,
    db: AsyncSession = None,
) -> dict:
    """Detect and read telemetry anomalies through the one CAgg-based engine.

    Detection runs SQL-side over the hourly continuous aggregate (rolling
    baseline per patient/device/slug), persists deduplicated events to
    ``telemetry_anomalies``, and this reader returns the stored window events
    (active and recently resolved). There is no Python raw-row detector: the
    analytic contract is telemetry-only and CAgg-based.
    """
    if not db:
        return {"anomalies": []}

    from app.services.biomarkers.anomaly_engine import (
        detect_telemetry_anomalies,
        list_telemetry_anomalies,
    )

    await detect_telemetry_anomalies(
        db,
        tenant_id=tenant_id,
        patient_id=patient_id,
    )

    slugs = None
    if biomarker_codes:
        slugs = [code.strip() for code in biomarker_codes.split(",") if code.strip()]

    anomalies = await list_telemetry_anomalies(
        db,
        tenant_id=tenant_id,
        patient_id=patient_id,
        slugs=slugs,
    )
    return {"anomalies": anomalies}


async def get_analytics_summary(
    tenant_id: str,
    patient_id: str | None = None,
    period: str = "last-year",
    db: AsyncSession = None,
) -> dict:
    if not db:
        return {
            "total_documents": 0,
            "total_observations": 0,
            "total_medications": 0,
            "active_alerts": 0,
            "last_upload": "",
        }

    doc_query = select(func.count(DocumentModel.id)).where(DocumentModel.tenant_id == tenant_id)
    if patient_id:
        doc_query = doc_query.where(DocumentModel.patient_id == patient_id)

    result = await db.execute(doc_query)
    total_documents = result.scalar() or 0

    obs_query = select(func.count(Observation.id)).where(Observation.tenant_id == tenant_id)
    if patient_id:
        obs_query = obs_query.where(
            Observation.subject["reference"].as_string() == f"Patient/{patient_id}"
        )

    result = await db.execute(obs_query)
    total_observations = result.scalar() or 0

    med_query = select(func.count(Medication.id)).where(Medication.tenant_id == tenant_id)
    if patient_id:
        med_query = med_query.where(Medication.patient_id == patient_id)

    result = await db.execute(med_query)
    total_medications = result.scalar() or 0

    last_upload_query = select(DocumentModel.updated_at).where(DocumentModel.tenant_id == tenant_id)
    if patient_id:
        last_upload_query = last_upload_query.where(DocumentModel.patient_id == patient_id)
    last_upload_query = last_upload_query.order_by(DocumentModel.updated_at.desc()).limit(1)

    result = await db.execute(last_upload_query)
    last_upload_row = result.scalar_one_or_none()
    last_upload = last_upload_row.isoformat() if last_upload_row else ""

    return {
        "total_documents": total_documents,
        "total_observations": total_observations,
        "total_medications": total_medications,
        "active_alerts": 0,
        "last_upload": last_upload,
    }


async def get_category_analytics(
    tenant_id: str,
    category_name: str,
    patient_id: str | None = None,
    db: AsyncSession = None,
) -> dict:
    if not db:
        return {"reports": [], "biomarkers": {}}

    from app.core.constants import CATEGORY_MAPPING

    backend_category = CATEGORY_MAPPING.get(category_name, category_name).lower()

    query = select(DocumentModel).where(DocumentModel.tenant_id == tenant_id)
    if patient_id:
        query = query.where(DocumentModel.patient_id == patient_id)

    query = query.where(DocumentModel.status == "completed")

    result = await db.execute(query)
    documents = result.scalars().all()

    exam_ids = [d.examination_id for d in documents if d.examination_id]
    exams_map = {}
    if exam_ids:
        exam_query = select(ExaminationModel).where(ExaminationModel.id.in_(exam_ids))
        exam_result = await db.execute(exam_query)
        for e in exam_result.scalars().all():
            exams_map[e.id] = e.examination_date

    reports = []
    matched_doc_ids = []

    for doc in documents:
        doc_entities = doc.entities or {}
        doc_category = doc_entities.get("document_category", "Uncategorized").lower()

        # Less strict matching - if it's anywhere in the category name
        if backend_category in doc_category or category_name.lower() in doc_category:
            exam_date = exams_map.get(doc.examination_id)
            display_date = (
                exam_date.isoformat()
                if exam_date
                else (doc.updated_at.isoformat() if doc.updated_at else "")
            )

            reports.append(
                {
                    "id": str(doc.id),
                    "date": display_date,
                    "document_name": doc.filename,
                    "type": doc_category,
                }
            )
            matched_doc_ids.append(doc.id)

    reports.sort(key=lambda x: x["date"], reverse=True)

    trends = {}
    if matched_doc_ids:
        obs_query = select(Observation).where(Observation.tenant_id == tenant_id)
        if patient_id:
            obs_query = obs_query.where(
                Observation.subject["reference"].astext == f"Patient/{patient_id}"
            )
        obs_query = obs_query.where(Observation.document_id.in_(matched_doc_ids))

        obs_result = await db.execute(obs_query)
        observations = obs_result.scalars().all()

        from datetime import datetime

        for obs in observations:
            name = obs.code.get("text", "Unknown")
            key = name.lower()
            if key not in trends:
                trends[key] = []

            val = getattr(obs, "normalized_value", None)
            if val is None:
                val = getattr(obs, "raw_value", None)
            if val is None:
                val = obs.value_quantity.get("value") if obs.value_quantity else None

            if val is not None:
                obs_date = obs.effective_datetime
                if obs.document_id and obs.document_id in [d.id for d in documents]:
                    doc_obj = next((d for d in documents if d.id == obs.document_id), None)
                    if doc_obj and doc_obj.examination_id and doc_obj.examination_id in exams_map:
                        exam_date = exams_map[doc_obj.examination_id]
                        if exam_date:
                            obs_date = datetime.combine(exam_date, datetime.min.time(), tzinfo=UTC)

                trends[key].append(
                    {
                        "date": obs_date.isoformat() if obs_date else "",
                        "value": val,
                        "unit": obs.value_quantity.get("unit", "") if obs.value_quantity else "",
                        "name": name,
                        "relative_score": getattr(obs, "relative_score", None),
                    }
                )

        for key in trends:
            trends[key].sort(key=lambda x: x["date"])

    return {"reports": reports, "biomarkers": trends}
