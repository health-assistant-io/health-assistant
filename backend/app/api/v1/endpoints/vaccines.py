"""Vaccine catalog + patient-immunization endpoints (Phase 5).

Mirrors ``medications.py``: catalog CRUD (RBAC via ``CatalogAccessPolicy``) +
patient-instance CRUD (tenant + patient-access scoped). The FHIR R4
``Immunization`` facade resource (``/fhir/R4/Immunization``) is registered
separately in ``app/facade/registry.py``.
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.utils import (
    check_immunization_access,
    check_patient_access,
)
from app.catalogs.policy import DEFAULT_CATALOG_POLICY
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.schemas.vaccine import (
    PatientImmunizationCreate,
    PatientImmunizationResponse,
    PatientImmunizationUpdate,
    VaccineCatalogCreate,
    VaccineCatalogResponse,
    VaccineCatalogUpdate,
)
from app.services import vaccine_service

router = APIRouter(prefix="/vaccines", tags=["vaccines"])


def _enforce_catalog_create(current_user: TokenData) -> None:
    """Catalog creates are role-derived-scope: SYSTEM_ADMIN→system,
    ADMIN/MANAGER→tenant, USER→user. Any authenticated role may create."""
    DEFAULT_CATALOG_POLICY.create_scope(current_user.role)


# ---------------------------------------------------------------------------
# Catalog CRUD
# ---------------------------------------------------------------------------


@router.get("/catalog", response_model=List[VaccineCatalogResponse])
async def list_vaccine_catalog(
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    return await vaccine_service.get_vaccine_catalog(db, current_user.tenant_id, search)


@router.get("/catalog/{catalog_id}", response_model=VaccineCatalogResponse)
async def get_vaccine_catalog_entry(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    result = await vaccine_service.get_catalog_vaccine(
        db, catalog_id, current_user.tenant_id
    )
    if not result:
        raise HTTPException(status_code=404, detail="Vaccine not found in catalog")
    return result


@router.post("/catalog", response_model=VaccineCatalogResponse)
async def create_vaccine_catalog_entry(
    data: VaccineCatalogCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create a vaccine catalog entry (scope derived from role)."""
    _enforce_catalog_create(current_user)
    return await vaccine_service.create_catalog_vaccine(db, current_user, data)


@router.put("/catalog/{catalog_id}", response_model=VaccineCatalogResponse)
async def update_vaccine_catalog_entry(
    catalog_id: UUID,
    data: VaccineCatalogUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Update a vaccine catalog entry (scope + ownership enforced in service)."""
    result = await vaccine_service.update_catalog_vaccine(
        db, catalog_id, current_user, data
    )
    if not result:
        raise HTTPException(status_code=404, detail="Vaccine not found in catalog")
    return result


@router.delete("/catalog/{catalog_id}")
async def delete_vaccine_catalog_entry(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Delete a vaccine catalog entry (scope + ownership enforced)."""
    success = await vaccine_service.delete_catalog_vaccine(
        db, catalog_id, current_user
    )
    if not success:
        raise HTTPException(status_code=404, detail="Vaccine not found in catalog")
    return {"message": "Vaccine catalog entry deleted"}


# ---------------------------------------------------------------------------
# Patient immunization instances
# ---------------------------------------------------------------------------


@router.get("/patient/{patient_id}", response_model=List[PatientImmunizationResponse])
async def get_patient_immunizations(
    patient_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_patient_access(patient_id, current_user, db)
    return await vaccine_service.get_patient_immunizations(
        db, patient_id, current_user.tenant_id
    )


@router.post("/patient/{patient_id}", response_model=PatientImmunizationResponse)
async def add_patient_immunization(
    patient_id: UUID,
    data: PatientImmunizationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_patient_access(patient_id, current_user, db)
    return await vaccine_service.add_patient_immunization(
        db, patient_id, current_user.tenant_id, data
    )


@router.get("/{immunization_id}", response_model=PatientImmunizationResponse)
async def get_patient_immunization(
    immunization_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    return await check_immunization_access(immunization_id, current_user, db)


@router.put("/{immunization_id}", response_model=PatientImmunizationResponse)
async def update_patient_immunization(
    immunization_id: UUID,
    data: PatientImmunizationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_immunization_access(immunization_id, current_user, db)
    result = await vaccine_service.update_patient_immunization(
        db, immunization_id, current_user.tenant_id, data
    )
    if not result:
        raise HTTPException(status_code=404, detail="Immunization record not found")
    return result


@router.delete("/{immunization_id}")
async def delete_patient_immunization(
    immunization_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_immunization_access(immunization_id, current_user, db)
    success = await vaccine_service.delete_patient_immunization(
        db, immunization_id, current_user.tenant_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Immunization record not found")
    return {"message": "Immunization record deleted"}
