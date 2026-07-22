"""Allergy catalog + patient-instance intolerance endpoints.

Mirrors :mod:`app.api.v1.endpoints.medications`: db-injected service calls,
``check_patient_access`` / ``check_allergy_access`` for tenancy + RBAC, and the
parity surface (single-instance GET, catalog usage, AI reprocess).
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogs.policy import DEFAULT_CATALOG_POLICY
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enums import Role
from app.models.fhir.patient import Patient
from app.schemas.allergy import (
    AllergyCatalogCreate,
    AllergyCatalogResponse,
    AllergyCatalogUpdate,
    AllergyIntoleranceCreate,
    AllergyIntoleranceResponse,
    AllergyIntoleranceUpdate,
)
from app.schemas.user import TokenData
from app.services import allergy_service
from app.services.access import (
    check_allergy_access,
    check_patient_access,
)

router = APIRouter(prefix="/allergies", tags=["allergies"])


def _enforce_catalog_create(current_user: TokenData) -> None:
    """Catalog creates are role-derived-scope (parity with medications). Any
    authenticated role may create; the scope varies by role.
    """
    DEFAULT_CATALOG_POLICY.create_scope(current_user.role)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get("/catalog", response_model=List[AllergyCatalogResponse])
async def list_catalog(
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Search the global + tenant allergy catalog (hybrid search)."""
    return await allergy_service.get_allergy_catalog(
        db, current_user.tenant_id, search
    )


@router.get("/catalog/{catalog_id}", response_model=AllergyCatalogResponse)
async def get_catalog_entry(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    entry = await allergy_service.get_catalog_allergy(
        db, catalog_id, current_user.tenant_id
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Allergy catalog entry not found")
    return entry


@router.post("/catalog", response_model=AllergyCatalogResponse)
async def create_catalog_entry(
    data: AllergyCatalogCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create an allergy catalog entry (scope derived from role)."""
    _enforce_catalog_create(current_user)
    return await allergy_service.create_catalog_allergy(db, current_user, data)


@router.put("/catalog/{catalog_id}", response_model=AllergyCatalogResponse)
async def update_catalog_entry(
    catalog_id: UUID,
    data: AllergyCatalogUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Update an allergy catalog entry (scope + ownership enforced in service)."""
    entry = await allergy_service.update_catalog_allergy(
        db, catalog_id, current_user, data
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Allergy catalog entry not found")
    return entry


@router.delete("/catalog/{catalog_id}")
async def delete_catalog_entry(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    success = await allergy_service.delete_catalog_allergy(
        db, catalog_id, current_user
    )
    if not success:
        raise HTTPException(status_code=404, detail="Allergy catalog entry not found")
    return {"message": "Allergy catalog entry deleted"}


@router.get("/catalog/{catalog_id}/usage")
async def get_allergy_usage(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Cross-patient usage of one allergen (drives the detail-page tab)."""
    return await allergy_service.get_allergy_usage(
        db, catalog_id, current_user.tenant_id
    )


@router.post(
    "/catalog/{catalog_id}/reprocess", response_model=AllergyCatalogResponse
)
async def reprocess_allergy(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """AI re-enrich the allergen catalog entry (best-effort)."""
    result = await allergy_service.reprocess_allergy(
        db, catalog_id, current_user.tenant_id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Allergy catalog entry not found")
    return result


# ---------------------------------------------------------------------------
# Cross-patient "active alerts" feed (allergy-unique; keeps the dashboard card
# and the legacy /alerts surface working).
# ---------------------------------------------------------------------------


@router.get("/active", response_model=List[AllergyIntoleranceResponse])
async def get_all_active_allergies(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Active intolerances in the tenant (or just the user's own patients
    for the ``USER`` role). Powers the dashboard ``AllergyAlertsCard``."""
    if current_user.role == Role.USER.value:
        from app.models.fhir.allergy import AllergyClinicalStatus, AllergyIntolerance
        from sqlalchemy import select

        query = (
            select(AllergyIntolerance)
            .join(Patient)
            .where(
                Patient.user_id == current_user.user_id,
                AllergyIntolerance.tenant_id == current_user.tenant_id,
                AllergyIntolerance.clinical_status == AllergyClinicalStatus.ACTIVE,
                AllergyIntolerance.deleted_at.is_(None),
            )
        )
        result = await db.execute(query)
        return result.scalars().all()

    rows = await allergy_service.get_active_allergies_by_tenant(
        db, current_user.tenant_id
    )
    # The service returns enriched dicts (with patient_name_display) — return
    # as-is; the response_model accepts ORM objects OR dicts.
    return rows


# ---------------------------------------------------------------------------
# Patient-instance intolerances
# ---------------------------------------------------------------------------


@router.get(
    "/patient/{patient_id}", response_model=List[AllergyIntoleranceResponse]
)
async def get_patient_allergies(
    patient_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_patient_access(patient_id, current_user, db)
    return await allergy_service.get_patient_allergies(
        db, patient_id, current_user.tenant_id
    )


@router.post(
    "/patient/{patient_id}", response_model=AllergyIntoleranceResponse
)
async def add_patient_allergy(
    patient_id: UUID,
    data: AllergyIntoleranceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_patient_access(patient_id, current_user, db)
    return await allergy_service.add_patient_allergy(
        db, patient_id, current_user.tenant_id, data
    )


@router.get("/{allergy_id}", response_model=AllergyIntoleranceResponse)
async def get_allergy(
    allergy_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Fetch one patient-instance allergy by id."""
    return await check_allergy_access(allergy_id, current_user, db)


@router.put("/{allergy_id}", response_model=AllergyIntoleranceResponse)
async def update_allergy(
    allergy_id: UUID,
    data: AllergyIntoleranceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_allergy_access(allergy_id, current_user, db)
    item = await allergy_service.update_patient_allergy(
        db, allergy_id, current_user.tenant_id, data
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Allergy record not found")
    return item


@router.delete("/{allergy_id}")
async def delete_allergy(
    allergy_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_allergy_access(allergy_id, current_user, db)
    success = await allergy_service.delete_patient_allergy(
        db, allergy_id, current_user.tenant_id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Allergy record not found")
    return {"message": "Allergy record deleted"}
