"""Patient-scoped tools for the agentic chat.

Extracted from ``ChatbotTools`` (Phase 3).
"""

import json
from typing import Any, List

from langchain_core.tools import tool
from sqlalchemy import and_, select

from app.ai.tools.registry import ToolContext, register_chat_tool
from app.models.fhir.patient import Patient
from app.models.notification_rule import NotificationRule


@register_chat_tool("patient")
def build(ctx: ToolContext) -> List[Any]:
    @tool
    async def get_patient_summary() -> str:
        """Fetch a high-level summary of the patient's profile."""
        result = await ctx.db.execute(
            select(Patient).where(
                and_(
                    Patient.id == ctx.patient_id,
                    Patient.tenant_id == ctx.tenant_id,
                )
            )
        )
        patient = result.scalars().first()
        if not patient:
            return "Patient not found."

        data = patient.to_dict()
        # Remove heavy UI config fields from AI context
        data.pop("dashboard_layout", None)
        return json.dumps(data)

    @tool
    async def get_patient_alerts() -> str:
        """Fetch active clinical alerts and monitoring thresholds for the patient."""
        result = await ctx.db.execute(
            select(NotificationRule).where(
                and_(
                    NotificationRule.patient_id == ctx.patient_id,
                    NotificationRule.tenant_id == ctx.tenant_id,
                    NotificationRule.enabled.is_(True),
                )
            )
        )
        alerts = result.scalars().all()
        return json.dumps([a.to_dict() for a in alerts])

    return [get_patient_summary, get_patient_alerts]
