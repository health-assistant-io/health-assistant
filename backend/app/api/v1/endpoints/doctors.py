from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.doctor_model import DoctorModel
from app.models.enums import Role
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.doctor import DoctorCreate, DoctorUpdate, DoctorResponse
from app.services.doctor_service import (
    get_doctor,
    create_doctor,
    update_doctor,
    delete_doctor,
)

from app.schemas.user import TokenData

router = APIRouter(prefix="/doctors", tags=["doctors"])


@router.get("", response_model=List[DoctorResponse])
async def list_doctors_endpoint(
    tenant_id: Optional[UUID] = Query(None),
    user_id: Optional[UUID] = Query(None),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):

    # Enforce tenant isolation for non-system admins
    final_tenant_id = current_user.tenant_id
    if current_user.role == Role.SYSTEM_ADMIN.value and tenant_id:
        final_tenant_id = tenant_id
    elif current_user.role == Role.SYSTEM_ADMIN.value and not tenant_id and user_id:
        final_tenant_id = None

    query = select(DoctorModel)
    if final_tenant_id:
        query = query.where(DoctorModel.tenant_id == final_tenant_id)

    if user_id:
        query = query.where(DoctorModel.user_id == user_id)

    result = await db.execute(query)
    doctors = result.scalars().unique().all()
    return [d.to_dict() for d in doctors]


@router.get("/{doctor_id}", response_model=DoctorResponse)
async def get_doctor_endpoint(
    doctor_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    doctor = await get_doctor(doctor_id, current_user.tenant_id, db)
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor


@router.post("", response_model=DoctorResponse, status_code=status.HTTP_201_CREATED)
async def create_doctor_endpoint(
    doctor_data: DoctorCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_doctor(
        tenant_id=current_user.tenant_id,
        creator_id=current_user.user_id,
        db=db,
        **doctor_data.model_dump(),
    )


@router.put("/{doctor_id}", response_model=DoctorResponse)
async def update_doctor_endpoint(
    doctor_id: UUID,
    doctor_data: DoctorUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    updated = await update_doctor(
        doctor_id=doctor_id,
        tenant_id=current_user.tenant_id,
        db=db,
        **doctor_data.model_dump(exclude_unset=True),
    )

    if not updated:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return updated


@router.delete("/{doctor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_doctor_endpoint(
    doctor_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    success = await delete_doctor(doctor_id, current_user.tenant_id, db)
    if not success:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return None
