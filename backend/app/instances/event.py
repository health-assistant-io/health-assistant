"""Clinical event instance search.

Tenant- (+ optional patient-) scoped ILIKE over ``title`` / ``description``.
Excludes soft-deleted rows (``deleted_at IS NULL`` — matches the access
helper). Self-registers.
"""
from uuid import UUID
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.instances._helpers import ilike_pattern, iso
from app.instances.registry import register_instance_search
from app.models.clinical_event import ClinicalEvent


async def search(
    db: AsyncSession,
    tenant_id: UUID,
    patient_id: Optional[UUID],
    q: str,
    limit: int,
) -> list[dict]:
    pat = ilike_pattern(q)
    stmt = select(ClinicalEvent).where(
        ClinicalEvent.tenant_id == tenant_id,
        ClinicalEvent.deleted_at.is_(None),
        or_(
            ClinicalEvent.title.ilike(pat),
            ClinicalEvent.description.ilike(pat),
        ),
    )
    if patient_id is not None:
        stmt = stmt.where(ClinicalEvent.patient_id == patient_id)
    stmt = stmt.order_by(ClinicalEvent.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    hits: list[dict] = []
    for ev in result.scalars().all():
        desc = (ev.description or "").strip()
        hits.append(
            {
                "type": "event",
                "id": str(ev.id),
                "label": ev.title or "Clinical event",
                "subtitle": desc[:60] or None,
                "date": iso(getattr(ev, "start_date", None)),
            }
        )
    return hits


register_instance_search("event", search)
