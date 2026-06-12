from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, String, cast
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from app.models.user_model import UserModel

# FHIR models
from app.models.fhir import Observation, Medication, DiagnosticReport


import re


def normalize_unit(unit: str) -> str:
    """Normalize common clinical unit variations to a canonical form for better comparison."""
    if not unit:
        return ""
    u = unit.strip().lower()
    # Normalize exponents and formatting
    u = u.replace("^", "").replace("*", "").replace("(", "").replace(")", "")
    # Cell counts (Standard WBC/RBC units)
    # Billion/L (10^9/L) is equivalent to 10^3/uL
    if any(x in u for x in ["109/l", "x109/l", "billion/l"]):
        return "10^9/L"
    if any(x in u for x in ["103/ul", "x103/ul", "103/ml", "x103/ml", "k/ul", "k/mm3"]):
        # Note: ML is often an OCR error or misuse for uL in lab reports for cell counts
        return "10^9/L"
    # Trillion/L (10^12/L) is equivalent to 10^6/uL
    if any(x in u for x in ["1012/l", "x1012/l", "trillion/l"]):
        return "10^12/L"
    if any(x in u for x in ["106/ul", "x106/ul", "106/ml", "x106/ml", "m/ul"]):
        return "10^12/L"
    # Mass/Volume
    if u in ["g/dl", "gr/dl"]:
        return "g/dL"
    if u in ["mg/dl", "mgr/dl"]:
        return "mg/dL"
    if u in ["ug/dl", "mcg/dl", "µg/dl"]:
        return "µg/dL"
    if u in ["g/l"]:
        return "g/L"
    if u in ["mmol/l"]:
        return "mmol/L"
    if u in ["umol/l", "µmol/l"]:
        return "µmol/L"
    if u in ["miu/l"]:
        return "mIU/L"
    if u in ["u/l"]:
        return "U/L"
    if u == "%":
        return "%"
    return u


def units_are_compatible(unit1: str, unit2: str) -> bool:
    """Check if two unit strings represent the same clinical unit."""
    if not unit1 or not unit2:
        return False
    return normalize_unit(unit1) == normalize_unit(unit2)


async def _get_observation_status(
    name: str, val: Any, obs: Observation, ref_min: float = None, ref_max: float = None
) -> str:
    """Helper to determine the status of an observation using the new relative_score or interpretation"""
    # 1. Use LLM extracted interpretation if available
    if getattr(obs, "interpretation", None):
        interp = obs.interpretation.upper()
        if interp in ["H", "HIGH", "A", "ABNORMAL", "E", "ELEVATED"]:
            return "High"
        if interp in ["L", "LOW", "D", "DECREASED"]:
            return "Low"
        if interp in ["N", "NORMAL"]:
            return "Normal"

    # 2. Use relative score
    if getattr(obs, "relative_score", None) is not None:
        if obs.relative_score < 0:
            return "Low"
        if obs.relative_score > 1.0:
            return "High"
        return "Normal"

    # 3. Use provided ranges or fallback to parsing FHIR reference_range
    status = "Normal"
    try:
        num_val = float(val)
        low = ref_min
        high = ref_max

        # If not provided as arguments, try to get from observation
        if low is None and high is None:
            ref_range = obs.reference_range
            if ref_range and isinstance(ref_range, list) and len(ref_range) > 0:
                low = ref_range[0].get("low", {}).get("value")
                high = ref_range[0].get("high", {}).get("value")

        if low is not None and num_val < float(low):
            status = "Low"
        elif high is not None and num_val > float(high):
            status = "High"
        elif low is None and high is None:
            # Fallback to standard ranges for legacy data that wasn't processed with new engine
            STANDARD_RANGES = {
                "glucose": {"min": 3.9, "max": 5.6},
                "cholesterol": {"min": 0, "max": 5.2},
                "hdl": {"min": 1.0, "max": 999},
                "ldl": {"min": 0, "max": 3.0},
                "hemoglobin": {"min": 120, "max": 180},
                "white_blood_cells": {"min": 4.0, "max": 11.0},
                "platelets": {"min": 150, "max": 450},
                "creatinine": {"min": 60, "max": 110},
                "tsh": {"min": 0.4, "max": 4.0},
                "vitamin_d": {"min": 50, "max": 125},
                "systolic": {"min": 90, "max": 130},
                "diastolic": {"min": 60, "max": 85},
            }

            lower_name = name.lower()
            for key, r in STANDARD_RANGES.items():
                # Flexible name matching for standard ranges
                if (
                    key in lower_name
                    or key.replace("_", " ") in lower_name
                    or key.replace("_", "") in lower_name
                ):
                    if num_val < r["min"]:
                        status = "Low"
                    elif num_val > r["max"]:
                        status = "High"
                    break
    except (ValueError, TypeError):
        pass
    return status


