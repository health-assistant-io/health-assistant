from typing import List, Optional, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from app.models.fhir.organization import OrganizationModel
from app.models.doctor_model import DoctorModel
from app.schemas.organization import OrganizationCreate, OrganizationUpdate


async def list_organizations(
    tenant_id: UUID, db: AsyncSession
) -> List[OrganizationModel]:
    result = await db.execute(
        select(OrganizationModel)
        .where(OrganizationModel.tenant_id == tenant_id)
        .order_by(OrganizationModel.name)
    )
    return list(result.scalars().all())


async def get_organization(
    organization_id: UUID,
    tenant_id: UUID,
    db: AsyncSession,
    include_details: bool = False,
) -> Optional[OrganizationModel]:
    query = select(OrganizationModel).where(
        OrganizationModel.id == organization_id,
        OrganizationModel.tenant_id == tenant_id,
    )

    if include_details:
        query = query.options(
            selectinload(OrganizationModel.doctors),
            selectinload(OrganizationModel.departments),
        )

    result = await db.execute(query)
    return result.scalar_one_or_none()


async def create_organization(
    tenant_id: UUID, user_id: UUID, obj_in: OrganizationCreate, db: AsyncSession
) -> OrganizationModel:
    data = obj_in.model_dump()
    doctor_ids = data.pop("doctor_ids", None)

    organization = OrganizationModel(tenant_id=tenant_id, created_by=user_id, **data)

    if doctor_ids:
        doc_result = await db.execute(
            select(DoctorModel).where(
                DoctorModel.id.in_(doctor_ids), DoctorModel.tenant_id == tenant_id
            )
        )
        doctors = doc_result.scalars().all()
        organization.doctors = list(doctors)

    db.add(organization)
    await db.commit()
    await db.refresh(organization)
    return organization


async def update_organization(
    organization_id: UUID, tenant_id: UUID, obj_in: OrganizationUpdate, db: AsyncSession
) -> Optional[OrganizationModel]:
    organization = await get_organization(
        organization_id, tenant_id, db, include_details=True
    )
    if not organization:
        return None

    update_data = obj_in.model_dump(exclude_unset=True)

    # Handle doctor associations separately
    if "doctor_ids" in update_data:
        doctor_ids = update_data.pop("doctor_ids")
        if doctor_ids is not None:
            # Fetch doctors
            doc_result = await db.execute(
                select(DoctorModel).where(
                    DoctorModel.id.in_(doctor_ids), DoctorModel.tenant_id == tenant_id
                )
            )
            doctors = doc_result.scalars().all()
            organization.doctors = list(doctors)

    for key, value in update_data.items():
        if hasattr(organization, key):
            setattr(organization, key, value)

    await db.commit()
    await db.refresh(organization)
    return organization


async def delete_organization(
    organization_id: UUID, tenant_id: UUID, db: AsyncSession
) -> bool:
    organization = await get_organization(organization_id, tenant_id, db)
    if not organization:
        return False

    await db.delete(organization)
    await db.commit()
    return True
