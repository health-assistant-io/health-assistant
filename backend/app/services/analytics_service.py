from typing import Any
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, String, cast
from app.models.document_model import DocumentModel
from app.models.examination_model import ExaminationModel
from app.models.user_model import UserModel

# FHIR models
from app.models.fhir import Observation, Medication, DiagnosticReport
from app.services.fhir_helpers import _flatten_interpretation

import re
import logging

logger = logging.getLogger(__name__)

# Strict whitelist for the INTERVAL '{bucket}' SQL interpolation.
# Values not in this set fall back to "1 hour". The list mirrors the
# PERIOD_MAPPING defaults + the auto-calculated buckets.
_ALLOWED_TELEMETRY_BUCKETS = frozenset(
    {
        "1 minute",
        "5 minutes",
        "15 minutes",
        "30 minutes",
        "1 hour",
        "6 hours",
        "12 hours",
        "1 day",
        "1 week",
        "1 month",
    }
)


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
    interp_raw = _flatten_interpretation(getattr(obs, "interpretation", None))
    if interp_raw:
        interp = interp_raw.upper()
        if interp in ["H", "HIGH", "A", "ABNORMAL", "E", "ELEVATED"]:
            return "High"
        if interp in ["L", "LOW", "D", "DECREASED"]:
            return "Low"
        if interp in ["N", "NORMAL"]:
            return "Normal"

    # 2. Use relative score (clamped to [0.0, 1.0] by ObservationBuilder).
    # A strictly-interior score (0 < score < 1) genuinely means the value
    # sits inside the reference range, so Normal is correct there. Boundary
    # values (exactly 0.0 or 1.0) are ambiguous after clamping: the value
    # could be exactly at the bound (still normal) or beyond it (abnormal).
    # Rather than guess, fall through to the explicit reference-range
    # comparison in step 3 which has the raw value + bounds to decide.
    if getattr(obs, "relative_score", None) is not None:
        if 0.0 < obs.relative_score < 1.0:
            return "Normal"
        # score at boundary (0.0 or 1.0) — defer to the range check below.

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
    from app.models.concept_model import Concept

    doc_imaging_query = (
        select(DocumentModel, Concept.name.label("exam_category"))
        .outerjoin(
            ExaminationModel, DocumentModel.examination_id == ExaminationModel.id
        )
        .outerjoin(Concept, ExaminationModel.category_concept_id == Concept.id)
        .where(DocumentModel.tenant_id == tenant_id)
    )

    if patient_id:
        doc_imaging_query = doc_imaging_query.where(
            DocumentModel.patient_id == patient_id
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
    from app.models.biomarker_model import BiomarkerDefinition

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
    aggregation: str = None,
    patient_id: str = None,
    start_date: datetime = None,
    end_date: datetime = None,
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
        Unit,
    )

    if biomarker_codes:
        codes = [c.strip() for c in biomarker_codes.split(",")]
        from sqlalchemy import or_
        import uuid

        # Resolve the requested codes/slugs against the definition catalog so we
        # can expand the filter to include the matched definitions' names,
        # aliases, and LOINC codes. This is essential for catching UNMAPPED
        # observations (biomarker_id is NULL) whose stored code.text is the raw
        # lab label (e.g. "WBC", "Leukocytes") rather than the canonical slug.
        # Without this, the detail page shows "No historical data" even though
        # the unfiltered trends page resolves the same data via fallback.
        uuid_codes = []
        text_codes = []
        for c in codes:
            try:
                uuid.UUID(c)
                uuid_codes.append(c)
            except ValueError:
                text_codes.append(c)

        expanded_terms = list(text_codes)
        matched_def_ids = []

        def_filters = []
        if text_codes:
            def_filters.append(
                or_(
                    *[BiomarkerDefinition.slug.ilike(f"%{c}%") for c in text_codes],
                    *[BiomarkerDefinition.name.ilike(f"%{c}%") for c in text_codes],
                    *[BiomarkerDefinition.code.ilike(f"%{c}%") for c in text_codes],
                )
            )
        if uuid_codes:
            def_filters.append(
                BiomarkerDefinition.id.in_([uuid.UUID(u) for u in uuid_codes])
            )

        if def_filters:
            def_filter = or_(*def_filters)
            def_res = await db.execute(select(BiomarkerDefinition).where(def_filter))
            for b in def_res.scalars().all():
                matched_def_ids.append(b.id)
                if b.name:
                    expanded_terms.append(b.name)
                if b.aliases:
                    expanded_terms.extend(a for a in b.aliases if a)

        # Deduplicate while preserving order
        seen = set()
        expanded_terms = [
            t for t in expanded_terms if not (t.lower() in seen or seen.add(t.lower()))
        ]

        # Join with BiomarkerDefinition to filter by slug (mapped observations)
        query = query.outerjoin(
            BiomarkerDefinition, Observation.biomarker_id == BiomarkerDefinition.id
        )

        filter_clauses = []
        # Match by expanded text terms (raw codes + definition names/aliases)
        for term in expanded_terms:
            filter_clauses.append(
                Observation.code["text"].as_string().ilike(f"%{term}%")
            )
        # Match by slug via the join (mapped observations)
        for code in text_codes:
            filter_clauses.append(BiomarkerDefinition.slug.ilike(f"%{code}%"))
        # Match by biomarker_id directly (mapped observations)
        if matched_def_ids:
            filter_clauses.append(Observation.biomarker_id.in_(matched_def_ids))

        query = query.where(or_(*filter_clauses))

    if start_date:
        query = query.where(Observation.effective_datetime >= start_date)
    if end_date:
        query = query.where(Observation.effective_datetime <= end_date)

    # Limit the total number of observations fetched to prevent massive unpaginated queries crashing the server
    # 2000 is usually enough for a single patient's trend history
    query = query.limit(2000)

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

    # Fetch clinical groups via concept_edges (MEMBER_OF) — replaces the old
    # BiomarkerGroup/BiomarkerGroupMember tables. A biomarker may belong to one
    # or more panel concepts; we group by the destination concept's name.
    from app.models.concept_model import Concept, ConceptEdge
    from app.models.enums import (
        EdgeEndpointType,
        ConceptRelationType,
        EdgeApprovalStatus,
    )

    edges_res = await db.execute(
        select(ConceptEdge).where(
            ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
            ConceptEdge.relation == ConceptRelationType.MEMBER_OF,
            ConceptEdge.status == EdgeApprovalStatus.APPROVED,
        )
    )
    all_edges = edges_res.scalars().all()
    panel_ids = {e.dst_id for e in all_edges}

    panel_name_map: dict = {}
    if panel_ids:
        panel_res = await db.execute(
            select(Concept.id, Concept.name).where(Concept.id.in_(panel_ids))
        )
        panel_name_map = {pid: name for pid, name in panel_res.all()}

    # Map biomarker_id to list of group (panel) names
    bio_to_groups = {}
    for edge in all_edges:
        g_name = panel_name_map.get(edge.dst_id)
        if g_name:
            bio_to_groups.setdefault(edge.src_id, []).append(g_name)

    # Optimize queries by fetching everything we need for exam info in one go
    # Only fetch doc_ids we actually care about
    doc_ids = list(set([obs.document_id for obs in observations if obs.document_id]))
    exam_info_map = {}

    if doc_ids:
        from app.models.concept_model import Concept as _Concept

        # Do it in chunks if there are too many documents
        chunk_size = 500
        for i in range(0, len(doc_ids), chunk_size):
            chunk = doc_ids[i : i + chunk_size]
            doc_query = await db.execute(
                select(
                    DocumentModel.id,
                    ExaminationModel.id.label("exam_id"),
                    ExaminationModel.examination_date,
                    _Concept.name.label("exam_category"),
                    DocumentModel.entities,
                )
                .outerjoin(
                    ExaminationModel,
                    DocumentModel.examination_id == ExaminationModel.id,
                )
                .outerjoin(
                    _Concept,
                    ExaminationModel.category_concept_id == _Concept.id,
                )
                .where(cast(DocumentModel.id, String).in_(chunk))
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

        # Prefer the canonical biomarker definition name (clean, standardized)
        # over the raw observation code.text, which may be unformatted lab/OCR
        # output (e.g. "GLUCOSE (FASTING)"). Keeps the list consistent with the
        # biomarker detail page, which shows the definition name.
        if b_def and b_def.name:
            name = b_def.name

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
                    from datetime import timezone

                    obs_date = datetime.combine(
                        exam_date, datetime.min.time(), tzinfo=timezone.utc
                    )
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
            source_id = None

            if exam_id:
                source_type = "examination"
                source_name = exam_name or "Clinical Examination"
            elif obs.document_id:
                source_type = "document"
                source_name = "Uploaded Document"
            elif (
                obs.performer
                and isinstance(obs.performer, list)
                and len(obs.performer) > 0
            ):
                p = obs.performer[0]
                if p.get("type") == "Integration":
                    source_type = "integration"
                    source_name = p.get("display") or "Integration"
                    ref = p.get("reference", "")
                    if ref.startswith("Integration/"):
                        source_id = ref.split("/")[1]
                    else:
                        source_id = source_name  # Fallback to domain if no UUID stored

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
                    "source_id": source_id,
                    "source_category": source_category,
                    "technical_category": technical_category,
                    "clinical_groups": clinical_groups,
                    "examination_id": exam_id,
                    "examination_name": exam_name,
                }
            )

    # Telemetry Data Aggregation using TimescaleDB
    from sqlalchemy import text
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    PERIOD_MAPPING = {
        "last-1-hour": {"delta": timedelta(hours=1), "bucket": "1 minute"},
        "last-6-hours": {"delta": timedelta(hours=6), "bucket": "5 minutes"},
        "last-24-hours": {"delta": timedelta(days=1), "bucket": "15 minutes"},
        "last-7-days": {"delta": timedelta(days=7), "bucket": "1 hour"},
        "last-30-days": {"delta": timedelta(days=30), "bucket": "1 day"},
        "last-90-days": {"delta": timedelta(days=90), "bucket": "1 day"},
        "last-12-months": {"delta": timedelta(days=365), "bucket": "1 week"},
        "last-year": {"delta": timedelta(days=365), "bucket": "1 week"},
        "last-6-months": {"delta": timedelta(days=180), "bucket": "1 day"},
        "all-time": {"delta": timedelta(days=3650), "bucket": "1 month"},
    }

    config = PERIOD_MAPPING.get(period, PERIOD_MAPPING["last-6-months"])

    effective_start_date = start_date if start_date else (now - config["delta"])
    effective_end_date = end_date if end_date else now

    bucket = aggregation if aggregation else config["bucket"]
    if start_date and end_date and not aggregation:
        # Auto-calculate a sensible bucket if custom dates are provided without one
        delta_days = (effective_end_date - effective_start_date).days
        if delta_days <= 1:
            bucket = "15 minutes"
        elif delta_days <= 7:
            bucket = "1 hour"
        elif delta_days <= 90:
            bucket = "1 day"
        elif delta_days <= 365:
            bucket = "1 week"
        else:
            bucket = "1 month"

    telemetry_slugs = []
    telemetry_defs = {}
    for b in bio_map_def.values():
        if getattr(b, "is_telemetry", False):
            telemetry_slugs.append(b.slug)
            telemetry_defs[b.slug] = b

    if biomarker_codes:
        codes = [c.strip() for c in biomarker_codes.split(",")]
        import uuid

        uuid_codes = []
        text_codes = []
        for c in codes:
            try:
                uuid.UUID(c)
                uuid_codes.append(c)
            except ValueError:
                text_codes.append(c)

        if uuid_codes:
            # Resolve UUIDs to slugs for telemetry querying
            def_res = await db.execute(
                select(BiomarkerDefinition.slug).where(
                    BiomarkerDefinition.id.in_([uuid.UUID(u) for u in uuid_codes])
                )
            )
            text_codes.extend([s for s in def_res.scalars().all() if s])

        telemetry_to_query = [
            s
            for s in telemetry_slugs
            if any(c.lower() in s.lower() for c in text_codes)
        ]
    else:
        telemetry_to_query = telemetry_slugs

    for slug in telemetry_to_query:
        if slug == "8867-4" or "heart-rate" in slug:
            col = "heart_rate"
            where_clause = "heart_rate IS NOT NULL"
        elif slug == "41950-7" or "steps" in slug:
            col = "steps"
            where_clause = "steps IS NOT NULL"
        elif "calories" in slug:
            col = "calories"
            where_clause = "calories IS NOT NULL"
        else:
            col = f"CAST(data->>'{slug}' AS FLOAT)"
            where_clause = f"data ? '{slug}'"

        table_name = "telemetry_data"
        time_col = "timestamp"

        # Separate the "aggregate expression" (what goes in the SELECT)
        # from the "source table". For cagg tables the columns are already
        # pre-aggregated so we wrap max/min (correct for extremes) and use
        # AVG(_avg) for the mean (best we can do without a count column).
        # For the raw hypertable we aggregate directly — AVG(col), not
        # AVG(AVG(col)).
        if bucket == "1 hour" and col in ["heart_rate", "steps", "calories"]:
            table_name = "telemetry_hourly"
            time_col = "bucket"
            avg_expr = f"AVG({col}_avg)"
            max_expr = f"MAX({col}_max)"
            min_expr = f"MIN({col}_min)"
        elif bucket == "1 day" and col in ["heart_rate", "steps", "calories"]:
            table_name = "telemetry_daily"
            time_col = "bucket"
            avg_expr = f"AVG({col}_avg)"
            max_expr = f"MAX({col}_max)"
            min_expr = f"MIN({col}_min)"
        else:
            # Raw hypertable — single-level aggregation (no double-wrapping).
            avg_expr = f"AVG({col})"
            max_expr = f"MAX({col})"
            min_expr = f"MIN({col})"

        # Validate the bucket interval against a strict whitelist.
        # The value is interpolated into INTERVAL '{bucket}' (PostgreSQL
        # INTERVAL literals don't accept bind parameters). The default comes
        # from PERIOD_MAPPING but an API caller can pass aggregation=...,
        # so we must guard against arbitrary strings here.
        safe_bucket = bucket if bucket in _ALLOWED_TELEMETRY_BUCKETS else "1 hour"

        sql = f"""
            SELECT 
                time_bucket_gapfill(INTERVAL '{safe_bucket}', {time_col}) AS bucket,
                device_id,
                {avg_expr} as avg_val,
                {max_expr} as max_val,
                {min_expr} as min_val
            FROM {table_name}
            WHERE tenant_id = :tenant_id
              AND {time_col} >= :start_date AND {time_col} <= :end_date
              AND {where_clause}
            GROUP BY bucket, device_id
            ORDER BY bucket
        """

        try:
            res = await db.execute(
                text(sql),
                {
                    "tenant_id": tenant_id,
                    "start_date": effective_start_date,
                    "end_date": effective_end_date,
                },
            )
            rows = res.all()

            if rows:
                if slug not in trends:
                    trends[slug] = []

                b_def = telemetry_defs[slug]
                technical_category = b_def.category if b_def else "other"

                clinical_groups = bio_to_groups.get(b_def.id) if b_def else None
                if not clinical_groups:
                    if b_def and b_def.category:
                        clinical_groups = [
                            b_def.category.replace("_", " ").replace("-", " ").title()
                        ]
                    else:
                        clinical_groups = ["Telemetry"]

                for row in rows:
                    bucket_date = row.bucket
                    device_id = row.device_id
                    avg_val = row.avg_val
                    max_val = row.max_val
                    min_val = row.min_val

                    if avg_val is None:
                        continue  # Gapfill returned null

                    trends[slug].append(
                        {
                            "date": bucket_date.isoformat() if bucket_date else "",
                            "value": round(avg_val, 2),
                            "max_value": round(max_val, 2),
                            "min_value": round(min_val, 2),
                            "unit": getattr(b_def, "preferred_unit_symbol", ""),
                            "name": b_def.name if b_def else slug,
                            "status": "Normal",
                            "biomarker_id": str(b_def.id) if b_def else None,
                            "reference_range_min": b_def.reference_range_min
                            if b_def
                            else None,
                            "reference_range_max": b_def.reference_range_max
                            if b_def
                            else None,
                            "reference_range_text": f"{b_def.reference_range_min} - {b_def.reference_range_max}"
                            if b_def and b_def.reference_range_min
                            else "--",
                            "source_type": "telemetry",
                            "source_name": device_id or "IoT Device",
                            "source_id": None,
                            "source_category": "telemetry",
                            "technical_category": technical_category,
                            "clinical_groups": clinical_groups,
                            "examination_id": None,
                            "examination_name": None,
                        }
                    )
        except Exception as e:
            logger.error(
                f"Failed to query telemetry data for '{slug}': {e}", exc_info=True
            )
            # If timescale is not perfectly configured or missing data, skip
            pass

    for key in trends:
        trends[key].sort(key=lambda x: x["date"])

    return {"biomarkers": trends}


