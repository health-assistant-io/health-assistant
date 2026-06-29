"""AI-assisted form fillers: biomarker entry, medication entry, examination.

Extracted from ``AIAssistanceService`` (Phase 6c). Each handler uses
``llm.with_structured_output(<Pydantic>)`` + ``ChatPromptTemplate`` and returns
``{"suggested_data": ..., "success": True}``.

``AIAssistanceService`` keeps thin delegate methods (``_fill_biomarker_form``
etc.) so the dispatcher and direct test calls (``svc._magic_fill_examination``)
continue to work.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import UUID

from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas.assistance import (
    BiomarkerFormOutput,
    ExaminationMagicFillOutput,
    MedicationFormOutput,
)
from app.models.examination_category import ExaminationCategory
from app.models.fhir.medication import Medication
from app.models.fhir.patient import Observation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lightweight context helpers (style matching for form fills)
# ---------------------------------------------------------------------------


async def _get_recent_biomarkers_context(
    db: AsyncSession, patient_id: UUID, limit: int = 10
) -> List[Dict[str, Any]]:
    """Get lightweight context of recent biomarkers for style matching"""
    patient_ref = f"Patient/{patient_id}"
    result = await db.execute(
        select(Observation)
        .where(Observation.subject["reference"].astext == patient_ref)
        .order_by(desc(Observation.effective_datetime))
        .limit(limit)
    )
    observations = result.scalars().all()
    return [
        {
            "name": obs.code.get("text"),
            "unit": obs.value_quantity.get("unit") if obs.value_quantity else None,
        }
        for obs in observations
    ]


async def _get_recent_medications_context(
    db: AsyncSession, patient_id: UUID, limit: int = 10
) -> List[Dict[str, Any]]:
    """Get lightweight context of recent medications for style matching"""
    result = await db.execute(
        select(Medication)
        .where(Medication.patient_id == patient_id)
        .order_by(desc(Medication.updated_at))
        .limit(limit)
    )
    meds = result.scalars().all()
    return [{"name": med.code.get("text"), "dosage": med.dosage} for med in meds]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def fill_biomarker_form(
    db: AsyncSession, llm, user_input: str, context: Dict[str, Any]
) -> Dict[str, Any]:
    patient_id = context.get("patient_id")
    recent_bios: List[Dict[str, Any]] = []
    if patient_id:
        recent_bios = await _get_recent_biomarkers_context(db, UUID(patient_id))

    system_prompt = """You are a medical assistant helping to fill a biomarker entry form.
Extract the biomarker name, value, unit, and interpretation from the user's input.
Style Matching: The user has previously recorded these biomarkers: {recent_bios}.
If the user mentions a biomarker that matches one of these, prefer the unit they used before.

Interpretation Rules:
- 'low': if the value is below normal
- 'normal': if the value is within normal range
- 'high': if the value is above normal
If not explicitly mentioned, assume 'normal' unless the value is obviously pathological.
"""

    structured_llm = llm.with_structured_output(BiomarkerFormOutput)
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt), ("human", "{user_input}")]
    )

    chain = prompt | structured_llm
    result = await chain.ainvoke(
        {"user_input": user_input, "recent_bios": json.dumps(recent_bios)}
    )

    data = result.model_dump()
    if data.get("interpretation"):
        data["interpretation"] = data["interpretation"].lower()
        if data["interpretation"] not in ["low", "normal", "high"]:
            data["interpretation"] = "normal"

    return {"suggested_data": data, "success": True}


async def fill_medication_form(
    db: AsyncSession, llm, user_input: str, context: Dict[str, Any]
) -> Dict[str, Any]:
    patient_id = context.get("patient_id")
    recent_meds: List[Dict[str, Any]] = []
    if patient_id:
        recent_meds = await _get_recent_medications_context(db, UUID(patient_id))

    system_prompt = """You are a medical assistant helping to record a new medication.
Extract the medication name, dosage, frequency, reason, and any notes from the user's input.
Frequency should be a clear label like 'Once Daily', 'Twice Daily', 'Every 8 hours', etc.

Style Matching: The patient currently takes: {recent_meds}.
"""

    structured_llm = llm.with_structured_output(MedicationFormOutput)
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt), ("human", "{user_input}")]
    )

    chain = prompt | structured_llm
    result = await chain.ainvoke(
        {"user_input": user_input, "recent_meds": json.dumps(recent_meds)}
    )

    return {"suggested_data": result.model_dump(), "success": True}


async def magic_fill_examination(
    db: AsyncSession, llm, user_input: str, context: Dict[str, Any]
) -> Dict[str, Any]:
    """AI-driven examination form filler"""
    tenant_id = context.get("tenant_id")

    # Fetch custom categories from the database
    cat_res = await db.execute(
        select(ExaminationCategory).where(
            or_(
                ExaminationCategory.tenant_id == tenant_id,
                ExaminationCategory.tenant_id.is_(None),
            )
        )
    )
    existing_slugs = [c.slug for c in cat_res.scalars().all()]
    if not existing_slugs:
        from app.core.constants import DOCUMENT_CATEGORIES

        existing_slugs = [c["id"] for c in DOCUMENT_CATEGORIES]

    slugs_str = ", ".join(existing_slugs)

    # Inject the live date so relative-date parsing stays accurate.
    _now = datetime.now(timezone.utc)
    today_iso = _now.strftime("%Y-%m-%d")
    current_year = _now.year

    system_prompt = f"""You are a medical assistant helping to record a new examination visit.
Extract the examination date, clinical notes, patient notes, category slug, and any doctor names from the user's input.

Doctor Names: Omit titles like 'Dr.', 'Doctor', 'MD', etc. Only return the actual name (e.g. return 'Smith' or 'John Smith' instead of 'Dr. Smith').

Available Category SLUGS: {slugs_str}
Pick EXACTLY one most appropriate category SLUG from the list above.
If unsure, you may suggest a new compact clinical specialty slug (e.g., 'dermatology').
Do NOT concatenate multiple categories. Pick the primary one.
Ensure the slug is lowercase and uses kebab-case.

Output the date in ISO format (YYYY-MM-DD). If no year is mentioned, assume {current_year}.
If no month or day is mentioned, use today's date if appropriate or leave null.
Today's date is {today_iso}.
"""

    structured_llm = llm.with_structured_output(ExaminationMagicFillOutput)
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt), ("human", "{user_input}")]
    )

    chain = prompt | structured_llm
    result = await chain.ainvoke({"user_input": user_input})

    return {"suggested_data": result.model_dump(), "success": True}
