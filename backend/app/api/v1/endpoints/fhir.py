from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enums import Role
from app.api.v1.endpoints.utils import check_patient_access
from app.services.fhir_service import (
    list_patients,
    create_patient,
    update_patient,
    delete_patient,
    update_patient_layout,
    get_observation,
    get_observation_history,
    list_observations,
    create_observation,
    delete_observation,
    get_diagnostic_report,
    create_diagnostic_report,
    get_medication,
    list_medications,
    create_medication,
)

from app.schemas.user import TokenData

router = APIRouter(prefix="/fhir", tags=["fhir"])


@router.get("/Patient/{patient_id}")
async def get_patient_endpoint(
    patient_id: str, 
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get patient by ID"""
    return await check_patient_access(patient_id, current_user, db)


@router.put("/Patient/{patient_id}/layout")
async def update_patient_layout_endpoint(
    patient_id: str,
    layout: dict,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update patient dashboard layout"""
    await check_patient_access(patient_id, current_user, db)
    patient = await update_patient_layout(patient_id, layout)
    return patient


@router.put("/Patient/{patient_id}")
async def update_patient_endpoint(
    patient_id: str,
    patient_data: dict,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update patient information"""
    await check_patient_access(patient_id, current_user, db)
    patient = await update_patient(patient_id, patient_data)
    return patient


@router.delete("/Patient/{patient_id}")
async def delete_patient_endpoint(
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete patient and all associated clinical data"""
    patient = await check_patient_access(patient_id, current_user, db)
    
    # Only Admin or the linked User can delete a patient
    if current_user.role not in [Role.SYSTEM_ADMIN.value, Role.ADMIN.value]:
        if str(patient.user_id) != str(current_user.user_id):
            raise HTTPException(status_code=403, detail="Access denied")

    success = await delete_patient(patient_id)
    if not success:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"message": "Patient deleted successfully"}


@router.get("/Patient")
async def list_patients_endpoint(
    tenant_id: str = Query(None),
    user_id: str = Query(None),
    limit: int = Query(10),
    offset: int = Query(0),
    current_user: TokenData = Depends(get_current_user),
):
    """List patients (with pagination and optional user_id filter)"""
    # Enforce tenant isolation for non-system admins
    final_tenant_id = current_user.tenant_id
    final_user_id = user_id

    if current_user.role == Role.SYSTEM_ADMIN.value:
        if tenant_id:
            final_tenant_id = tenant_id
        if not tenant_id and user_id:
            # If system admin is looking for a specific user's patient, don't restrict by admin's tenant
            final_tenant_id = None
    elif current_user.role == Role.USER.value:
        # Standard users can only see patients linked to them
        final_user_id = str(current_user.user_id)
    
    # ADMIN and MANAGER roles see all patients in their tenant (final_tenant_id = current_user.tenant_id)
    # unless they also want to filter by a specific user_id

    patients = await list_patients(final_tenant_id, limit, offset, user_id=final_user_id)
    return patients


@router.post("/Patient")
async def create_patient_endpoint(
    patient_data: dict, current_user: TokenData = Depends(get_current_user)
):
    """Create a new patient"""
    # Force user_id for standard users to ensure they can see their own created patients
    if current_user.role == Role.USER.value:
        patient_data["user_id"] = str(current_user.user_id)
        
    patient = await create_patient(patient_data, current_user.tenant_id)
    return patient


@router.get("/Observation")
async def list_observations_endpoint(
    patient_id: str = Query(None),
    code: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List observations (with filtering and pagination)"""
    if patient_id:
        await check_patient_access(patient_id, current_user, db)
    elif current_user.role == Role.USER.value:
        return {"items": [], "total": 0}

    observations = await list_observations(
        current_user.tenant_id, patient_id, code, start_date, end_date
    )
    return observations


@router.get("/Observation/history")
async def get_observation_history_endpoint(
    patient_id: str,
    code: str,
    period: str = Query("last-6-months"),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get observation history for a patient+code pair (audit A2, B5).

    A2: previously called ``get_observation(patient_id, code, period)`` but
    ``get_observation`` takes only ``(observation_id)`` — every call raised
    TypeError. Now calls the new ``get_observation_history`` service fn.

    B5: the service filters by ``current_user.tenant_id`` so cross-tenant
    reads are impossible even with a guessed patient_id.

    NOTE: this route MUST be declared before ``/Observation/{observation_id}``
    or FastAPI will match ``history`` as the path parameter and route the
    request to ``get_observation_endpoint``.
    """
    await check_patient_access(patient_id, current_user, db)
    history = await get_observation_history(
        tenant_id=current_user.tenant_id,
        patient_id=patient_id,
        code=code,
        period=period,
    )
    return {"items": history, "total": len(history)}


@router.get("/Observation/{observation_id}")
async def get_observation_endpoint(
    observation_id: str, current_user: TokenData = Depends(get_current_user)
):
    """Get observation by ID"""
    observation = await get_observation(observation_id, current_user.tenant_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    return observation


@router.delete("/Observation/{observation_id}")
async def delete_observation_endpoint(
    observation_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete observation by ID"""
    # Audit B5: service-level tenant scoping (defense in depth) — the
    # get_observation / delete_observation lookups now filter on tenant_id
    # so a cross-tenant delete is impossible even if a future caller forgets
    # to check. The patient-access check below remains for USER-role scoping.
    observation = await get_observation(observation_id, current_user.tenant_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")

    # Audit B5: tenant scoping now happens at the service level, so the
    # explicit tenant_id comparison is redundant. The USER-role
    # patient-access check below remains — Observation.subject holds the
    # patient reference and USER must own that patient.
    if current_user.role == Role.USER.value:
        patient_id = None
        subject = observation.subject
        if subject and "reference" in subject:
            ref = subject["reference"]
            if "Patient/" in ref:
                patient_id = ref.split("/")[-1]

        if patient_id:
            await check_patient_access(patient_id, current_user, db)
        else:
            # If no patient link found in subject, fall back to tenant check (already done)
            # but usually observations should have a subject.
            pass

    success = await delete_observation(observation_id, current_user.tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail="Observation not found")
    return {"message": "Observation deleted successfully"}


@router.post("/Observation")
async def create_observation_endpoint(
    observation_data: dict, 
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new observation"""
    # Extract patient_id from subject or patient_id field
    patient_id = observation_data.get("patient_id")
    subject = observation_data.get("subject", {})
    if not patient_id and subject and "reference" in subject:
        ref = subject["reference"]
        if "Patient/" in ref:
            patient_id = ref.split("/")[-1]
    
    if patient_id:
        await check_patient_access(patient_id, current_user, db)
    elif current_user.role == Role.USER.value:
        raise HTTPException(status_code=400, detail="Patient reference required")
    
    # Ensure subject is set for FHIR compatibility if we only have patient_id
    if patient_id and not subject:
        observation_data["subject"] = {"reference": f"Patient/{patient_id}"}

    observation = await create_observation(observation_data, current_user.tenant_id)
    return observation


@router.get("/DiagnosticReport/{report_id}")
async def get_diagnostic_report_endpoint(
    report_id: str, current_user: TokenData = Depends(get_current_user)
):
    """Get diagnostic report by ID"""
    report = await get_diagnostic_report(report_id, current_user.tenant_id)
    if not report:
        raise HTTPException(status_code=404, detail="Diagnostic report not found")
    return report


@router.post("/DiagnosticReport")
async def create_diagnostic_report_endpoint(
    report_data: dict, current_user: TokenData = Depends(get_current_user)
):
    """Create a new diagnostic report"""
    report = await create_diagnostic_report(report_data, current_user.tenant_id)
    return report


@router.get("/Medication/{medication_id}")
async def get_medication_endpoint(
    medication_id: str, current_user: TokenData = Depends(get_current_user)
):
    """Get medication by ID"""
    medication = await get_medication(medication_id, current_user.tenant_id)
    if not medication:
        raise HTTPException(status_code=404, detail="Medication not found")
    return medication


@router.get("/Medication")
async def list_medications_endpoint(
    patient_id: str = Query(None),
    status: str = Query(None),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List medications (with filtering and pagination)"""
    if patient_id:
        await check_patient_access(patient_id, current_user, db)
    elif current_user.role == Role.USER.value:
        return {"items": [], "total": 0}

    medications = await list_medications(
        tenant_id=current_user.tenant_id,
        patient_id=patient_id,
        status=status,
        limit=100,
        offset=0,
    )
    return medications


@router.post("/Medication")
async def create_medication_endpoint(
    medication_data: dict, 
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new medication"""
    patient_id = medication_data.get("patient_id")
    if not patient_id:
        subject = medication_data.get("subject", {})
        if subject and "reference" in subject:
            ref = subject["reference"]
            if "Patient/" in ref:
                patient_id = ref.split("/")[-1]
    
    if patient_id:
        await check_patient_access(patient_id, current_user, db)
    elif current_user.role == Role.USER.value:
        raise HTTPException(status_code=400, detail="Patient reference required")

    medication = await create_medication(medication_data, current_user.tenant_id)
    return medication