async def get_dashboard_data(
    tenant_id: str,
    patient_id: str = None,
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
    latest_exam_query = select(ExaminationModel).where(
        ExaminationModel.tenant_id == tenant_id
    )
    if patient_id:
        latest_exam_query = latest_exam_query.where(
            ExaminationModel.patient_id == patient_id
        )
    latest_exam_query = latest_exam_query.order_by(
        ExaminationModel.examination_date.desc()
    ).limit(1)

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
    from app.models.examination_category import ExaminationCategory

    doc_imaging_query = (
        select(DocumentModel, ExaminationCategory.name.label("exam_category"))
        .outerjoin(
            ExaminationModel, DocumentModel.examination_id == ExaminationModel.id
        )
        .outerjoin(
            ExaminationCategory, ExaminationModel.category_id == ExaminationCategory.id
        )
        .where(DocumentModel.tenant_id == tenant_id)
    )

    if patient_id:
        doc_imaging_query = doc_imaging_query.where(
            DocumentModel.patient_id == patient_id
        )

    from app.models.biomarker_model import (
        BiomarkerDefinition,
        BiomarkerGroup,
        BiomarkerGroupMember,
    )

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

    doc_imaging_query = doc_imaging_query.order_by(
        DocumentModel.updated_at.desc()
    ).limit(20)

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
                "category": exam_cat
                or doc_cat.replace("_", " ").replace("-", " ").title()
                if doc_cat
                else "Imaging",
                "examination_id": str(doc.examination_id)
                if doc.examination_id
                else None,
                "has_image": True,
            }
        )

    # 2. If list is small, add DiagnosticReports
    if len(imaging_list) < 5:
        imaging_query = select(DiagnosticReport).where(
            DiagnosticReport.tenant_id == tenant_id
        )
        if patient_id:
            imaging_query = imaging_query.where(
                DiagnosticReport.subject["reference"].astext == f"Patient/{patient_id}"
            )
        imaging_query = imaging_query.order_by(
            DiagnosticReport.effective_datetime.desc()
        ).limit(5 - len(imaging_list))

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
        .outerjoin(
            BiomarkerDefinition, Observation.biomarker_id == BiomarkerDefinition.id
        )
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

    # Standard reference ranges for common biomarkers
    STANDARD_RANGES = {
        "glucose": {"min": 3.9, "max": 5.6},
        "cholesterol": {"min": 0, "max": 5.2},
        "hdl": {"min": 1.0, "max": 999},
        "ldl": {"min": 0, "max": 3.0},
        "hemoglobin": {"min": 120, "max": 180},
        "white_blood_cells": {"min": 4.0, "max": 11.0},
        "platelets": {"min": 150, "max": 450},
        "creatinine": {"min": 60, "max": 110},
        "tsh": {"min": 0.4, "max": 4.0},
        "vitamin_d": {"min": 50, "max": 125},
        "systolic": {"min": 90, "max": 130},
        "diastolic": {"min": 60, "max": 85},
    }

    unique_labs = {}
    for obs, b_info in all_labs:
        name = obs.code.get("text", "Unknown")
        if name not in unique_labs and len(unique_labs) < 10:
            val = getattr(obs, "normalized_value", None)
            if val is None:
                val = getattr(obs, "raw_value", None)
            if val is None:
                val = (
                    obs.value_quantity.get("value")
                    if obs.value_quantity
                    else obs.value_string
                )

            if val is not None:
                # Basic interpretation
                status = await _get_observation_status(name, val, obs)

                unique_labs[name] = {
                    "name": name,
                    "result": val,
                    "unit": obs.value_quantity.get("unit", "")
                    if obs.value_quantity
                    else "",
                    "date": obs.effective_datetime.isoformat()
                    if obs.effective_datetime
                    else "",
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
            "last_upload": recent_documents[0]["created_at"]
            if recent_documents
            else "",
        },
    }


