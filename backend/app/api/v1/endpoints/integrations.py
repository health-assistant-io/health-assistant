from typing import Any, List, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging
from uuid import UUID

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.models.user_integration import UserIntegration
from app.models.system_integration import SystemIntegration
from app.models.fhir.patient import Patient
from app.models.enums import IntegrationStatus
from app.core.integration_registry import integration_registry

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/available", response_model=List[Dict[str, Any]])
async def list_available_integrations(db: AsyncSession = Depends(get_db)) -> Any:
    """
    List all available integrations discovered in the system that are explicitly enabled.
    """
    manifests = integration_registry.get_all_manifests()
    
    stmt = select(SystemIntegration).where(SystemIntegration.is_enabled == True)
    result = await db.execute(stmt)
    enabled_domains = {i.domain for i in result.scalars().all()}
    
    return [m for m in manifests if m.get("domain") in enabled_domains]

@router.get("/active", response_model=List[Dict[str, Any]])
async def list_active_integrations(
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    List active integrations for the current patient context.
    """
    stmt = select(UserIntegration).where(
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_id
    )
    result = await db.execute(stmt)
    integrations = result.scalars().all()
    
    return [
        {
            "id": str(i.id),
            "domain": i.provider,
            "status": i.status.value,
            "last_synced_at": i.last_synced_at,
        }
        for i in integrations
    ]

@router.get("/{domain}/config-flow", response_model=Dict[str, Any])
async def get_config_flow(
    domain: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Get the configuration UI schema for an integration.
    """
    # Check if system has enabled it
    stmt = select(SystemIntegration).where(SystemIntegration.domain == domain, SystemIntegration.is_enabled == True)
    result = await db.execute(stmt)
    if not result.scalar_one_or_none():
         raise HTTPException(status_code=400, detail="Integration is not enabled by system admin.")
         
    config_flow = integration_registry.get_config_flow(domain)
    if not config_flow:
        raise HTTPException(status_code=404, detail="Integration config flow not found")
        
    return await config_flow.get_schema()

@router.post("/{domain}/config-flow", response_model=Dict[str, Any])
async def submit_config_flow(
    domain: str,
    patient_id: str,
    payload: Dict[str, Any],
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Submit configuration data and setup the integration.
    """
    # Check if system has enabled it
    stmt = select(SystemIntegration).where(SystemIntegration.domain == domain, SystemIntegration.is_enabled == True)
    result = await db.execute(stmt)
    if not result.scalar_one_or_none():
         raise HTTPException(status_code=400, detail="Integration is not enabled by system admin.")

    config_flow = integration_registry.get_config_flow(domain)
    if not config_flow:
        raise HTTPException(status_code=404, detail="Integration config flow not found")
        
    try:
        validated_config = await config_flow.validate_input(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    # Check for existing integration
    stmt = select(UserIntegration).where(
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_id,
        UserIntegration.provider == domain
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        existing.user_config = validated_config
        existing.status = IntegrationStatus.ACTIVE
    else:
        # Create new integration
        # Verify the patient belongs to the user or their tenant
        stmt_patient = select(Patient).where(
            Patient.id == patient_id,
            Patient.tenant_id == current_user.tenant_id
        ).limit(1)
        res_patient = await db.execute(stmt_patient)
        patient = res_patient.scalar_one_or_none()
        
        if not patient:
            raise HTTPException(status_code=400, detail="Invalid Patient record.")
            
        new_integration = UserIntegration(
            user_id=current_user.user_id,
            patient_id=patient.id,
            provider=domain,
            status=IntegrationStatus.ACTIVE,
            user_config=validated_config,
            tenant_id=current_user.tenant_id
        )
        db.add(new_integration)
        
    await db.commit()
    return {"message": "Integration configured successfully."}

@router.get("/{domain}/details")
async def get_integration_details(
    domain: str,
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information about an active integration."""
    from app.models.user_integration import UserIntegration, IntegrationSyncLog
    from app.models.fhir.patient import Observation
    from app.models.biomarker_model import BiomarkerDefinition
    from sqlalchemy import select, desc, func

    try:
        patient_uuid = UUID(patient_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format for user or patient")

    stmt = select(UserIntegration).where(
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_uuid,
        UserIntegration.provider == domain
    )
    result = await db.execute(stmt)
    integration = result.scalar_one_or_none()

    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found or not active")

    # Fetch sync logs
    logs_stmt = select(IntegrationSyncLog).where(
        IntegrationSyncLog.integration_id == integration.id
    ).order_by(desc(IntegrationSyncLog.started_at)).limit(20)
    logs_result = await db.execute(logs_stmt)
    sync_logs = logs_result.scalars().all()

    # Fetch exposed items (distinct biomarkers synced by this integration)
    # This queries observations where performer display is the domain
    obs_stmt = select(Observation.biomarker_id, func.max(Observation.effective_datetime).label("last_seen")).where(
        Observation.tenant_id == integration.tenant_id,
        Observation.subject["reference"].astext == f"Patient/{patient_id}",
        Observation.performer[0]["display"].astext == domain,
        Observation.biomarker_id != None
    ).group_by(Observation.biomarker_id)
    
    obs_res = await db.execute(obs_stmt)
    exposed_rows = obs_res.all()
    
    exposed_items = []
    if exposed_rows:
        b_ids = [row[0] for row in exposed_rows]
        b_stmt = select(BiomarkerDefinition).where(BiomarkerDefinition.id.in_(b_ids))
        b_res = await db.execute(b_stmt)
        b_defs = {b.id: b for b in b_res.scalars().all()}
        
        for row in exposed_rows:
            b_id = row[0]
            last_seen = row[1]
            if b_id in b_defs:
                b = b_defs[b_id]
                exposed_items.append({
                    "id": str(b.id),
                    "name": b.name,
                    "slug": b.slug,
                    "category": b.category,
                    "last_seen": last_seen.isoformat() if last_seen else None
                })
                
    provider = integration_registry.get_provider(domain)
    custom_actions = []
    if provider and hasattr(provider, "get_custom_actions"):
        custom_actions = provider.get_custom_actions()

    return {
        "id": str(integration.id),
        "domain": integration.provider,
        "status": integration.status.value,
        "user_config": integration.user_config,
        "last_synced_at": integration.last_synced_at.isoformat() if integration.last_synced_at else None,
        "sync_history": [
            {
                "id": str(log.id),
                "status": log.status,
                "records_synced": log.records_synced,
                "started_at": log.started_at.isoformat(),
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
                "error_message": log.error_message
            }
            for log in sync_logs
        ],
        "exposed_items": exposed_items,
        "custom_actions": custom_actions
    }

@router.delete("/{domain}")
async def remove_integration(
    domain: str,
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Remove an active integration.
    """
    stmt = select(UserIntegration).where(
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_id,
        UserIntegration.provider == domain
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")
        
    await db.delete(existing)
    await db.commit()
    return {"message": "Integration removed successfully."}

import datetime

@router.post("/{domain}/action/{action_id}")
async def execute_custom_action(
    domain: str,
    action_id: str,
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Execute a custom action defined by the integration provider."""
    stmt = select(UserIntegration).where(
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_id,
        UserIntegration.provider == domain
    )
    result = await db.execute(stmt)
    integration = result.scalar_one_or_none()
    
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
        
    provider = integration_registry.get_provider(domain)
    if not provider:
        raise HTTPException(status_code=404, detail="Integration provider not loaded")
        
    if not hasattr(provider, "execute_custom_action"):
        raise HTTPException(status_code=400, detail="Provider does not support custom actions")
        
    try:
        response = await provider.execute_custom_action(integration, action_id)
        # Commit any changes to user_config (like cursors) made by the action
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(integration, "user_config")
        await db.commit()
        return response
    except NotImplementedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Custom action {action_id} failed for {domain}: {e}")
        raise HTTPException(status_code=500, detail=f"Action failed: {str(e)}")

@router.post("/{domain}/sync")
async def sync_integration(
    domain: str,
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Manually trigger a sync for an active integration.
    """
    stmt = select(UserIntegration).where(
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_id,
        UserIntegration.provider == domain,
        UserIntegration.status == IntegrationStatus.ACTIVE
    )
    result = await db.execute(stmt)
    integration = result.scalar_one_or_none()
    
    if not integration:
        raise HTTPException(status_code=404, detail="Active integration not found")
        
    provider = integration_registry.get_provider(domain)
    if not provider:
        raise HTTPException(status_code=404, detail="Integration provider not loaded")
        
    from app.models.user_integration import IntegrationSyncLog
    import datetime

    from app.integrations.sdk.exceptions import IntegrationAuthError, IntegrationRateLimitError

    try:
        start_time = datetime.datetime.now(datetime.timezone.utc)
        observations_data = await provider.pull_data(integration)
        count = 0
        if observations_data:
            from app.models.fhir import Observation
            # Convert to ORM models BEFORE passing to mapping
            observations = []
            for obs_data in observations_data:
                obs_dict = obs_data.model_dump(exclude_unset=True) if hasattr(obs_data, "model_dump") else obs_data.dict(exclude_unset=True) if hasattr(obs_data, "dict") else obs_data
                obs = Observation(**obs_dict)
                observations.append(obs)
                
            from app.services.fhir_service import map_observations_to_biomarkers
            await map_observations_to_biomarkers(db, observations)
            for obs in observations:
                if not obs.performer:
                    obs.performer = [{"type": "Integration", "display": integration.provider}]
                db.add(obs)
                count += 1
                
        await provider.push_data(integration, {"status": "manual_sync"})
        
        integration.last_synced_at = datetime.datetime.now(datetime.timezone.utc)
        
        # Log the sync
        sync_log = IntegrationSyncLog(
            integration_id=integration.id,
            tenant_id=integration.tenant_id,
            status="success",
            records_synced=count,
            started_at=start_time,
            completed_at=integration.last_synced_at
        )
        db.add(sync_log)
        
        await db.commit()
        return {
            "message": "Sync completed successfully", 
            "metrics_synced": count,
            "last_synced_at": integration.last_synced_at
        }
    except IntegrationAuthError as e:
        logger.error(f"Auth failed for {domain}: {e}")
        integration.status = IntegrationStatus.ERROR
        sync_log = IntegrationSyncLog(
            integration_id=integration.id,
            tenant_id=integration.tenant_id,
            status="failed",
            records_synced=0,
            started_at=start_time,
            completed_at=datetime.datetime.now(datetime.timezone.utc),
            error_message=str(e)
        )
        db.add(sync_log)
        await db.commit()
        raise HTTPException(status_code=401, detail="Integration authentication failed. Please re-authenticate.")
    except IntegrationRateLimitError as e:
        logger.error(f"Rate limit hit for {domain}: {e}")
        raise HTTPException(status_code=429, detail="Third-party API rate limit exceeded. Try again later.")
    except Exception as e:
        logger.error(f"Manual sync failed for {domain}: {e}")
        # Log failure
        sync_log = IntegrationSyncLog(
            integration_id=integration.id,
            tenant_id=integration.tenant_id,
            status="failed",
            records_synced=0,
            started_at=datetime.datetime.now(datetime.timezone.utc),
            completed_at=datetime.datetime.now(datetime.timezone.utc),
            error_message=str(e)
        )
        db.add(sync_log)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")
