from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.patient_access_grant import GrantPermission
from app.schemas.medication import (
    MedicationCatalogCreate,
    MedicationCatalogResponse,
    MedicationCatalogUpdate,
    MedicationRecordCreate,
    MedicationRecordResponse,
    MedicationRecordUpdate,
)
from app.schemas.user import TokenData
from app.services.audit.audit_service import audit_read, log_audit_action
from app.services.auth.access import check_medication_access, check_patient_access
from app.services.catalog import catalog_write_service
from app.services.clinical import medication_service

router = APIRouter(prefix="/medications", tags=["medications"])


@router.get("/catalog", response_model=list[MedicationCatalogResponse])
async def get_medication_catalog(
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    rows = await medication_service.get_medication_catalog(db, current_user.tenant_id, search)
    return [
        catalog_write_service.serialize_catalog_row(row, current_user.tenant_id) for row in rows
    ]


@router.get("/catalog/{catalog_id}", response_model=MedicationCatalogResponse)
async def get_catalog_medication(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    result = await medication_service.get_catalog_medication(db, catalog_id, current_user.tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="Medication not found in catalog")
    return catalog_write_service.serialize_catalog_row(result, current_user.tenant_id)


@router.post("/catalog", response_model=MedicationCatalogResponse, status_code=201)
async def create_catalog_medication(
    data: MedicationCatalogCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create a medication catalog entry (scope derived from role).

    Thin entry point onto the unified write contract — the same service the
    generic ``POST /catalogs/medication`` route calls. Unknown/read-only
    fields are rejected 422 by the schema; duplicate codes → 409.
    """
    return await catalog_write_service.create_medication(db, current_user, data)


@router.put("/catalog/{catalog_id}", response_model=MedicationCatalogResponse)
async def update_catalog_medication(
    catalog_id: UUID,
    data: MedicationCatalogUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Update a medication catalog entry. Scope + ownership enforced in the
    write service (raises ``CatalogPermissionDenied`` → 403)."""
    result = await catalog_write_service.update_medication(db, current_user, catalog_id, data)
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
    success = await catalog_write_service.delete_medication(db, current_user, catalog_id)
    if not success:
        raise HTTPException(status_code=404, detail="Medication not found in catalog")
    return {"status": "success", "message": "medication item deleted"}


@router.get("/patient/{patient_id}", response_model=list[MedicationRecordResponse])
async def get_patient_medications(
    patient_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_patient_access(patient_id, current_user, db)
    return await medication_service.get_patient_medications(db, patient_id, current_user.tenant_id)


@router.post("/patient/{patient_id}", response_model=MedicationRecordResponse)
async def add_patient_medication(
    patient_id: UUID,
    data: MedicationRecordCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    data.patient_id = patient_id
    record = await medication_service.add_patient_medication(db, current_user, data)
    # §17: clinical writes are audited (plan 16 H2).
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="create_medication",
        resource_type="Medication",
        resource_id=getattr(record, "id", None),
        new_value=data.model_dump(mode="json"),
    )
    return record


@router.put("/{medication_id}", response_model=MedicationRecordResponse)
async def update_patient_medication(
    medication_id: UUID,
    data: MedicationRecordUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    await check_medication_access(medication_id, current_user, db, required=GrantPermission.WRITE)
    result = await medication_service.update_patient_medication(
        db, medication_id, current_user, data
    )
    if not result:
        raise HTTPException(status_code=404, detail="Medication record not found")
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="update_medication",
        resource_type="Medication",
        resource_id=medication_id,
        new_value=data.model_dump(mode="json", exclude_unset=True),
    )
    return result


@router.get("/{medication_id}", response_model=MedicationRecordResponse)
@audit_read("Medication", id_param="medication_id")
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
    await check_medication_access(medication_id, current_user, db, required=GrantPermission.WRITE)
    success = await medication_service.delete_patient_medication(db, medication_id, current_user)
    if not success:
        raise HTTPException(status_code=404, detail="Medication record not found")
    await log_audit_action(
        required=True,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="delete_medication",
        resource_type="Medication",
        resource_id=medication_id,
    )
    return {"message": "Medication record deleted"}


@router.get("/catalog/{catalog_id}/usage")
async def get_medication_usage(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    return await medication_service.get_medication_usage(db, catalog_id, current_user.tenant_id)


@router.post("/catalog/{catalog_id}/reprocess", response_model=MedicationCatalogResponse)
async def reprocess_medication(
    catalog_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    result = await medication_service.reprocess_medication(db, catalog_id, current_user.tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="Medication not found in catalog")
    return catalog_write_service.serialize_catalog_row(result, current_user.tenant_id)
