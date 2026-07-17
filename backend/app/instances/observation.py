"""Observation instance search.

Tenant- (+ optional patient-) scoped ILIKE over the FHIR ``code.text`` JSONB
field (LOINC display). Self-registers.
"""
from uuid import UUID
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.instances._helpers import code_text, ilike_pattern, iso
from app.instances.registry import register_instance_search
from app.models.fhir.patient import Observation


async def search(
    db: AsyncSession,
    tenant_id: UUID,
    patient_id: Optional[UUID],
    q: str,
    limit: int,
) -> list[dict]:
    pat = ilike_pattern(q)
    stmt = select(Observation).where(
        Observation.tenant_id == tenant_id,
        Observation.code["text"].astext.ilike(pat),
    )
    if patient_id is not None:
        stmt = stmt.where(Observation.patient_id == patient_id)
    stmt = stmt.order_by(
        Observation.effective_datetime.desc().nullslast()
    ).limit(limit)

    result = await db.execute(stmt)
    hits: list[dict] = []
    for o in result.scalars().all():
        vq = o.value_quantity if isinstance(o.value_quantity, dict) else None
        value_str = ""
        if vq and "value" in vq:
            value_str = f"{vq['value']}"
            unit = vq.get("unit")
            if unit:
                value_str = f"{value_str} {unit}"
        hits.append(
            {
                "type": "observation",
                "id": str(o.id),
                "label": code_text(o.code) or "Observation",
                "subtitle": value_str or None,
                "date": iso(o.effective_datetime),
            }
        )
    return hits


register_instance_search("observation", search)
