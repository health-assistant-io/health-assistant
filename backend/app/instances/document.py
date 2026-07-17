"""Document instance search.

Tenant- (+ optional patient-) scoped ILIKE over ``filename``. Excludes
soft-deleted rows (``deleted_at IS NULL`` — documents carry ``SoftDeleteMixin``
per the FHIR facade tombstone behavior). Self-registers.

Note: the existing ``GET /documents`` endpoint lacks a ``patient_id`` filter
(audit gap — cross-patient read within a tenant). This search function ALWAYS
accepts an optional ``patient_id`` and applies it when provided, so the picker
never leaks across patients even when an admin browses tenant-wide.
"""
from uuid import UUID
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.instances._helpers import ilike_pattern
from app.instances.registry import register_instance_search
from app.models.document_model import DocumentModel


async def search(
    db: AsyncSession,
    tenant_id: UUID,
    patient_id: Optional[UUID],
    q: str,
    limit: int,
) -> list[dict]:
    pat = ilike_pattern(q)
    stmt = select(DocumentModel).where(
        DocumentModel.tenant_id == tenant_id,
        DocumentModel.deleted_at.is_(None),
        DocumentModel.filename.ilike(pat),
    )
    if patient_id is not None:
        stmt = stmt.where(DocumentModel.patient_id == patient_id)
    stmt = stmt.order_by(DocumentModel.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    hits: list[dict] = []
    for d in result.scalars().all():
        hits.append(
            {
                "type": "document",
                "id": str(d.id),
                "label": d.filename,
                "subtitle": getattr(d, "status", None),
                "date": None,
            }
        )
    return hits


register_instance_search("document", search)
