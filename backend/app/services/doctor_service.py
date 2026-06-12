from typing import List, Optional, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.models.doctor_model import DoctorModel


async def list_doctors(
    tenant_id: UUID | None, 
    db: AsyncSession,
    user_id: Optional[UUID] = None
) -> List[DoctorModel]:
    query = select(DoctorModel)
    if tenant_id:
        query = query.where(DoctorModel.tenant_id == tenant_id)
    
    if user_id:
        query = query.where(DoctorModel.user_id == user_id)
        
    result = await db.execute(query.order_by(DoctorModel.name))
    return list(result.scalars().all())


async def get_doctor(
    doctor_id: UUID, tenant_id: UUID, db: AsyncSession
) -> Optional[DoctorModel]:
    result = await db.execute(
        select(DoctorModel).where(
            DoctorModel.id == doctor_id, DoctorModel.tenant_id == tenant_id
        )
    )
    return result.scalar_one_or_none()


async def create_doctor(
    tenant_id: UUID,
    creator_id: UUID,
    name: str,
    specialty: Optional[str] = None,
    license_number: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    telecom: Optional[List[Any]] = None,
    address: Optional[Any] = None,
    office_number: Optional[str] = None,
    office_details: Optional[str] = None,
    user_id: Optional[UUID] = None,  # Linked identity
    db: AsyncSession = None,
) -> DoctorModel:
    doctor = DoctorModel(
        tenant_id=tenant_id,
        created_by=creator_id,
        user_id=user_id,
        name=name,
        specialty=specialty,
        license_number=license_number,
        email=email,
        phone=phone,
        telecom=telecom,
        address=address,
        office_number=office_number,
        office_details=office_details,
    )
    db.add(doctor)
    await db.commit()
    await db.refresh(doctor)
    return doctor


async def update_doctor(
    doctor_id: UUID, tenant_id: UUID, db: AsyncSession, **kwargs
) -> Optional[DoctorModel]:
    doctor = await get_doctor(doctor_id, tenant_id, db)
    if not doctor:
        return None

    for key, value in kwargs.items():
        if hasattr(doctor, key):
            setattr(doctor, key, value)

    await db.commit()
    await db.refresh(doctor)
    return doctor


async def delete_doctor(doctor_id: UUID, tenant_id: UUID, db: AsyncSession) -> bool:
    doctor = await get_doctor(doctor_id, tenant_id, db)
    if not doctor:
        return False

    await db.delete(doctor)
    await db.commit()
    return True
