from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.body_part import BodyPartCreate, BodyPartResponse, BodyPartUpdate
from app.services.body_part_service import (
    list_body_parts,
    get_body_part,
    create_body_part,
)
from app.core.security import get_current_user
from app.schemas.user import TokenData


router = APIRouter()


@router.get("", response_model=List[BodyPartResponse])
async def list_body_parts_endpoint(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List all body parts available for the current tenant.
    """
    return await list_body_parts(tenant_id=current_user.tenant_id, db=db)


@router.post("", response_model=BodyPartResponse, status_code=status.HTTP_201_CREATED)
async def create_body_part_endpoint(
    body_part_data: BodyPartCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new custom body part.
    """
    return await create_body_part(
        tenant_id=current_user.tenant_id,
        db=db,
        **body_part_data.model_dump(exclude={"is_custom", "slug"}),
        is_custom=True,
    )


@router.get("/{body_part_id}", response_model=BodyPartResponse)
async def get_body_part_endpoint(
    body_part_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a specific body part by ID.
    """
    body_part = await get_body_part(
        body_part_id=body_part_id, tenant_id=current_user.tenant_id, db=db
    )
    if not body_part:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Body part not found"
        )
    return body_part
