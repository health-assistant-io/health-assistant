"""Medication instance search.

Tenant- (+ optional patient-) scoped ILIKE over the FHIR ``code.text`` JSONB
field and the ``reason`` text column. Self-registers.
"""
from uuid import UUID
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.instances._helpers import code_text, ilike_pattern, iso
from app.instances.registry import register_instance_search
from app.models.fhir.medication import Medication


async def search(
    db: AsyncSession,
    tenant_id: UUID,
    patient_id: Optional[UUID],
    q: str,
    limit: int,
) -> list[dict]:
    pat = ilike_pattern(q)
    stmt = select(Medication).where(
        Medication.tenant_id == tenant_id,
        or_(
            Medication.code["text"].astext.ilike(pat),
            Medication.reason.ilike(pat),
        ),
    )
    if patient_id is not None:
        stmt = stmt.where(Medication.patient_id == patient_id)
    stmt = stmt.order_by(Medication.start_date.desc().nullslast()).limit(limit)

    result = await db.execute(stmt)
    hits: list[dict] = []
    for m in result.scalars().all():
        label = code_text(m.code) or "Medication"
        status = getattr(m, "status", None)
        status_val = status.value if hasattr(status, "value") else status
        subtitle = " · ".join(p for p in [status_val, iso(m.start_date)] if p)
        hits.append(
            {
                "type": "medication",
                "id": str(m.id),
                "label": label,
                "subtitle": subtitle or None,
                "date": iso(m.start_date),
            }
        )
    return hits


register_instance_search("medication", search)
