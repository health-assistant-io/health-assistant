from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.organization import (
    Organization,
    OrganizationCreate,
    OrganizationUpdate,
    OrganizationWithDetails,
)
from app.services.organization_service import (
    list_organizations,
    get_organization,
    create_organization,
    update_organization,
    delete_organization,
)
from app.schemas.user import TokenData

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("", response_model=List[Organization])
async def list_organizations_endpoint(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    organizations = await list_organizations(current_user.tenant_id, db)
    return [o.to_dict() for o in organizations]


@router.get("/{organization_id}", response_model=OrganizationWithDetails)
async def get_organization_endpoint(
    organization_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    organization = await get_organization(
        organization_id, current_user.tenant_id, db, include_details=True
    )
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Enrich response with doctors and departments
    res = organization.to_dict()
    res["doctors"] = [d.to_dict() for d in organization.doctors]
    res["departments"] = [dept.to_dict() for dept in organization.departments]
    return res


@router.post("", response_model=Organization, status_code=status.HTTP_201_CREATED)
async def create_organization_endpoint(
    obj_in: OrganizationCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await create_organization(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        obj_in=obj_in,
        db=db,
    )


@router.put("/{organization_id}", response_model=Organization)
async def update_organization_endpoint(
    organization_id: UUID,
    obj_in: OrganizationUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    updated = await update_organization(
        organization_id=organization_id,
        tenant_id=current_user.tenant_id,
        obj_in=obj_in,
        db=db,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Organization not found")
    return updated


@router.delete("/{organization_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization_endpoint(
    organization_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    success = await delete_organization(organization_id, current_user.tenant_id, db)
    if not success:
        raise HTTPException(status_code=404, detail="Organization not found")
    return None
