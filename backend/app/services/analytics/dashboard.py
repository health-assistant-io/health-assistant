# ruff: noqa: E501 -- ternary and long lines; reflow when touched
"""Dashboard aggregate (statuses, latest results, category rollups)."""

from app.services.analytics.status import (
    _get_observation_status,
)
from app.services.analytics.types import (
    AsyncSession,
    BiomarkerDefinition,
    DiagnosticReport,
    DocumentModel,
    ExaminationModel,
    Observation,
    UserModel,
    select,
)


async def get_dashboard_data(
    tenant_id: str,
    patient_id: str | None = None,
    period: str = "last-30-days",
    db: AsyncSession = None,
) -> dict:
    if not db:
        return {
            "recent_documents": [],
            "upcoming_appointments": [],
            "alerts": [],
            "summary": {
                "total_documents": 0,
                "total_observations": 0,
                "last_upload": "",
            },
        }

    query = select(DocumentModel).where(DocumentModel.tenant_id == tenant_id)
    if patient_id:
        query = query.where(DocumentModel.patient_id == patient_id)
    query = query.order_by(DocumentModel.updated_at.desc()).limit(20)

    result = await db.execute(query)
    documents = result.scalars().all()

    exam_ids = [d.examination_id for d in documents if d.examination_id]
    exams_map = {}
    if exam_ids:
        exam_query = select(ExaminationModel).where(ExaminationModel.id.in_(exam_ids))
        exam_result = await db.execute(exam_query)
        for e in exam_result.scalars().all():
            exams_map[e.id] = e.examination_date

    recent_documents = []
    for doc in documents:
        exam_date = exams_map.get(doc.examination_id)
        display_date = (
            exam_date.isoformat()
            if exam_date
            else (doc.updated_at.isoformat() if doc.updated_at else "")
        )
        recent_documents.append(
            {
                "id": str(doc.id),
                "filename": doc.filename,
                "created_at": display_date,
                "entities": doc.entities,
            }
        )

    # Latest examination
    latest_exam_query = select(ExaminationModel).where(ExaminationModel.tenant_id == tenant_id)
    if patient_id:
        latest_exam_query = latest_exam_query.where(ExaminationModel.patient_id == patient_id)
    latest_exam_query = latest_exam_query.order_by(ExaminationModel.examination_date.desc()).limit(
        1
    )

    exam_result = await db.execute(latest_exam_query)
    latest_exam = exam_result.scalar_one_or_none()
    latest_exam_dict = None
    if latest_exam:
        latest_exam_dict = latest_exam.to_dict()

        # Format doctors string
        if latest_exam.doctors:
            latest_exam_dict["doctor_name"] = ", ".join(
                [f"Dr. {d.name}" for d in latest_exam.doctors]
            )
            latest_exam_dict["has_assigned_doctor"] = True
        elif latest_exam.created_by:
            user_result = await db.execute(
                select(UserModel.email).where(UserModel.id == latest_exam.created_by)
            )
            email = user_result.scalar_one_or_none()
            if email:
                name_part = email.split("@")[0].replace(".", " ").title()
                latest_exam_dict["doctor_name"] = f"Dr. {name_part}"
            else:
                latest_exam_dict["doctor_name"] = "Unknown Doctor"
            latest_exam_dict["has_assigned_doctor"] = False
        else:
            latest_exam_dict["doctor_name"] = "Unknown Doctor"
            latest_exam_dict["has_assigned_doctor"] = False

    # Latest Imaging
    # We look for both DiagnosticReports and Documents categorized as imaging or having image extensions
    imaging_list = []

    # 1. Look for Documents categorized as imaging/radiology or having image extensions
    from app.models.concept_model import Concept

    doc_imaging_query = (
        select(DocumentModel, Concept.name.label("exam_category"))
        .outerjoin(ExaminationModel, DocumentModel.examination_id == ExaminationModel.id)
        .outerjoin(Concept, ExaminationModel.category_concept_id == Concept.id)
        .where(DocumentModel.tenant_id == tenant_id)
    )

    if patient_id:
        doc_imaging_query = doc_imaging_query.where(DocumentModel.patient_id == patient_id)

    doc_imaging_query = doc_imaging_query.where(
        (DocumentModel.entities["document_category"].as_string().ilike("%imaging%"))
        | (DocumentModel.entities["document_category"].as_string().ilike("%radiology%"))
        | (DocumentModel.filename.ilike("%.jpg"))
        | (DocumentModel.filename.ilike("%.jpeg"))
        | (DocumentModel.filename.ilike("%.png"))
        | (DocumentModel.filename.ilike("%.webp"))
        | (DocumentModel.filename.ilike("%.gif"))
        | (DocumentModel.filename.ilike("%.bmp"))
        | (DocumentModel.filename.ilike("%.dcm"))
    )

    doc_imaging_query = doc_imaging_query.order_by(DocumentModel.updated_at.desc()).limit(20)

    doc_res = await db.execute(doc_imaging_query)
    all_docs = doc_res.all()

    for doc, exam_cat in all_docs:
        entities = doc.entities or {}
        doc_cat = str(entities.get("document_category", "")).lower()

        imaging_list.append(
            {
                "id": str(doc.id),
                "type": "document",
                "date": doc.updated_at.isoformat() if doc.updated_at else "",
                "title": doc.filename,
                "category": exam_cat or doc_cat.replace("_", " ").replace("-", " ").title()
                if doc_cat
                else "Imaging",
                "examination_id": str(doc.examination_id) if doc.examination_id else None,
                "has_image": True,
            }
        )

    # 2. If list is small, add DiagnosticReports
    if len(imaging_list) < 5:
        imaging_query = select(DiagnosticReport).where(DiagnosticReport.tenant_id == tenant_id)
        if patient_id:
            imaging_query = imaging_query.where(
                DiagnosticReport.subject["reference"].astext == f"Patient/{patient_id}"
            )
        imaging_query = imaging_query.order_by(DiagnosticReport.effective_datetime.desc()).limit(
            5 - len(imaging_list)
        )

        imaging_result = await db.execute(imaging_query)
        imaging_reports = imaging_result.scalars().all()
        for report in imaging_reports:
            if not any(item["id"] == str(report.id) for item in imaging_list):
                imaging_list.append(
                    {
                        "id": str(report.id),
                        "type": "report",
                        "date": report.effective_datetime.isoformat()
                        if report.effective_datetime
                        else "",
                        "title": report.code.get("text", "Imaging Study"),
                        "status": report.status,
                        "category": "Diagnostic Report",
                        "examination_id": None,
                        "has_image": False,
                    }
                )

    imaging_list.sort(key=lambda x: x["date"], reverse=True)
    imaging_list = imaging_list[:6]

    # Latest Laboratory Results
    labs_query = (
        select(Observation, BiomarkerDefinition.info.label("biomarker_info"))
        .outerjoin(BiomarkerDefinition, Observation.biomarker_id == BiomarkerDefinition.id)
        .where(Observation.tenant_id == tenant_id)
    )
    if patient_id:
        labs_query = labs_query.where(
            Observation.subject["reference"].as_string() == f"Patient/{patient_id}"
        )
    # Usually lab results are observations with category 'laboratory'
    # But for now let's just get the latest 20 unique biomarker results
    labs_query = labs_query.order_by(Observation.effective_datetime.desc()).limit(30)

    labs_result = await db.execute(labs_query)
    all_labs = labs_result.all()

    unique_labs = {}
    for obs, b_info in all_labs:
        name = obs.code.get("text", "Unknown")
        if name not in unique_labs and len(unique_labs) < 10:
            val = getattr(obs, "normalized_value", None)
            if val is None:
                val = getattr(obs, "raw_value", None)
            if val is None:
                val = obs.value_quantity.get("value") if obs.value_quantity else obs.value_string

            if val is not None:
                # Basic interpretation
                status = await _get_observation_status(name, val, obs)

                unique_labs[name] = {
                    "name": name,
                    "result": val,
                    "unit": obs.value_quantity.get("unit", "") if obs.value_quantity else "",
                    "date": obs.effective_datetime.isoformat() if obs.effective_datetime else "",
                    "status": status,
                    "biomarker_id": str(obs.biomarker_id) if obs.biomarker_id else None,
                    "relative_score": getattr(obs, "relative_score", None),
                    "info": b_info,
                }

    return {
        "recent_documents": recent_documents,
        "upcoming_appointments": [],
        "alerts": [],
        "latest_examination": latest_exam_dict,
        "latest_imaging": imaging_list,
        "latest_labs": list(unique_labs.values()),
        "summary": {
            "total_documents": len(documents),
            "total_observations": 0,
            "last_upload": recent_documents[0]["created_at"] if recent_documents else "",
        },
    }
