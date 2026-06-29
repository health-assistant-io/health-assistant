"""Medication tools for the agentic chat.

Extracted from ``ChatbotTools`` (Phase 3).
"""
import json
from typing import Any, List

from langchain_core.tools import tool
from sqlalchemy import and_, desc, select

from app.ai.tools.registry import ToolContext, register_chat_tool
from app.models.fhir.medication import Medication, MedicationCatalog


@register_chat_tool("medications")
def build(ctx: ToolContext) -> List[Any]:
    @tool
    async def get_current_medications() -> str:
        """Fetch the list of medications currently prescribed to the patient."""
        from app.models.enums import MedicationStatus

        result = await ctx.db.execute(
            select(Medication).where(
                and_(
                    Medication.patient_id == ctx.patient_id,
                    Medication.tenant_id == ctx.tenant_id,
                    Medication.status == MedicationStatus.ACTIVE,
                )
            )
        )
        meds = result.scalars().all()

        summary = []
        for med in meds:
            summary.append(
                {
                    "id": str(med.id),
                    "name": med.code.get("text"),
                    "dosage": med.dosage,
                    "frequency": med.frequency,
                    "start_date": med.start_date.isoformat()
                    if med.start_date
                    else None,
                    "reason": med.reason,
                }
            )
        return json.dumps(summary)

    @tool
    async def get_patient_medication_history(limit: int = 20) -> str:
        """Fetch the historical list of all medications prescribed to the patient, including inactive or completed ones."""
        result = await ctx.db.execute(
            select(Medication)
            .where(
                and_(
                    Medication.patient_id == ctx.patient_id,
                    Medication.tenant_id == ctx.tenant_id,
                )
            )
            .order_by(desc(Medication.start_date))
            .limit(limit)
        )
        meds = result.scalars().all()

        history = []
        for med in meds:
            history.append(
                {
                    "id": str(med.id),
                    "name": med.code.get("text"),
                    "status": med.status.value if med.status else "unknown",
                    "dosage": med.dosage,
                    "frequency": med.frequency,
                    "start_date": med.start_date.isoformat()
                    if med.start_date
                    else None,
                    "end_date": med.end_date.isoformat() if med.end_date else None,
                    "reason": med.reason,
                }
            )
        return json.dumps(history)

    @tool
    async def get_medication_catalog_details(medication_id: str) -> str:
        """Fetch informational details about a medication from the clinical catalog, including indications and side effects."""
        from uuid import UUID

        try:
            med_uuid = UUID(medication_id)
        except ValueError:
            return "Invalid medication ID format."

        result = await ctx.db.execute(
            select(MedicationCatalog).where(MedicationCatalog.id == med_uuid)
        )
        med = result.scalars().first()
        if not med:
            return "Medication not found in catalog."

        return json.dumps(med.to_dict())

    @tool
    async def search_medications(
        search_term: str,
        limit: int = 10,
    ) -> str:
        """Search the medication catalog (tenant-scoped + globals) by name or indication.
        Typo-tolerant (trigram similarity + substring fallback): returns the closest
        matches ranked by similarity so you can resolve "ibuprofin" / "paracetamol" /
        "Glucophage" to the canonical catalog entry. Use this BEFORE proposing to add
        a medication so you reuse an existing catalog_id instead of creating a duplicate.

        Args:
            search_term: Drug name, generic name, or symptom/indication fragment.
            limit: Max results (default 10).
        """
        from app.services.catalog_search_service import search_medications as _search

        meds = await _search(ctx.db, ctx.tenant_id, search_term, limit=limit)
        return json.dumps([m.to_dict() for m in meds])

    return [
        get_current_medications,
        get_patient_medication_history,
        get_medication_catalog_details,
        search_medications,
    ]
