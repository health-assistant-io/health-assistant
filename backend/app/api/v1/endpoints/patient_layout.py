from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.patient_layout import (
    PatientLayoutCreate,
    PatientLayoutUpdate,
    PatientLayoutResponse,
)
from app.services.access import check_patient_access
from app.services.patient_layout_service import (
    get_patient_layouts,
    get_active_layout,
    create_patient_layout,
    update_patient_layout,
    delete_patient_layout,
)

from app.schemas.user import TokenData

router = APIRouter(prefix="/patients/{patient_id}/layouts", tags=["patient-layouts"])


@router.get("", response_model=List[PatientLayoutResponse])
async def list_layouts(
    patient_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all layouts for a specific patient for the current user"""
    await check_patient_access(patient_id, current_user, db)
    return await get_patient_layouts(current_user.user_id, patient_id)


@router.get("/active", response_model=PatientLayoutResponse)
async def get_current_active_layout(
    patient_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the active layout for a specific patient for the current user"""
    await check_patient_access(patient_id, current_user, db)
    layout = await get_active_layout(current_user.user_id, patient_id)
    if not layout:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No layout found for this patient",
        )
    return layout


@router.post(
    "", response_model=PatientLayoutResponse, status_code=status.HTTP_201_CREATED
)
async def create_layout(
    patient_id: UUID,
    layout_data: PatientLayoutCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new layout for a specific patient"""
    await check_patient_access(patient_id, current_user, db)

    return await create_patient_layout(
        user_id=current_user.user_id,
        patient_id=patient_id,
        tenant_id=current_user.tenant_id,
        name=layout_data.name,
        layout_config=layout_data.layout_config,
        cards_config=layout_data.cards_config,
        is_default=layout_data.is_default,
    )


@router.put("/{layout_id}", response_model=PatientLayoutResponse)
async def update_layout(
    patient_id: UUID,
    layout_id: UUID,
    layout_data: PatientLayoutUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing layout"""
    await check_patient_access(patient_id, current_user, db)
    updated_layout = await update_patient_layout(
        layout_id=layout_id,
        user_id=current_user.user_id,
        **layout_data.dict(exclude_unset=True),
    )

    if not updated_layout:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Layout not found or access denied",
        )

    return updated_layout


@router.delete("/{layout_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_layout(
    patient_id: UUID,
    layout_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a layout"""
    await check_patient_access(patient_id, current_user, db)
    success = await delete_patient_layout(layout_id, current_user.user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Layout not found or access denied",
        )

    return None