async def get_biomarker_trends(
    tenant_id: str,
    biomarker_codes: str = None,
    period: str = "last-6-months",
    patient_id: str = None,
    db: AsyncSession = None,
) -> dict:
    if not db:
        return {"biomarkers": []}

    query = select(Observation).where(Observation.tenant_id == tenant_id)
    if patient_id:
        query = query.where(
            Observation.subject["reference"].as_string() == f"Patient/{patient_id}"
        )

    from app.models.biomarker_model import (
        BiomarkerDefinition,
        BiomarkerGroup,
        BiomarkerGroupMember,
        Unit,
    )

    if biomarker_codes:
        codes = [c.strip() for c in biomarker_codes.split(",")]
        from sqlalchemy import or_

        # Join with BiomarkerDefinition to filter by slug as well
        query = query.outerjoin(
            BiomarkerDefinition, Observation.biomarker_id == BiomarkerDefinition.id
        )

        query = query.where(
            or_(
                *[
                    Observation.code["text"].as_string().ilike(f"%{code}%")
                    for code in codes
                ],
                *[BiomarkerDefinition.slug.ilike(f"%{code}%") for code in codes],
            )
        )

    result = await db.execute(query)
    observations = result.scalars().all()

    # Fetch Biomarker Definitions and their Groups
    bio_defs_query = select(
        BiomarkerDefinition, Unit.symbol.label("unit_symbol")
    ).outerjoin(Unit, BiomarkerDefinition.preferred_unit_id == Unit.id)
    bio_defs_res = await db.execute(bio_defs_query)
    bio_defs_rows = bio_defs_res.all()
    bio_map_def = {}
    for b, symbol in bio_defs_rows:
        # Attach symbol to the object dynamically for easier access in loop
        setattr(b, "preferred_unit_symbol", symbol)
        bio_map_def[b.id] = b

    bio_slug_map = {b.slug: b for b in bio_map_def.values()}
    bio_name_map = {b.name.lower(): b for b in bio_map_def.values()}
    # Also include aliases in the name map for better matching
    for b in bio_map_def.values():
        if b.aliases:
            for alias in b.aliases:
                if alias.lower() not in bio_name_map:
                    bio_name_map[alias.lower()] = b

    # Fetch all groups and their members
    groups_query = select(BiomarkerGroup)
    groups_res = await db.execute(groups_query)
    all_groups = groups_res.scalars().all()

    group_members_query = select(BiomarkerGroupMember)
    group_members_res = await db.execute(group_members_query)
    all_members = group_members_res.scalars().all()

    # Map biomarker_id to list of group names
    bio_to_groups = {}
    group_id_to_name = {g.id: g.name for g in all_groups}
    for member in all_members:
        b_id = member.biomarker_id
        g_name = group_id_to_name.get(member.group_id)
        if g_name:
            if b_id not in bio_to_groups:
                bio_to_groups[b_id] = []
            bio_to_groups[b_id].append(g_name)

    doc_ids = [obs.document_id for obs in observations if obs.document_id]
    exam_info_map = {}

    if doc_ids:
        from app.models.examination_category import ExaminationCategory

        doc_query = await db.execute(
            select(
                DocumentModel.id,
                ExaminationModel.id.label("exam_id"),
                ExaminationModel.examination_date,
                ExaminationCategory.name.label("exam_category"),
                DocumentModel.entities,
            )
            .outerjoin(
                ExaminationModel, DocumentModel.examination_id == ExaminationModel.id
            )
            .outerjoin(
                ExaminationCategory,
                ExaminationModel.category_id == ExaminationCategory.id,
            )
            .where(cast(DocumentModel.id, String).in_(doc_ids))
        )
        for row in doc_query.all():
            doc_id = row[0]
            exam_id = row[1]
            exam_date = row[2]
            exam_cat = row[3]
            entities = row[4]

            category = "other"
            if entities and "document_category" in entities:
                from app.core.constants import DOCUMENT_CATEGORIES

                cat_name = entities["document_category"].lower()
                for cat in DOCUMENT_CATEGORIES:
                    if cat["name"].lower() in cat_name or cat["id"] in cat_name:
                        category = cat["id"]
                        break

            exam_info_map[str(doc_id)] = {
                "date": exam_date,
                "category": category,
                "exam_id": str(exam_id) if exam_id else None,
                "exam_name": exam_cat or "General Visit",
            }

    trends = {}
    from datetime import datetime

    for obs in observations:
        name = obs.code.get("text", "Unknown")
        # Get definition info
        b_def = bio_map_def.get(obs.biomarker_id)

        # Use slug as key if available, otherwise fallback to lowercase name
        if b_def and b_def.slug:
            key = b_def.slug
        else:
            key = name.lower()
            # If we don't have a direct b_def by ID, try to find it by slug or name/alias
            if not b_def:
                # Try by slug
                b_def = bio_slug_map.get(key)
                if not b_def:
                    # Try by direct name/alias match
                    b_def = bio_name_map.get(key)

                # Try variants (e.g. "White Blood Cell (WBC)" -> "White Blood Cell")
                if not b_def and "(" in key:
                    stripped = re.sub(r"\(.*?\)", "", key).strip()
                    b_def = bio_name_map.get(stripped) or bio_slug_map.get(stripped)

                # Try stripping plural
                if not b_def and key.endswith("s"):
                    b_def = bio_name_map.get(key[:-1]) or bio_slug_map.get(key[:-1])

                if b_def:
                    # Update key to the official slug
                    key = b_def.slug

        if key not in trends:
            trends[key] = []

        technical_category = b_def.category if b_def else "other"

        # Clinical Groups mapping with fallback to technical category
        clinical_groups = (
            bio_to_groups.get(obs.biomarker_id) if obs.biomarker_id else None
        )
        if not clinical_groups and b_def:
            clinical_groups = bio_to_groups.get(b_def.id)

        if not clinical_groups:
            if b_def and b_def.category:
                # Convert technical category (e.g. 'blood_laboratory') to readable (e.g. 'Blood Laboratory')
                fallback = b_def.category.replace("_", " ").replace("-", " ").title()
                clinical_groups = [fallback]
            else:
                clinical_groups = ["Uncategorized"]

        val = getattr(obs, "normalized_value", None)
        if val is None:
            val = getattr(obs, "raw_value", None)
        if val is None and obs.value_quantity:
            val = obs.value_quantity.get("value")

        if val is not None:
            obs_date = obs.effective_datetime
            source_category = "other"
            exam_id = None
            exam_name = None

            # Get reference range
            ref_range_min = None
            ref_range_max = None
            ref_range_text = "--"

            # Current observation unit
            obs_unit_symbol = (
                obs.value_quantity.get("unit", "") if obs.value_quantity else ""
            )

            if (
                obs.reference_range
                and isinstance(obs.reference_range, list)
                and len(obs.reference_range) > 0
            ):
                low = obs.reference_range[0].get("low", {}).get("value")
                high = obs.reference_range[0].get("high", {}).get("value")
                ref_range_min = low
                ref_range_max = high
            elif b_def:
                # Fallback to global definition
                # Use smarter unit compatibility check
                pref_unit = getattr(b_def, "preferred_unit_symbol", None)
                units_match = False
                if pref_unit and obs_unit_symbol:
                    units_match = units_are_compatible(obs_unit_symbol, pref_unit)
                elif not pref_unit and not obs_unit_symbol:
                    # Both missing units
                    units_match = True

                # Leniency: If we matched the biomarker and the definition has a range,
                # we show it even if units don't perfectly match (best effort),
                # unless they are obviously different (e.g. mass vs molar).
                # For now, we allow it if the observation itself has no range.
                if units_match or (ref_range_min is None and ref_range_max is None):
                    ref_range_min = b_def.reference_range_min
                    ref_range_max = b_def.reference_range_max

            if ref_range_min is not None and ref_range_max is not None:
                ref_range_text = f"{ref_range_min} - {ref_range_max}"
            elif ref_range_min is not None:
                ref_range_text = f"> {ref_range_min}"
            elif ref_range_max is not None:
                ref_range_text = f"< {ref_range_max}"

            # Priority 1: Use direct examination_id if present
            exam_id = str(obs.examination_id) if obs.examination_id else None

            # Priority 2: Use document linkage if direct exam_id is missing
            if (
                not exam_id
                and obs.document_id
                and str(obs.document_id) in exam_info_map
            ):
                info = exam_info_map[str(obs.document_id)]
                exam_date = info["date"]
                source_category = info["category"]
                exam_id = info["exam_id"]
                exam_name = info["exam_name"]
                if exam_date:
                    obs_date = datetime.combine(exam_date, datetime.min.time())
            elif exam_id:
                # If we have direct exam_id, we might still want the name from the map if it's there
                # Or we could fetch it, but for now we try to find it in the map
                for info in exam_info_map.values():
                    if info["exam_id"] == exam_id:
                        exam_name = info["exam_name"]
                        source_category = info["category"]
                        break

            # Determine source type and source name
            source_type = "unknown"
            source_name = "Manual Entry"

            if exam_id:
                source_type = "examination"
                source_name = exam_name or "Clinical Examination"
            elif obs.document_id:
                source_type = "document"
                source_name = "Uploaded Document"
            elif obs.performer and isinstance(obs.performer, list) and len(obs.performer) > 0:
                p = obs.performer[0]
                if p.get("type") == "Integration":
                    source_type = "integration"
                    source_name = p.get("display") or "Integration"

            trends[key].append(
                {
                    "date": obs_date.isoformat() if obs_date else "",
                    "value": val,
                    "unit": obs.value_quantity.get("unit", "")
                    if obs.value_quantity
                    else "",
                    "name": name,
                    "status": await _get_observation_status(
                        name, val, obs, ref_range_min, ref_range_max
                    ),
                    "biomarker_id": str(obs.biomarker_id)
                    if obs.biomarker_id
                    else (str(b_def.id) if b_def else None),
                    "reference_range_min": ref_range_min,
                    "reference_range_max": ref_range_max,
                    "reference_range_text": ref_range_text,
                    "source_type": source_type,
                    "source_name": source_name,
                    "source_category": source_category,
                    "technical_category": technical_category,
                    "clinical_groups": clinical_groups,
                    "examination_id": exam_id,
                    "examination_name": exam_name,
                }
            )

    for key in trends:
        trends[key].sort(key=lambda x: x["date"])

    return {"biomarkers": trends}


