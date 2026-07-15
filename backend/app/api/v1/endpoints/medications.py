from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.services.access import check_patient_access, check_medication_access
from app.schemas.medication import (
    MedicationCatalogCreate,
    MedicationCatalogUpdate,
    MedicationCatalogResponse,
    MedicationRecordCreate,
    MedicationRecordUpdate,
    MedicationRecordResponse,
)
from app.services import medication_service
from app.catalogs.policy import DEFAULT_CATALOG_POLICY

router = APIRouter(prefix="/medications", tags=["medications"])


def _enforce_catalog_create(current_user: TokenData) -> None:
    """Catalog creates are role-derived-scope: SYSTEM_ADMIN→system,
    ADMIN/MANAGER→tenant, USER→user. Any authenticated role may create (the
    scope varies). Raises ``CatalogPermissionDenied`` (→ HTTP 403) never —
    kept as a hook for future per-type lock-down (Phase F)."""
    DEFAULT_CATALOG_POLICY.create_scope(current_user.role)


@router.get("/catalog", response_model=List[MedicationCatalogResponse])
async def get_medication_catalog(
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    return await medication_service.get_medication_catalog(
        db, current_user.tenant_id, search
    )


@router.get("/catalog/{catalog_id}", response_model=MedicationCatalogResponse)
async def get_catalog_medication(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    result = await medication_service.get_catalog_medication(
        db, catalog_id, current_user.tenant_id
    )
    if not result:
        raise HTTPException(status_code=404, detail="Medication not found in catalog")
    return result


@router.post("/catalog", response_model=MedicationCatalogResponse)
async def create_catalog_medication(
    data: MedicationCatalogCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create a medication catalog entry (scope derived from role)."""
    _enforce_catalog_create(current_user)
    return await medication_service.create_catalog_medication(
        db, current_user, data
    )


@router.put("/catalog/{catalog_id}", response_model=MedicationCatalogResponse)
async def update_catalog_medication(
    catalog_id: UUID,
    data: MedicationCatalogUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Update a medication catalog entry. Scope + ownership enforced in the
    service (raises ``CatalogPermissionDenied`` → 403)."""
    result = await medication_service.update_catalog_medication(
        db, catalog_id, current_user, data
    )
    if not result:
        raise HTTPException(status_code=404, detail="Medication not found in catalog")
    return result


@router.delete("/catalog/{catalog_id}")
async def delete_catalog_medication(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Delete a medication catalog entry (scope + ownership enforced)."""
    success = await medication_service.delete_catalog_medication(
        db, catalog_id, current_user
    )
    if not success:
        raise HTTPException(status_code=404, detail="Medication not found in catalog")
    return {"message": "Medication catalog entry deleted"}


@router.get("/patient/{patient_id}", response_model=List[MedicationRecordResponse])
async def get_patient_medications(
    patient_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_patient_access(patient_id, current_user, db)
    return await medication_service.get_patient_medications(
        db, patient_id, current_user.tenant_id
    )


@router.post("/patient/{patient_id}", response_model=MedicationRecordResponse)
async def add_patient_medication(
    patient_id: UUID,
    data: MedicationRecordCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_patient_access(patient_id, current_user, db)
    return await medication_service.add_patient_medication(
        db, patient_id, current_user.tenant_id, data
    )


@router.put("/{medication_id}", response_model=MedicationRecordResponse)
async def update_patient_medication(
    medication_id: UUID,
    data: MedicationRecordUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_medication_access(medication_id, current_user, db)
    result = await medication_service.update_patient_medication(
        db, medication_id, current_user.tenant_id, data
    )
    if not result:
        raise HTTPException(status_code=404, detail="Medication record not found")
    return result


@router.get("/{medication_id}", response_model=MedicationRecordResponse)
async def get_patient_medication(
    medication_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    return await check_medication_access(medication_id, current_user, db)


@router.delete("/{medication_id}")
async def delete_patient_medication(
    medication_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_medication_access(medication_id, current_user, db)
    success = await medication_service.delete_patient_medication(
        db, medication_id, current_user.tenant_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Medication record not found")
    return {"message": "Medication record deleted"}


@router.get("/catalog/{catalog_id}/usage")
async def get_medication_usage(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    return await medication_service.get_medication_usage(
        db, catalog_id, current_user.tenant_id
    )


@router.post(
    "/catalog/{catalog_id}/reprocess", response_model=MedicationCatalogResponse
)
async def reprocess_medication(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    result = await medication_service.reprocess_medication(
        db, catalog_id, current_user.tenant_id
    )
    if not result:
        raise HTTPException(status_code=404, detail="Medication not found in catalog")
    return result
