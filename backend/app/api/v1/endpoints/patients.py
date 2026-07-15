from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enums import Role
from app.services.access import check_patient_access
from app.services.fhir_service import (
    list_patients,
    create_patient,
    update_patient,
    delete_patient,
    update_patient_layout,
)
from app.schemas.user import TokenData

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("")
async def list_patients_endpoint(
    tenant_id: str = Query(None),
    user_id: str = Query(None),
    limit: int = Query(10),
    offset: int = Query(0),
    current_user: TokenData = Depends(get_current_user),
):
    """List patients (with pagination and optional user_id filter)"""
    final_tenant_id = current_user.tenant_id
    final_user_id = user_id

    if current_user.role == Role.SYSTEM_ADMIN.value:
        if tenant_id:
            final_tenant_id = tenant_id
        if not tenant_id and user_id:
            final_tenant_id = None
    elif current_user.role == Role.USER.value:
        final_user_id = str(current_user.user_id)

    patients = await list_patients(
        final_tenant_id, limit, offset, user_id=final_user_id
    )
    return patients


@router.post("")
async def create_patient_endpoint(
    patient_data: dict,
    current_user: TokenData = Depends(get_current_user),
):
    """Create a new patient"""
    if current_user.role == Role.USER.value:
        patient_data["user_id"] = str(current_user.user_id)

    patient = await create_patient(patient_data, current_user.tenant_id)
    return patient


@router.get("/{patient_id}")
async def get_patient_endpoint(
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get patient by ID"""
    return await check_patient_access(patient_id, current_user, db)


@router.put("/{patient_id}/layout")
async def update_patient_layout_endpoint(
    patient_id: str,
    layout: dict,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update patient dashboard layout (legacy single-layout slot on Patient.dashboard_layout).

    Distinct from the per-user multi-layout routes at
    ``/patients/{patient_id}/layouts/*`` (``patient_layout.py``).
    """
    await check_patient_access(patient_id, current_user, db)
    patient = await update_patient_layout(patient_id, layout)
    return patient


@router.put("/{patient_id}")
async def update_patient_endpoint(
    patient_id: str,
    patient_data: dict,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update patient information"""
    await check_patient_access(patient_id, current_user, db)
    patient = await update_patient(patient_id, patient_data)
    return patient


@router.delete("/{patient_id}")
async def delete_patient_endpoint(
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete patient and all associated clinical data"""
    patient = await check_patient_access(patient_id, current_user, db)

    if current_user.role not in [Role.SYSTEM_ADMIN.value, Role.ADMIN.value]:
        if str(patient.user_id) != str(current_user.user_id):
            raise HTTPException(status_code=403, detail="Access denied")

    success = await delete_patient(patient_id)
    if not success:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"message": "Patient deleted successfully"}
