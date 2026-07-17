"""Examination instance search.

Tenant- (+ optional patient-) scoped ILIKE over ``notes`` / ``patient_notes``
/ ``impressions``. Self-registers with the instance-search registry.
"""
from uuid import UUID
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.instances._helpers import ilike_pattern, iso
from app.instances.registry import register_instance_search
from app.models.examination_model import ExaminationModel


async def search(
    db: AsyncSession,
    tenant_id: UUID,
    patient_id: Optional[UUID],
    q: str,
    limit: int,
) -> list[dict]:
    pat = ilike_pattern(q)
    stmt = select(ExaminationModel).where(
        ExaminationModel.tenant_id == tenant_id,
        or_(
            ExaminationModel.notes.ilike(pat),
            ExaminationModel.patient_notes.ilike(pat),
            ExaminationModel.impressions.ilike(pat),
        ),
    )
    if patient_id is not None:
        stmt = stmt.where(ExaminationModel.patient_id == patient_id)
    stmt = stmt.order_by(
        ExaminationModel.examination_date.desc().nullslast(),
        ExaminationModel.created_at.desc(),
    ).limit(limit)

    result = await db.execute(stmt)
    hits: list[dict] = []
    for e in result.scalars().all():
        snippet = (e.notes or e.patient_notes or "").strip()
        hits.append(
            {
                "type": "examination",
                "id": str(e.id),
                "label": (snippet[:60] or "Examination"),
                "subtitle": iso(e.examination_date),
                "date": iso(e.examination_date),
            }
        )
    return hits


register_instance_search("examination", search)
