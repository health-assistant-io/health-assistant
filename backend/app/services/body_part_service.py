from typing import List, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.models.body_part import BodyPartModel
from slugify import slugify


async def list_body_parts(tenant_id: UUID, db: AsyncSession) -> List[BodyPartModel]:
    """
    List both global (tenant_id is None) and tenant-specific body parts.
    """
    result = await db.execute(
        select(BodyPartModel)
        .where(
            or_(BodyPartModel.tenant_id == tenant_id, BodyPartModel.tenant_id == None)
        )
        .order_by(BodyPartModel.name)
    )
    return list(result.scalars().all())


async def get_body_part(
    body_part_id: UUID, tenant_id: UUID, db: AsyncSession
) -> Optional[BodyPartModel]:
    result = await db.execute(
        select(BodyPartModel).where(
            BodyPartModel.id == body_part_id,
            or_(BodyPartModel.tenant_id == tenant_id, BodyPartModel.tenant_id == None),
        )
    )
    return result.scalar_one_or_none()


async def create_body_part(
    tenant_id: UUID,
    name: str,
    snomed_code: Optional[str] = None,
    description: Optional[str] = None,
    is_custom: bool = True,
    db: AsyncSession = None,
) -> BodyPartModel:
    slug = slugify(name)
    body_part = BodyPartModel(
        tenant_id=tenant_id,
        name=name,
        slug=slug,
        snomed_code=snomed_code,
        description=description,
        is_custom=is_custom,
    )
    db.add(body_part)
    await db.commit()
    await db.refresh(body_part)
    return body_part
