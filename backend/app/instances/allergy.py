"""Allergy instance search.

Tenant- (+ optional patient-) scoped ILIKE over the FHIR ``code.text`` JSONB
field (the allergen substance). Self-registers.
"""
from uuid import UUID
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.instances._helpers import code_text, ilike_pattern
from app.instances.registry import register_instance_search
from app.models.fhir.allergy import AllergyIntolerance


async def search(
    db: AsyncSession,
    tenant_id: UUID,
    patient_id: Optional[UUID],
    q: str,
    limit: int,
) -> list[dict]:
    pat = ilike_pattern(q)
    stmt = select(AllergyIntolerance).where(
        AllergyIntolerance.tenant_id == tenant_id,
        AllergyIntolerance.code["text"].astext.ilike(pat),
    )
    if patient_id is not None:
        stmt = stmt.where(AllergyIntolerance.patient_id == patient_id)
    stmt = stmt.order_by(AllergyIntolerance.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    hits: list[dict] = []
    for a in result.scalars().all():
        status = getattr(a, "clinical_status", None)
        status_val = status.value if hasattr(status, "value") else status
        hits.append(
            {
                "type": "allergy",
                "id": str(a.id),
                "label": code_text(a.code) or "Allergy",
                "subtitle": status_val or None,
                "date": None,
            }
        )
    return hits


register_instance_search("allergy", search)
