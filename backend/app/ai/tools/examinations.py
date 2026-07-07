"""Examination tools for the agentic chat.

Extracted from ``ChatbotTools`` (Phase 3). Includes the only write tool
(``update_examination_notes``).
"""

import json
from typing import Any, List
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import and_, desc, select
from sqlalchemy.orm import selectinload

from app.ai.tools.registry import ToolContext, register_chat_tool
from app.models.examination_model import ExaminationModel


@register_chat_tool("examinations")
def build(ctx: ToolContext) -> List[Any]:
    @tool
    async def get_recent_examinations(limit: int = 5) -> str:
        """Fetch a list of recent clinical examinations/visits for the patient.
        Returns exam dates, categories, and summary notes."""
        result = await ctx.db.execute(
            select(ExaminationModel)
            .options(selectinload(ExaminationModel.category_concept))
            .where(
                and_(
                    ExaminationModel.patient_id == ctx.patient_id,
                    ExaminationModel.tenant_id == ctx.tenant_id,
                )
            )
            .order_by(desc(ExaminationModel.examination_date))
            .limit(limit)
        )
        exams = result.scalars().all()

        # Lightweight mapping: avoid deeply nested observations/medications
        summary = []
        for exam in exams:
            summary.append(
                {
                    "id": str(exam.id),
                    "date": exam.examination_date.isoformat()
                    if exam.examination_date
                    else None,
                    "category": exam.category_concept.name
                    if exam.category_concept
                    else None,
                    "notes": exam.notes[:500]
                    if exam.notes
                    else None,  # Truncate long notes
                    "diagnoses": exam.diagnoses,
                }
            )
        return json.dumps(summary)

    @tool
    async def get_examination_details(examination_id: str) -> str:
        """Fetch comprehensive details of a specific examination (clinical visit).
        Returns notes, diagnoses, impressions, and lists of associated biomarkers and medications."""
        try:
            exam_uuid = UUID(examination_id)
        except ValueError:
            return "Invalid examination ID format."

        result = await ctx.db.execute(
            select(ExaminationModel)
            .options(selectinload(ExaminationModel.category_concept))
            .where(
                and_(
                    ExaminationModel.id == exam_uuid,
                    ExaminationModel.tenant_id == ctx.tenant_id,
                    ExaminationModel.patient_id == ctx.patient_id,
                )
            )
        )
        exam = result.scalars().first()
        if not exam:
            return "Examination not found or access denied."

        # Map the core examination data
        summary = {
            "id": str(exam.id),
            "date": exam.examination_date.isoformat()
            if exam.examination_date
            else None,
            "category": exam.category_concept.name if exam.category_concept else None,
            "notes": exam.notes,
            "patient_notes": exam.patient_notes,
            "diagnoses": exam.diagnoses,
            "impressions": exam.impressions,
            "biomarkers": [],
            "medications": [],
            "documents": [],
        }

        # Map associated documents
        for doc in exam.documents:
            summary["documents"].append(
                {
                    "id": str(doc.id),
                    "filename": doc.filename,
                    "status": doc.status,
                }
            )

        # Map associated biomarkers (Observations)
        # Limit to 40 for efficiency in the context window
        for obs in exam.observations[:40]:
            summary["biomarkers"].append(
                {
                    "id": str(obs.id),
                    "biomarker_id": str(obs.biomarker_id) if obs.biomarker_id else None,
                    "name": obs.code.get("text"),
                    "value": obs.value_quantity.get("value")
                    if obs.value_quantity
                    else obs.value_string,
                    "unit": obs.value_quantity.get("unit")
                    if obs.value_quantity
                    else None,
                    "interpretation": obs.interpretation,
                    "date": obs.effective_datetime.isoformat()
                    if obs.effective_datetime
                    else None,
                }
            )

        # Map associated medications
        for med in exam.medications:
            summary["medications"].append(
                {
                    "id": str(med.id),
                    "name": med.code.get("text"),
                    "status": med.status.value if med.status else None,
                    "dosage": med.dosage,
                    "frequency": med.frequency,
                    "reason": med.reason,
                }
            )

        return json.dumps(summary)

    @tool
    async def update_examination_notes(examination_id: str, notes: str) -> str:
        """Update the clinician notes for a specific examination."""
        result = await ctx.db.execute(
            select(ExaminationModel).where(
                and_(
                    ExaminationModel.id == UUID(examination_id),
                    ExaminationModel.tenant_id == ctx.tenant_id,
                    ExaminationModel.patient_id == ctx.patient_id,
                )
            )
        )
        exam = result.scalars().first()
        if not exam:
            return "Examination not found or access denied."

        exam.notes = notes
        await ctx.db.commit()
        return f"Successfully updated notes for examination on {exam.examination_date}."

    return [get_recent_examinations, get_examination_details, update_examination_notes]
