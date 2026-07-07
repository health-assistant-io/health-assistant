"""Biomarker tools for the agentic chat.

Extracted from ``ChatbotTools`` (Phase 3). Covers clinical lab history,
telemetry trends, catalog search, and definition lookup.
"""

import json
from typing import Any, List, Optional
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import and_, desc, select

from app.ai.tools.registry import ToolContext, register_chat_tool
from app.models.biomarker_model import BiomarkerDefinition
from app.models.fhir.patient import Observation


@register_chat_tool("biomarkers")
def build(ctx: ToolContext) -> List[Any]:
    @tool
    async def get_recent_biomarkers(limit: int = 15) -> str:
        """Fetch the most recent biomarker observations (lab results) for the patient.
        Returns a list of results with values, units, and interpretations."""
        patient_ref = f"Patient/{ctx.patient_id}"
        result = await ctx.db.execute(
            select(Observation)
            .where(
                and_(
                    Observation.subject["reference"].astext == patient_ref,
                    Observation.tenant_id == ctx.tenant_id,
                )
            )
            .order_by(desc(Observation.effective_datetime))
            .limit(limit)
        )
        observations = result.scalars().all()

        # Lightweight mapping: avoid repeating heavy biomarker_info
        summary = []
        for obs in observations:
            summary.append(
                {
                    "id": str(obs.id),
                    "biomarker_id": str(obs.biomarker_id) if obs.biomarker_id else None,
                    "date": obs.effective_datetime.isoformat()
                    if obs.effective_datetime
                    else None,
                    "name": obs.code.get("text"),
                    "value": obs.value_quantity.get("value")
                    if obs.value_quantity
                    else obs.value_string,
                    "unit": obs.value_quantity.get("unit")
                    if obs.value_quantity
                    else None,
                    "interpretation": obs.interpretation,
                    "biomarker_slug": obs.biomarker.slug if obs.biomarker else None,
                }
            )

        return json.dumps(summary)

    @tool
    async def get_biomarker_history(biomarker_id_or_slug: str, limit: int = 10) -> str:
        """Fetch the historical trend for a specific biomarker using its ID (or slug).
        Do NOT use this for high-frequency telemetry (like heart rate or steps). Use it only for exact, discrete clinical lab results."""
        patient_ref = f"Patient/{ctx.patient_id}"

        # Check if it's a UUID
        try:
            bio_uuid = UUID(biomarker_id_or_slug)
            biomarker_filter = Observation.biomarker_id == bio_uuid
        except ValueError:
            biomarker_filter = Observation.biomarker.has(slug=biomarker_id_or_slug)

        result = await ctx.db.execute(
            select(Observation)
            .where(
                and_(
                    Observation.subject["reference"].astext == patient_ref,
                    Observation.tenant_id == ctx.tenant_id,
                    biomarker_filter,
                )
            )
            .order_by(desc(Observation.effective_datetime))
            .limit(limit)
        )
        observations = result.scalars().all()

        history = []
        for obs in observations:
            history.append(
                {
                    "id": str(obs.id),
                    "biomarker_id": str(obs.biomarker_id) if obs.biomarker_id else None,
                    "date": obs.effective_datetime.isoformat()
                    if obs.effective_datetime
                    else None,
                    "name": obs.code.get("text"),
                    "value": obs.value_quantity.get("value")
                    if obs.value_quantity
                    else obs.value_string,
                    "unit": obs.value_quantity.get("unit")
                    if obs.value_quantity
                    else None,
                    "interpretation": obs.interpretation,
                }
            )
        return json.dumps(history)

    @tool
    async def search_available_biomarkers(search_term: Optional[str] = None) -> str:
        """Search the clinical catalog to find the exact ID and type (telemetry vs clinical) of a biomarker.
        Use this tool BEFORE querying data if you are unsure of the exact ID or whether it is high-frequency telemetry.
        Typo-tolerant (trigram similarity) over name/slug/code; tenant-scoped + globals.
        If search_term is omitted, returns a list of common biomarkers."""
        from app.services.catalog_search_service import search_biomarkers

        biomarkers = await search_biomarkers(
            ctx.db, ctx.tenant_id, search_term, limit=20
        )

        summary = []
        for b in biomarkers:
            summary.append(
                {
                    "id": str(b.id),
                    "name": b.name,
                    "slug": b.slug,
                    "category": b.category,
                    "is_telemetry": b.is_telemetry,
                    "preferred_unit": b.preferred_unit.symbol
                    if b.preferred_unit
                    else None,
                }
            )
        return json.dumps(summary)

    @tool
    async def get_aggregated_biomarker_trends(
        biomarker_id_or_slug: str,
        start_date_iso: Optional[str] = None,
        end_date_iso: Optional[str] = None,
        period: str = "last-30-days",
        aggregation: Optional[str] = None,
        limit: int = 100,
    ) -> str:
        """Fetch historical, aggregated timeseries data for a biomarker (especially telemetry like heart rate or steps).
        Specify a 'period' (e.g., 'last-7-days', 'last-6-months', 'all-time') OR explicit 'start_date_iso' and 'end_date_iso'.
        Optionally specify 'aggregation' bucket (e.g. '1 hour', '1 day').
        Returns averaged OHLC data. Do NOT use this for exact single point-in-time lab results; use get_biomarker_history for those.
        Returns up to the `limit` most recent aggregated records within the range to protect context size."""
        from datetime import datetime

        from app.services.analytics_service import get_biomarker_trends

        start_date = None
        end_date = None
        if start_date_iso:
            try:
                start_date = datetime.fromisoformat(
                    start_date_iso.replace("Z", "+00:00")
                )
            except ValueError:
                return "Invalid start_date_iso format. Use ISO 8601."
        if end_date_iso:
            try:
                end_date = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
            except ValueError:
                return "Invalid end_date_iso format. Use ISO 8601."

        result = await get_biomarker_trends(
            tenant_id=str(ctx.tenant_id),
            biomarker_codes=biomarker_id_or_slug,
            period=period,
            aggregation=aggregation,
            patient_id=str(ctx.patient_id),
            start_date=start_date,
            end_date=end_date,
            db=ctx.db,
        )

        trends = result.get("biomarkers", {})

        target_data = []
        # First try exact match
        for key, data in trends.items():
            if biomarker_id_or_slug.lower() == key.lower():
                target_data = data
                break

        # Fallback to substring match if exact match fails
        if not target_data:
            for key, data in trends.items():
                if (
                    biomarker_id_or_slug.lower() in key.lower()
                    or key.lower() in biomarker_id_or_slug.lower()
                ):
                    target_data = data
                    break

        if not target_data:
            # If exact match fails, return the first one if there is only one
            if len(trends) == 1:
                target_data = list(trends.values())[0]
            else:
                return json.dumps([])

        # Apply record limit, keeping the most recent records
        target_data = target_data[-limit:]

        # Strip heavy UI metadata to save tokens
        lightweight_data = []
        for item in target_data:
            # Ensure we only keep what's essential
            clean_item = {
                "date": item.get("date"),
                "value": item.get("value"),
                "unit": item.get("unit"),
                "status": item.get("status"),
            }
            if item.get("min_value") is not None:
                clean_item["min_value"] = item.get("min_value")
            if item.get("max_value") is not None:
                clean_item["max_value"] = item.get("max_value")
            lightweight_data.append(clean_item)

        return json.dumps(lightweight_data)

    @tool
    async def get_biomarker_details(biomarker_id_or_slug: str) -> str:
        """Fetch full clinical definition, reference ranges, and informational text for a specific biomarker."""
        try:
            # Try by UUID first
            bio_uuid = UUID(biomarker_id_or_slug)
            query = select(BiomarkerDefinition).where(
                BiomarkerDefinition.id == bio_uuid
            )
        except ValueError:
            # Try by slug
            query = select(BiomarkerDefinition).where(
                BiomarkerDefinition.slug == biomarker_id_or_slug
            )

        result = await ctx.db.execute(query)
        bio = result.scalars().first()
        if not bio:
            return "Biomarker definition not found."

        return json.dumps(
            {
                "id": str(bio.id),
                "name": bio.name,
                "slug": bio.slug,
                "category": bio.category,
                "description": bio.description,
                "info": bio.info,
                "reference_range": {
                    "min": bio.reference_range_min,
                    "max": bio.reference_range_max,
                },
            }
        )

    return [
        get_recent_biomarkers,
        get_biomarker_history,
        search_available_biomarkers,
        get_aggregated_biomarker_trends,
        get_biomarker_details,
    ]
