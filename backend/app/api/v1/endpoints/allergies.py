from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.models.enums import Role
from app.models.fhir.patient import Patient
from app.api.v1.endpoints.utils import check_patient_access, check_allergy_access
from app.schemas.allergy import (
    AllergyCatalogCreate,
    AllergyCatalogResponse,
    AllergyCatalogUpdate,
    AllergyIntoleranceCreate,
    AllergyIntoleranceUpdate,
    AllergyIntoleranceResponse,
)
from app.services import allergy_service
from app.catalogs.policy import DEFAULT_CATALOG_POLICY

router = APIRouter(prefix="/allergies", tags=["allergies"])


@router.get("/catalog", response_model=List[AllergyCatalogResponse])
async def list_catalog(
    search: Optional[str] = Query(None),
    current_user: TokenData = Depends(get_current_user),
):
    """Search the global and local allergy catalog"""
    return await allergy_service.list_allergy_catalog(search, current_user.tenant_id)


@router.get("/catalog/{catalog_id}", response_model=AllergyCatalogResponse)
async def get_catalog_entry(
    catalog_id: UUID,
    current_user: TokenData = Depends(get_current_user),
):
    """Fetch one allergy catalog entry by id (tenant-scoped)."""
    entry = await allergy_service.get_catalog_allergy(
        catalog_id, current_user.tenant_id
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Allergy catalog entry not found")
    return entry


@router.get("/active", response_model=List[AllergyIntoleranceResponse])
async def get_all_active_allergies(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all active allergy records for the entire tenant (or just for the user's patients if standard role)"""
    if current_user.role == Role.USER.value:
        from app.models.fhir.allergy import AllergyIntolerance, AllergyClinicalStatus

        query = (
            select(AllergyIntolerance)
            .join(Patient)
            .where(
                Patient.user_id == current_user.user_id,
                AllergyIntolerance.tenant_id == current_user.tenant_id,
                AllergyIntolerance.clinical_status == AllergyClinicalStatus.ACTIVE,
            )
        )
        result = await db.execute(query)
        return result.scalars().all()

    return await allergy_service.get_active_allergies_by_tenant(current_user.tenant_id)


@router.post("/catalog", response_model=AllergyCatalogResponse)
async def create_catalog_entry(
    data: AllergyCatalogCreate, current_user: TokenData = Depends(get_current_user)
):
    """Add a new allergen to the catalog (scope derived from role)."""
    DEFAULT_CATALOG_POLICY.create_scope(current_user.role)
    return await allergy_service.add_to_catalog(
        name=data.name,
        category=data.category.value,
        actor=current_user,
        description=data.description,
    )


@router.put("/catalog/{catalog_id}", response_model=AllergyCatalogResponse)
async def update_catalog_entry(
    catalog_id: UUID,
    data: AllergyCatalogUpdate,
    current_user: TokenData = Depends(get_current_user),
):
    """Update an allergy catalog entry (scope + ownership enforced in service)."""
    entry = await allergy_service.update_catalog_allergy(
        catalog_id,
        current_user,
        data.model_dump(exclude_unset=True),
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Allergy catalog entry not found")
    return entry


@router.delete("/catalog/{catalog_id}")
async def delete_catalog_entry(
    catalog_id: UUID,
    current_user: TokenData = Depends(get_current_user),
):
    """Delete an allergy catalog entry (scope + ownership enforced)."""
    success = await allergy_service.delete_catalog_allergy(catalog_id, current_user)
    if not success:
        raise HTTPException(status_code=404, detail="Allergy catalog entry not found")
    return {"message": "Allergy catalog entry deleted"}


@router.get("/patient/{patient_id}", response_model=List[AllergyIntoleranceResponse])
async def get_patient_allergies(
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all allergy records for a specific patient"""
    await check_patient_access(patient_id, current_user, db)
    return await allergy_service.get_patient_allergies(
        UUID(patient_id), current_user.tenant_id
    )


@router.post("/patient/{patient_id}", response_model=AllergyIntoleranceResponse)
async def add_patient_allergy(
    patient_id: str,
    data: AllergyIntoleranceCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add an allergy record to a patient's profile"""
    await check_patient_access(patient_id, current_user, db)
    return await allergy_service.add_patient_allergy(
        UUID(patient_id), current_user.tenant_id, data.model_dump()
    )


@router.put("/{allergy_id}", response_model=AllergyIntoleranceResponse)
async def update_allergy(
    allergy_id: str,
    data: AllergyIntoleranceUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing allergy record"""
    await check_allergy_access(allergy_id, current_user, db)
    item = await allergy_service.update_patient_allergy(
        UUID(allergy_id), current_user.tenant_id, data.model_dump(exclude_unset=True)
    )
    if not item:
        raise HTTPException(status_code=404, detail="Allergy record not found")
    return item


@router.delete("/{allergy_id}")
async def delete_allergy(
    allergy_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete an allergy record"""
    await check_allergy_access(allergy_id, current_user, db)
    success = await allergy_service.delete_patient_allergy(
        UUID(allergy_id), current_user.tenant_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Allergy record not found")
    return {"message": "Allergy record deleted"}
