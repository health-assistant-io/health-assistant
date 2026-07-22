"""Allergy tools for the agentic chat.

Mirrors :mod:`app.ai.tools.medications`: read-only chat tools that let the
agent answer "what is the patient allergic to?", surface the allergen catalog,
and search for existing entries before proposing new ones. Writes are still
HITL-gated (see ``propose_record_allergy`` / ``propose_define_allergy``).
"""

import json
from typing import Any, List

from langchain_core.tools import tool
from sqlalchemy import and_, desc, select

from app.ai.tools.registry import ToolContext, register_chat_tool
from app.models.fhir.allergy import AllergyCatalog, AllergyIntolerance


@register_chat_tool("allergies")
def build(ctx: ToolContext) -> List[Any]:
    @tool
    async def get_current_allergies() -> str:
        """Fetch the list of ACTIVE allergies currently on the patient's chart.

        Returns one entry per active intolerance with the allergen name,
        criticality, category, reactions, onset, and last occurrence.
        """
        from app.models.enums import AllergyClinicalStatus

        result = await ctx.db.execute(
            select(AllergyIntolerance).where(
                and_(
                    AllergyIntolerance.patient_id == ctx.patient_id,
                    AllergyIntolerance.tenant_id == ctx.tenant_id,
                    AllergyIntolerance.clinical_status
                    == AllergyClinicalStatus.ACTIVE,
                    AllergyIntolerance.deleted_at.is_(None),
                )
            )
        )
        rows = result.scalars().all()

        summary = []
        for a in rows:
            summary.append(
                {
                    "id": str(a.id),
                    "name": (a.code or {}).get("text"),
                    "criticality": a.criticality.value if a.criticality else None,
                    "category": a.category.value if a.category else None,
                    "onset_date": a.onset_date.isoformat() if a.onset_date else None,
                    "last_occurrence": a.last_occurrence.isoformat()
                    if a.last_occurrence
                    else None,
                    "reactions": a.reactions or [],
                    "note": a.note,
                }
            )
        return json.dumps(summary)

    @tool
    async def get_patient_allergy_history(limit: int = 50) -> str:
        """Fetch the full allergy history (active + inactive + resolved) for the patient."""
        result = await ctx.db.execute(
            select(AllergyIntolerance)
            .where(
                and_(
                    AllergyIntolerance.patient_id == ctx.patient_id,
                    AllergyIntolerance.tenant_id == ctx.tenant_id,
                    AllergyIntolerance.deleted_at.is_(None),
                )
            )
            .order_by(
                desc(AllergyIntolerance.onset_date), desc(AllergyIntolerance.created_at)
            )
            .limit(limit)
        )
        rows = result.scalars().all()

        history = []
        for a in rows:
            history.append(
                {
                    "id": str(a.id),
                    "name": (a.code or {}).get("text"),
                    "clinical_status": a.clinical_status.value
                    if a.clinical_status
                    else "unknown",
                    "criticality": a.criticality.value if a.criticality else None,
                    "category": a.category.value if a.category else None,
                    "onset_date": a.onset_date.isoformat() if a.onset_date else None,
                    "resolved_date": a.resolved_date.isoformat()
                    if a.resolved_date
                    else None,
                    "reactions": a.reactions or [],
                    "note": a.note,
                }
            )
        return json.dumps(history)

    @tool
    async def get_allergy_catalog_details(allergy_id: str) -> str:
        """Fetch informational details about an allergen from the clinical
        catalog (description, typical reactions, category).
        """
        from uuid import UUID

        try:
            catalog_uuid = UUID(allergy_id)
        except ValueError:
            return "Invalid allergy ID format."

        result = await ctx.db.execute(
            select(AllergyCatalog).where(AllergyCatalog.id == catalog_uuid)
        )
        entry = result.scalars().first()
        if not entry:
            return "Allergy not found in catalog."

        return json.dumps(entry.to_dict())

    @tool
    async def search_allergens(
        search_term: str,
        limit: int = 10,
    ) -> str:
        """Search the allergy catalog (tenant-scoped + globals).

        Hybrid search (trigram + full-text + RRF): matches the allergen name
        AND the description / typical reactions. Use this BEFORE proposing to
        record an allergy so you reuse an existing catalog_id instead of
        creating a duplicate.

        Args:
            search_term: Allergen name, category, or symptom (e.g. "peanuts",
                "penicillin", "hives").
            limit: Max results (default 10).

        Returns JSON: [{id, name, category, description, typical_reactions,
        matched_on, snippet}].
        """
        from app.services.catalog_search_service import search_catalogs as _search

        results = await _search(
            ctx.db,
            ctx.tenant_id,
            search_term,
            types=["allergy"],
            limit_total=limit,
        )
        out = []
        for r in results:
            out.append(
                {
                    "id": r["id"],
                    "name": r.get("name") or r.get("label"),
                    "category": r.get("category"),
                    "description": r.get("description"),
                    "typical_reactions": r.get("typical_reactions", []),
                    "matched_on": r.get("matched_on", []),
                    "snippet": r.get("snippet"),
                }
            )
        return json.dumps(out)

    return [
        get_current_allergies,
        get_patient_allergy_history,
        get_allergy_catalog_details,
        search_allergens,
    ]
