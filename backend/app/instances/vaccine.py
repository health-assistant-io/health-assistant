"""Vaccine (immunization) instance search.

Tenant- (+ optional patient-) scoped ILIKE over the FHIR ``vaccine_code.text``
JSONB field and the ``lot_number`` column. Excludes soft-deleted rows.
Self-registers.
"""
from uuid import UUID
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.instances._helpers import code_text, ilike_pattern, iso
from app.instances.registry import register_instance_search
from app.models.fhir.vaccine import PatientImmunization


async def search(
    db: AsyncSession,
    tenant_id: UUID,
    patient_id: Optional[UUID],
    q: str,
    limit: int,
) -> list[dict]:
    pat = ilike_pattern(q)
    stmt = select(PatientImmunization).where(
        PatientImmunization.tenant_id == tenant_id,
        PatientImmunization.deleted_at.is_(None),
        or_(
            PatientImmunization.vaccine_code["text"].astext.ilike(pat),
            PatientImmunization.lot_number.ilike(pat),
        ),
    )
    if patient_id is not None:
        stmt = stmt.where(PatientImmunization.patient_id == patient_id)
    stmt = stmt.order_by(
        PatientImmunization.administered_at.desc().nullslast()
    ).limit(limit)

    result = await db.execute(stmt)
    hits: list[dict] = []
    for v in result.scalars().all():
        lot = (v.lot_number or "").strip()
        hits.append(
            {
                "type": "vaccine",
                "id": str(v.id),
                "label": code_text(v.vaccine_code) or "Vaccination",
                "subtitle": (f"Lot {lot}") if lot else iso(v.administered_at),
                "date": iso(v.administered_at),
            }
        )
    return hits


register_instance_search("vaccine", search)
