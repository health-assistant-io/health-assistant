"""Clinical-event (health journey) tools for the agentic chat.

Extracted from ``ChatbotTools`` (Phase 3).
"""

import json
from typing import Any, List
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import and_, desc, select
from sqlalchemy.orm import selectinload

from app.ai.tools.registry import ToolContext, register_chat_tool
from app.models.biomarker_model import BiomarkerDefinition
from app.models.clinical_event import (
    ClinicalEvent,
    EventExaminationLink,
    EventObservationLink,
)
from app.models.fhir.patient import Observation


@register_chat_tool("clinical_events")
def build(ctx: ToolContext) -> List[Any]:
    @tool
    async def get_clinical_events(limit: int = 10) -> str:
        """Fetch a list of health journeys and clinical events for the patient (e.g., pregnancies, chronic pain cycles, surgical recoveries).
        Returns event titles, types, status, and dates."""
        result = await ctx.db.execute(
            select(ClinicalEvent)
            .options(selectinload(ClinicalEvent.type_entity))
            .where(
                and_(
                    ClinicalEvent.patient_id == ctx.patient_id,
                    ClinicalEvent.tenant_id == ctx.tenant_id,
                )
            )
            .order_by(desc(ClinicalEvent.onset_date))
            .limit(limit)
        )
        events = result.scalars().all()

        summary = []
        for event in events:
            summary.append(
                {
                    "id": str(event.id),
                    "title": event.title,
                    "type": event.type_entity.name if event.type_entity else "Unknown",
                    "status": event.status.value,
                    "onset_date": event.onset_date.isoformat()
                    if event.onset_date
                    else None,
                    "resolved_date": event.resolved_date.isoformat()
                    if event.resolved_date
                    else None,
                    "description": event.description[:200]
                    if event.description
                    else None,
                }
            )
        return json.dumps(summary)

    @tool
    async def get_clinical_event_details(event_id: str) -> str:
        """Fetch comprehensive details of a specific clinical event or health journey.
        Returns full description, occurrences/episodes, metadata, and linked examinations or biomarkers."""
        try:
            event_uuid = UUID(event_id)
        except ValueError:
            return "Invalid event ID format."

        result = await ctx.db.execute(
            select(ClinicalEvent)
            .options(
                selectinload(ClinicalEvent.type_entity),
                selectinload(ClinicalEvent.examination_links).selectinload(
                    EventExaminationLink.examination
                ),
                selectinload(ClinicalEvent.observation_links)
                .selectinload(EventObservationLink.observation)
                .selectinload(Observation.biomarker)
                .selectinload(BiomarkerDefinition.preferred_unit),
            )
            .where(
                and_(
                    ClinicalEvent.id == event_uuid,
                    ClinicalEvent.tenant_id == ctx.tenant_id,
                    ClinicalEvent.patient_id == ctx.patient_id,
                )
            )
        )
        event = result.scalars().first()
        if not event:
            return "Clinical event not found or access denied."

        return json.dumps(event.to_dict())

    return [get_clinical_events, get_clinical_event_details]
