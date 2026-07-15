from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enums import Role
from app.services.access import check_patient_access
from app.services.fhir_service import (
    get_observation,
    list_observations,
    create_observation,
    delete_observation,
)
from app.schemas.user import TokenData
from app.services.audit_service import log_audit_action

router = APIRouter(prefix="/observations", tags=["observations"])


@router.get("")
async def list_observations_endpoint(
    patient_id: str = Query(None),
    code: str = Query(None),
    start_date: str = Query(None),
    end_date: str = Query(None),
    limit: int = Query(100),
    offset: int = Query(0),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List observations (with filtering and pagination)"""
    if patient_id:
        await check_patient_access(patient_id, current_user, db)
    elif current_user.role == Role.USER.value:
        return {"items": [], "total": 0}

    observations = await list_observations(
        current_user.tenant_id,
        patient_id,
        code,
        start_date,
        end_date,
        limit=limit,
        offset=offset,
    )
    return observations


@router.post("")
async def create_observation_endpoint(
    observation_data: dict,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new observation"""
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

    if patient_id and not subject:
        observation_data["subject"] = {"reference": f"Patient/{patient_id}"}

    observation = await create_observation(observation_data, current_user.tenant_id)
    await log_audit_action(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="create_observation",
        resource_type="Observation",
        resource_id=getattr(observation, "id", None),
        new_value=observation_data,
    )
    return observation


@router.get("/{observation_id}")
async def get_observation_endpoint(
    observation_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Get observation by ID (tenant-scoped at the service layer)"""
    observation = await get_observation(observation_id, current_user.tenant_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    return observation


@router.delete("/{observation_id}")
async def delete_observation_endpoint(
    observation_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete observation by ID"""
    observation = await get_observation(observation_id, current_user.tenant_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")

    if current_user.role == Role.USER.value:
        patient_id = None
        subject = observation.subject
        if subject and "reference" in subject:
            ref = subject["reference"]
            if "Patient/" in ref:
                patient_id = ref.split("/")[-1]

        if patient_id:
            await check_patient_access(patient_id, current_user, db)

    old_snapshot = observation.to_dict() if hasattr(observation, "to_dict") else None
    success = await delete_observation(observation_id, current_user.tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail="Observation not found")
    await log_audit_action(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="delete_observation",
        resource_type="Observation",
        resource_id=observation.id if hasattr(observation, "id") else None,
        old_value=old_snapshot,
    )
    return {"message": "Observation deleted successfully"}