async def get_analytics_summary(
    tenant_id: str,
    patient_id: str = None,
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

    doc_query = select(func.count(DocumentModel.id)).where(
        DocumentModel.tenant_id == tenant_id
    )
    if patient_id:
        doc_query = doc_query.where(DocumentModel.patient_id == patient_id)

    result = await db.execute(doc_query)
    total_documents = result.scalar() or 0

    obs_query = select(func.count(Observation.id)).where(
        Observation.tenant_id == tenant_id
    )
    if patient_id:
        obs_query = obs_query.where(
            Observation.subject["reference"].as_string() == f"Patient/{patient_id}"
        )

    result = await db.execute(obs_query)
    total_observations = result.scalar() or 0

    med_query = select(func.count(Medication.id)).where(
        Medication.tenant_id == tenant_id
    )
    if patient_id:
        med_query = med_query.where(Medication.patient_id == patient_id)

    result = await db.execute(med_query)
    total_medications = result.scalar() or 0

    last_upload_query = select(DocumentModel.updated_at).where(
        DocumentModel.tenant_id == tenant_id
    )
    if patient_id:
        last_upload_query = last_upload_query.where(
            DocumentModel.patient_id == patient_id
        )
    last_upload_query = last_upload_query.order_by(
        DocumentModel.updated_at.desc()
    ).limit(1)

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
    patient_id: str = None,
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
            matched_doc_ids.append(str(doc.id))

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
                if obs.document_id and obs.document_id in [
                    str(d.id) for d in documents
                ]:
                    doc_obj = next(
                        (d for d in documents if str(d.id) == obs.document_id), None
                    )
                    if (
                        doc_obj
                        and doc_obj.examination_id
                        and doc_obj.examination_id in exams_map
                    ):
                        exam_date = exams_map[doc_obj.examination_id]
                        if exam_date:
                            obs_date = datetime.combine(exam_date, datetime.min.time())

                trends[key].append(
                    {
                        "date": obs_date.isoformat() if obs_date else "",
                        "value": val,
                        "unit": obs.value_quantity.get("unit", "")
                        if obs.value_quantity
                        else "",
                        "name": name,
                        "relative_score": getattr(obs, "relative_score", None),
                    }
                )

        for key in trends:
            trends[key].sort(key=lambda x: x["date"])

    return {"reports": reports, "biomarkers": trends}