async def get_biomarker_anomalies(
    tenant_id: str,
    biomarker_codes: str = None,
    patient_id: str = None,
    db: AsyncSession = None,
) -> dict:
    """Detect anomalies in biomarker trends by reusing the trends pipeline
    and running statistical + reference-range analysis on each series."""
    if not db:
        return {"anomalies": []}

    from app.services.anomaly_detector import AnomalyDetector

    detector = AnomalyDetector()

    trends_data = await get_biomarker_trends(
        tenant_id=tenant_id,
        biomarker_codes=biomarker_codes,
        period="all-time",
        patient_id=patient_id,
        db=db,
    )

    anomalies: list = []
    biomarkers = trends_data.get("biomarkers", {})

    for slug, points in biomarkers.items():
        if not points:
            continue

        latest = points[-1]
        historical = [{"value": p["value"], "date": p.get("date")} for p in points[:-1]]
        new_value = {"value": latest["value"], "date": latest.get("date")}

        biomarker_name = latest.get("name", slug)
        biomarker_id = latest.get("biomarker_id")
        unit = latest.get("unit", "")
        ref_min = latest.get("reference_range_min")
        ref_max = latest.get("reference_range_max")
        value = latest["value"]

        for a in detector.detect_biomarker_anomalies(historical, new_value):
            a["biomarker"] = biomarker_name
            a["biomarker_slug"] = slug
            a["biomarker_id"] = biomarker_id
            a["value"] = value
            a["unit"] = unit
            anomalies.append(a)

        if ref_min is not None and ref_max is not None and ref_max > ref_min:
            if value < ref_min:
                anomalies.append(
                    {
                        "type": "below_reference",
                        "biomarker": biomarker_name,
                        "biomarker_slug": slug,
                        "biomarker_id": biomarker_id,
                        "value": value,
                        "unit": unit,
                        "message": f"Value {value} {unit} is below the reference minimum of {ref_min} {unit}",
                        "severity": "warning" if value < ref_min * 0.9 else "info",
                    }
                )
            elif value > ref_max:
                anomalies.append(
                    {
                        "type": "above_reference",
                        "biomarker": biomarker_name,
                        "biomarker_slug": slug,
                        "biomarker_id": biomarker_id,
                        "value": value,
                        "unit": unit,
                        "message": f"Value {value} {unit} is above the reference maximum of {ref_max} {unit}",
                        "severity": "warning" if value > ref_max * 1.1 else "info",
                    }
                )

    severity_order = {"critical": 0, "warning": 1, "info": 2}
    anomalies.sort(key=lambda a: severity_order.get(a.get("severity", "info"), 3))

    return {"anomalies": anomalies}


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
                            from datetime import timezone

                            obs_date = datetime.combine(
                                exam_date, datetime.min.time(), tzinfo=timezone.utc
                            )

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
