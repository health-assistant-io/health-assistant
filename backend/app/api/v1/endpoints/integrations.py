from typing import Any, List, Dict
import os
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import logging
from uuid import UUID

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.models.user_integration import UserIntegration
from app.models.system_integration import SystemIntegration
from app.models.user_model import UserModel
from app.models.fhir.patient import Patient
from app.models.enums import IntegrationStatus
from app.core.integration_registry import integration_registry
from integrations.sdk.auth import OAuthStateStore
from integrations.sdk.exceptions import IntegrationAuthError, IntegrationDataError

logger = logging.getLogger(__name__)

router = APIRouter()


def _frontend_origin() -> str:
    """The SPA origin for OAuth callback redirects. Defaults to dev port 3000."""
    return os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")

@router.get("/available", response_model=List[Dict[str, Any]])
async def list_available_integrations(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    List all available integrations discovered in the system that are explicitly enabled.

    Requires an authenticated user (any role).
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
            "instance_name": i.instance_name,
            "status": i.status.value,
            "last_synced_at": i.last_synced_at,
        }
        for i in integrations
    ]

@router.get("/{domain}/documentation")
async def get_integration_documentation(
    domain: str,
    file: str = None,
    current_user: TokenData = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get the markdown documentation for an integration if it exists.

    Requires an authenticated user (any role). Path traversal is mitigated
    via ``os.path.basename``.
    """
    import os
    import json
    from app.core.integration_registry import integration_registry

    # We don't check if it's enabled here, so users can read docs before enabling.
    # We do check if the domain is known to the registry (discovered).
    manifests = integration_registry.get_all_manifests()
    if not any(m.get("domain") == domain for m in manifests):
        raise HTTPException(status_code=404, detail="Integration not found")
        
    base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "integrations", domain))
    
    # 1. Check for structured docs (docs-tree.json)
    docs_tree_path = os.path.join(base_path, "docs", "docs-tree.json")
    if os.path.exists(docs_tree_path):
        try:
            with open(docs_tree_path, "r") as f:
                tree = json.load(f)
                
            target_file = file
            if not target_file:
                for category in tree:
                    if category.get("items") and len(category["items"]) > 0:
                        target_file = category["items"][0].get("file")
                        break
                        
            markdown_content = ""
            if target_file:
                # Prevent directory traversal attacks
                target_file = os.path.basename(target_file)
                target_file_path = os.path.join(base_path, "docs", target_file)
                if os.path.exists(target_file_path):
                    with open(target_file_path, "r") as f:
                        markdown_content = f.read()
                else:
                     markdown_content = f"# Error\n\nCould not find file {target_file} in docs folder."
                        
            return {
                "markdown": markdown_content,
                "tree": tree
            }
        except Exception as e:
            logger.error(f"Failed to parse docs-tree.json for {domain}: {e}")

    # 2. Check for legacy single-file docs
    doc_paths = [
        os.path.join(base_path, "README.md"),
        os.path.join(base_path, "DOCS.md")
    ]
    
    for path in doc_paths:
        if os.path.exists(path):
            with open(path, "r") as f:
                return {"markdown": f.read()}
                
    # 3. If no file exists, return an empty string or a default message
    return {"markdown": f"# {domain.capitalize()} Integration\n\nNo documentation provided for this integration."}

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
    integration_id: str = None,
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

    # Extract instance_name if provided, otherwise default to domain
    instance_name = validated_config.pop("instance_name", domain.capitalize())

    # Generic: let the config flow encrypt any secret fields it declared.
    # No-op for integrations with no secret fields (no key required).
    try:
        validated_config = await config_flow.prepare_for_storage(validated_config)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Generic: enforce per-user instance cap if the config flow declared one.
    if not integration_id and config_flow.max_instances_per_user is not None:
        from sqlalchemy import func as _func
        count_stmt = select(_func.count()).select_from(UserIntegration).where(
            UserIntegration.user_id == current_user.user_id,
            UserIntegration.provider == domain,
        )
        count_res = await db.execute(count_stmt)
        existing_count = int(count_res.scalar() or 0)
        cap = config_flow.max_instances_per_user
        if existing_count >= cap:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"You already have {existing_count} instance(s) of "
                    f"{domain} configured. The per-user limit is {cap}."
                ),
            )

    # Check if this is an update to an existing instance
    if integration_id:
        try:
            integration_uuid = UUID(integration_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid integration ID format")
            
        stmt = select(UserIntegration).where(
            UserIntegration.id == integration_uuid,
            UserIntegration.user_id == current_user.user_id,
            UserIntegration.patient_id == patient_id
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if not existing:
            raise HTTPException(status_code=404, detail="Integration instance not found")
            
        existing.user_config = validated_config
        existing.instance_name = instance_name
    else:
        # Create new integration since we allow multiples
        # Verify the patient belongs to the user or their tenant
        stmt_patient = select(Patient).where(
            Patient.id == patient_id,
            Patient.tenant_id == current_user.tenant_id
        ).limit(1)
        res_patient = await db.execute(stmt_patient)
        patient = res_patient.scalar_one_or_none()
        
        if not patient:
            raise HTTPException(status_code=400, detail="Invalid Patient record.")
            
        # OAuth integrations start PENDING only when THIS instance actually
        # needs the OAuth round-trip (auth_mode == "smart"). Tokenless instances
        # (auth_mode == "none", e.g. a local HAPI FHIR) go straight to ACTIVE.
        needs_oauth = (
            config_flow.is_oauth
            and validated_config.get("auth_mode", "smart") == "smart"
        )
        new_integration = UserIntegration(
            user_id=current_user.user_id,
            patient_id=patient.id,
            provider=domain,
            instance_name=instance_name,
            status=IntegrationStatus.PENDING if needs_oauth else IntegrationStatus.ACTIVE,
            user_config=validated_config,
            tenant_id=current_user.tenant_id
        )
        db.add(new_integration)
        
    await db.commit()
    return {"message": "Integration configured successfully."}


# ---------------- OAuth round-trip (opt-in via config_flow.is_oauth) ----------------


async def _load_enabled_oauth(domain: str, db: AsyncSession):
    """Resolve the enabled system integration + provider + config_flow for an OAuth domain."""
    stmt = select(SystemIntegration).where(
        SystemIntegration.domain == domain, SystemIntegration.is_enabled == True
    )
    result = await db.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Integration is not enabled by system admin.")
    provider = integration_registry.get_provider(domain)
    config_flow = integration_registry.get_config_flow(domain)
    if not provider or not config_flow:
        raise HTTPException(status_code=404, detail="Integration not loaded.")
    if not getattr(config_flow, "is_oauth", False):
        raise HTTPException(status_code=400, detail=f"{domain} is not an OAuth integration.")
    return provider, config_flow


@router.post("/{domain}/oauth/start")
async def oauth_start(
    domain: str,
    integration_id: str,
    patient_id: str,
    request: Request,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Begin the OAuth Authorization Code flow: discover + DCR + authorize URL.

    The caller (frontend) redirects the user's browser to the returned
    ``authorize_url``. The PKCE verifier + SMART endpoints + ``integration_id``/
    ``user_id`` are stored under an opaque ``state`` in Redis (short TTL).
    """
    provider, _ = await _load_enabled_oauth(domain, db)

    try:
        integration_uuid = UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration ID format")

    stmt = select(UserIntegration).where(
        UserIntegration.id == integration_uuid,
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_id,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if not existing:
        raise HTTPException(status_code=404, detail="Integration instance not found")

    redirect_uri = f"{str(request.base_url).rstrip('/')}/api/v1/integrations/{domain}/oauth/callback"
    try:
        authorize_url, state = await provider.begin_oauth(
            existing,
            redirect_uri,
            extra_state={
                "integration_id": str(existing.id),
                "user_id": str(current_user.user_id),
                "tenant_id": str(current_user.tenant_id),
            },
        )
    except (IntegrationAuthError, IntegrationDataError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"authorize_url": authorize_url, "state": state}


@router.get("/{domain}/oauth/callback")
async def oauth_callback(
    domain: str,
    state: str,
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """OAuth callback (browser redirect, unauthenticated — secured by `state`).

    Consumes the one-shot ``state`` (which carries ``integration_id``), exchanges
    the code for tokens via the provider, persists them encrypted, flips the
    instance to ACTIVE, then 302-redirects to the SPA ``/connected`` landing.
    """
    provider, _ = await _load_enabled_oauth(domain, db)

    pending = await OAuthStateStore().consume(state)
    if not pending:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")

    integration_id = pending.get("integration_id")
    user_id = pending.get("user_id")
    if not integration_id or not user_id:
        raise HTTPException(status_code=400, detail="Malformed OAuth state payload.")

    try:
        integration_uuid = UUID(integration_id)
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed identifiers in OAuth state.")

    stmt = select(UserIntegration).where(
        UserIntegration.id == integration_uuid, UserIntegration.user_id == user_uuid
    )
    integration = (await db.execute(stmt)).scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=404, detail="Integration instance not found.")

    try:
        await provider.complete_oauth(integration, pending, code)
    except (IntegrationAuthError, IntegrationDataError):
        redirect = (
            f"{_frontend_origin()}/integrations/{domain}/connected"
            f"?integration_id={integration_id}&status=error"
        )
        return Response(status_code=302, headers={"Location": redirect})

    integration.status = IntegrationStatus.ACTIVE
    await db.commit()

    redirect = (
        f"{_frontend_origin()}/integrations/{domain}/connected"
        f"?integration_id={integration_id}&status=connected"
    )
    return Response(status_code=302, headers={"Location": redirect})


@router.get("/instance/{integration_id}/details")
async def get_integration_details(
    integration_id: str,
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information about an active integration."""
    from app.models.user_integration import UserIntegration, IntegrationSyncLog
    from app.models.fhir.patient import Observation
    from app.models.biomarker_model import BiomarkerDefinition
    from app.models.examination_model import ExaminationModel
    from sqlalchemy import desc, func

    try:
        integration_uuid = UUID(integration_id)
        patient_uuid = UUID(patient_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format for user or patient")

    stmt = select(UserIntegration).where(
        UserIntegration.id == integration_uuid,
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_uuid
    )
    result = await db.execute(stmt)
    integration = result.scalar_one_or_none()

    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found or not active")
        
    domain = integration.provider

    # Fetch sync logs
    logs_stmt = select(IntegrationSyncLog).where(
        IntegrationSyncLog.integration_id == integration.id
    ).order_by(desc(IntegrationSyncLog.started_at)).limit(20)
    logs_result = await db.execute(logs_stmt)
    sync_logs = logs_result.scalars().all()

    from sqlalchemy import or_
    
    # Fetch exposed items (distinct biomarkers synced by this integration)
    # Match on modern Integration UUID reference OR legacy domain display name
    obs_stmt = select(Observation.biomarker_id, func.max(Observation.effective_datetime).label("last_seen")).where(
        Observation.tenant_id == integration.tenant_id,
        Observation.subject["reference"].astext == f"Patient/{patient_id}",
        or_(
            Observation.performer[0]["reference"].astext == f"Integration/{integration.id}",
            Observation.performer[0]["display"].astext == domain
        ),
        Observation.biomarker_id != None
    ).group_by(Observation.biomarker_id)
    
    obs_res = await db.execute(obs_stmt)
    exposed_rows = obs_res.all()
    
    exposed_items = []
    b_defs = {}
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
                
    # Fetch recent actual measurements
    recent_obs_stmt = select(Observation).where(
        Observation.tenant_id == integration.tenant_id,
        Observation.subject["reference"].astext == f"Patient/{patient_id}",
        or_(
            Observation.performer[0]["reference"].astext == f"Integration/{integration.id}",
            Observation.performer[0]["display"].astext == domain
        ),
        Observation.biomarker_id != None
    ).order_by(desc(Observation.effective_datetime)).limit(30)
    
    recent_obs_res = await db.execute(recent_obs_stmt)
    recent_obs = recent_obs_res.scalars().all()
    
    recent_data = []
    for obs in recent_obs:
        b_name = b_defs.get(obs.biomarker_id).name if obs.biomarker_id in b_defs else obs.code.get("text", "Unknown Metric")
        b_slug = b_defs.get(obs.biomarker_id).slug if obs.biomarker_id in b_defs else None
        unit = obs.value_quantity.get("unit", "") if obs.value_quantity else ""
        recent_data.append({
            "id": str(obs.id),
            "date": obs.effective_datetime.isoformat() if obs.effective_datetime else None,
            "sync_time": obs.created_at.isoformat() if hasattr(obs, 'created_at') and obs.created_at else None,
            "metric": b_name,
            "slug": b_slug,
            "biomarker_id": str(obs.biomarker_id) if obs.biomarker_id else None,
            "value": obs.raw_value,
            "unit": unit,
            "examination_id": str(obs.examination_id) if obs.examination_id else None
        })
                
    # Fetch synced examinations
    exam_stmt = select(ExaminationModel).where(
        ExaminationModel.tenant_id == integration.tenant_id,
        ExaminationModel.patient_id == patient_uuid,
        ExaminationModel.source_integration_id == integration.id
    ).order_by(desc(ExaminationModel.examination_date))
    
    exam_res = await db.execute(exam_stmt)
    synced_examinations = [exam.to_dict() for exam in exam_res.scalars().all()]

    provider = integration_registry.get_provider(domain)
    custom_actions = []
    if provider and hasattr(provider, "get_custom_actions"):
        custom_actions = provider.get_custom_actions()

    # Generic: let the config flow mask secret fields before returning to UI.
    config_flow = integration_registry.get_config_flow(domain)
    if config_flow:
        returned_config = config_flow.prepare_for_read(integration.user_config or {})
    else:
        returned_config = integration.user_config

    # Surface the last push result + sync direction for the FHIR server (and any
    # other integration that writes them). Lives under _sync_state cursors.
    sync_state = (integration.user_config or {}).get("_sync_state") or {}

    return {
        "id": str(integration.id),
        "domain": integration.provider,
        "instance_name": integration.instance_name,
        "status": integration.status.value,
        "user_config": returned_config,
        "is_debug_enabled": integration.is_debug_enabled,
        "last_synced_at": integration.last_synced_at.isoformat() if integration.last_synced_at else None,
        "sync_direction": (integration.user_config or {}).get("sync_direction"),
        "push_status": sync_state.get("last_push_result"),
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
        "recent_data": recent_data,
        "synced_examinations": synced_examinations,
        "custom_actions": custom_actions
    }

@router.get("/instance/{integration_id}/debug-logs")
async def get_integration_debug_logs(
    integration_id: str,
    patient_id: str,
    limit: int = 200,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch debug logs for a specific integration instance."""
    from app.models.user_integration import UserIntegration, IntegrationDebugLog
    from sqlalchemy import desc
    try:
        integration_uuid = UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration ID")

    # Verify ownership
    stmt = select(UserIntegration).where(
        UserIntegration.id == integration_uuid,
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_id
    )
    result = await db.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Integration not found")

    logs_stmt = select(IntegrationDebugLog).where(
        IntegrationDebugLog.integration_id == integration_uuid
    ).order_by(desc(IntegrationDebugLog.timestamp)).limit(limit)
    
    logs_result = await db.execute(logs_stmt)
    debug_logs = logs_result.scalars().all()

    return [
        {
            "id": str(log.id),
            "timestamp": log.timestamp.isoformat(),
            "level": log.level,
            "title": log.title,
            "payload": log.payload
        }
        for log in debug_logs
    ]

@router.post("/instance/{integration_id}/toggle-debug")
async def toggle_integration_debug(
    integration_id: str,
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Toggle debug mode for a specific integration instance."""
    try:
        integration_uuid = UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration ID")
        
    stmt = select(UserIntegration).where(
        UserIntegration.id == integration_uuid,
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_id
    )
    result = await db.execute(stmt)
    integration = result.scalar_one_or_none()
    
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
        
    integration.is_debug_enabled = not integration.is_debug_enabled
    await db.commit()
    return {"message": f"Debug mode {'enabled' if integration.is_debug_enabled else 'disabled'}.", "is_debug_enabled": integration.is_debug_enabled}

@router.delete("/instance/{integration_id}")
async def remove_integration(
    integration_id: str,
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Remove an active integration instance.
    """
    try:
        integration_uuid = UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration ID")
        
    stmt = select(UserIntegration).where(
        UserIntegration.id == integration_uuid,
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_id
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")

    # Best-effort token revocation on disconnect (RFC 7009) — prevents stale
    # tokens from lingering on the remote server. Wrapped in try/except so a
    # revocation failure never blocks the delete.
    provider = integration_registry.get_provider(existing.provider)
    if provider and hasattr(provider, "revoke"):
        try:
            await provider.revoke(existing)
        except Exception:
            logger.warning("Token revocation failed for %s — deleting anyway", integration_id)

    await db.delete(existing)
    await db.commit()
    return {"message": "Integration removed successfully."}

import datetime

@router.post("/instance/{integration_id}/action/{action_id}")
async def execute_custom_action(
    integration_id: str,
    action_id: str,
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Execute a custom action defined by the integration provider."""
    try:
        integration_uuid = UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration ID")
        
    stmt = select(UserIntegration).where(
        UserIntegration.id == integration_uuid,
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_id
    )
    result = await db.execute(stmt)
    integration = result.scalar_one_or_none()
    
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
        
    domain = integration.provider
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


@router.get("/notification-types")
async def list_notification_types(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Aggregate every enabled integration's declared notification types.

    Returns a list of ``{domain, instance_name, types: [{id, label,
    description, category, severity, default_enabled, channels, enabled}]}``
    for every UserIntegration the caller has, where ``enabled`` reflects
    the caller's per-type pref (defaults to ``default_enabled`` when the
    user hasn't set an explicit override). Used by both the central
    ``/settings/notifications`` rollup AND the per-integration tab (the
    latter filters client-side by domain).
    """
    stmt = select(UserIntegration).where(
        UserIntegration.user_id == current_user.user_id,
    )
    integrations = (await db.execute(stmt)).scalars().all()

    # Load the caller's settings once.
    user_row = (
        await db.execute(
            select(UserModel.settings).where(UserModel.id == current_user.user_id)
        )
    ).scalar_one_or_none()
    user_settings = dict(user_row or {})

    out = []
    for integration in integrations:
        provider = integration_registry.get_provider(integration.provider)
        if provider is None:
            continue
        getter = getattr(provider, "get_notification_types", None)
        if getter is None:
            continue
        try:
            types = getter() or []
        except Exception:
            logger.exception(
                "get_notification_types failed for %s", integration.provider
            )
            continue
        if not types:
            continue
        out.append({
            "domain": integration.provider,
            "instance_name": integration.instance_name,
            "integration_id": str(integration.id),
            "types": [
                {
                    **t.to_dict(),
                    "enabled": _resolve_type_enabled(
                        user_settings, integration.provider, t
                    ),
                }
                for t in types
            ],
        })
    return {"integrations": out}


@router.put("/{domain}/notification-types/{type_id}")
async def update_notification_type_pref(
    domain: str,
    type_id: str,
    payload: Dict[str, Any],
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Set the caller's per-type preference for an integration's notification kind.

    Body: ``{"enabled": bool}``. Stored at
    ``user.settings["notifications.integration.{domain}.{type_id}"]``.
    The integration must be one the caller has enabled (404 otherwise).
    """
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="`enabled` (bool) is required")

    # Confirm the caller owns an integration of this domain.
    stmt = select(UserIntegration.id).where(
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.provider == domain,
    ).limit(1)
    has_integration = (await db.execute(stmt)).first() is not None
    if not has_integration:
        raise HTTPException(
            status_code=404,
            detail=f"No enabled integration for domain '{domain}'",
        )

    key = f"notifications.integration.{domain}.{type_id}"
    user = (
        await db.execute(
            select(UserModel).where(UserModel.id == current_user.user_id)
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    settings = dict(user.settings or {})
    if enabled:
        # Remove the override so it falls back to default_enabled.
        # (Storing True is also fine but explicit-removal keeps the JSONB tidy.)
        settings.pop(key, None)
    else:
        settings[key] = False
    user.settings = settings
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(user, "settings")
    await db.commit()
    return {
        "status": "success",
        "domain": domain,
        "type_id": type_id,
        "enabled": enabled,
    }


def _resolve_type_enabled(user_settings: Dict[str, Any], domain: str, type_decl: Any) -> bool:
    """USER setting wins; otherwise the provider's default_enabled; else True."""
    key = f"notifications.integration.{domain}.{type_decl.id}"
    if key in user_settings:
        return bool(user_settings[key])
    return getattr(type_decl, "default_enabled", True)


@router.post("/{domain}/notification-action/{integration_id}/{action_id}")
async def execute_notification_action(
    domain: str,
    integration_id: str,
    action_id: str,
    payload: Dict[str, Any] | None = None,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Dispatch a clicked notification action button to the provider.

    Triggered by the notification detail modal when a user clicks an action
    of ``type="post"`` (the endpoint path is the action's ``endpoint`` URL).
    Routes to ``provider.handle_notification_action(integration, action_id,
    payload)`` — providers return an ActionResult dict that the frontend
    renders as a follow-up modal.

    Tenant-scoped to the caller (must own the integration).
    """
    try:
        integration_uuid = UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration ID")

    stmt = select(UserIntegration).where(
        UserIntegration.id == integration_uuid,
        UserIntegration.user_id == current_user.user_id,
    )
    integration = (await db.execute(stmt)).scalar_one_or_none()
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    if integration.provider != domain:
        raise HTTPException(status_code=400, detail="Integration domain mismatch")

    provider = integration_registry.get_provider(domain)
    if provider is None:
        raise HTTPException(status_code=404, detail="Integration provider not loaded")
    if not getattr(provider, "supports_notifications", lambda: False)():
        raise HTTPException(
            status_code=400,
            detail="Provider does not support notification actions",
        )

    try:
        response = await provider.handle_notification_action(
            integration, action_id, payload or {}
        )
        # Persist any provider-side state changes (cursor bumps, ack flags, etc.)
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(integration, "user_config")
        await db.commit()
        return response
    except NotImplementedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Notification action %s failed for %s: %s", action_id, domain, e
        )
        raise HTTPException(status_code=500, detail=f"Action failed: {str(e)}")

@router.post("/instance/{integration_id}/sync")
async def sync_integration(
    integration_id: str,
    patient_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Manually trigger a sync for an active integration instance.
    """
    try:
        integration_uuid = UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration ID")
        
    stmt = select(UserIntegration).where(
        UserIntegration.id == integration_uuid,
        UserIntegration.user_id == current_user.user_id,
        UserIntegration.patient_id == patient_id,
        UserIntegration.status == IntegrationStatus.ACTIVE
    )
    result = await db.execute(stmt)
    integration = result.scalar_one_or_none()
    
    if not integration:
        raise HTTPException(status_code=404, detail="Active integration not found")
        
    domain = integration.provider
    provider = integration_registry.get_provider(domain)
    if not provider:
        raise HTTPException(status_code=404, detail="Integration provider not loaded")

    from app.services.integration_sync_service import run_sync

    result = await run_sync(db, integration, provider, source="manual")

    if result.status == "skipped":
        raise HTTPException(
            status_code=409,
            detail="A sync is already in progress for this integration. Try again in a moment.",
        )
    if result.status == "failed":
        if result.error_type == "auth":
            raise HTTPException(
                status_code=401,
                detail="Integration authentication failed. Please re-authenticate.",
            )
        if result.error_type == "rate_limit":
            raise HTTPException(
                status_code=429,
                detail="Third-party API rate limit exceeded. Try again later.",
            )
        raise HTTPException(status_code=500, detail=f"Sync failed: {result.error}")

    message = (
        "Sync completed successfully"
        if result.dropped_invalid == 0
        else f"Sync completed with {result.dropped_invalid} invalid observation(s) dropped"
    )
    return {
        "message": message,
        "metrics_synced": result.fhir_persisted + result.telemetry_persisted,
        "pulled": result.pulled,
        "dropped_invalid": result.dropped_invalid,
        "status": result.status,
        "last_synced_at": integration.last_synced_at,
    }

from fastapi import Request


def _verify_webhook_signature(
    secret: str, raw_body: bytes, provided_signature: str
) -> bool:
    """Constant-time HMAC-SHA256 verification of a webhook payload.

    Used to authenticate inbound webhook deliveries when an integration has
    configured a ``webhook_secret``. The route verifies an HMAC-SHA256
    signature over the raw body before processing the payload.

    Supported header formats (case-insensitive lookup by caller):
      - ``X-Webhook-Signature``: ``<hex digest>``
      - ``X-Webhook-Signature-256``: ``<hex digest>``
      - ``X-Hub-Signature-256`` (GitHub): ``sha256=<hex digest>``

    Returns True iff the computed HMAC matches the provided signature.
    """
    import hashlib
    import hmac as _hmac

    if not secret or not provided_signature:
        return False

    computed = _hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()

    # Strip an optional "sha256=" prefix (GitHub convention) before comparing.
    provided = provided_signature.strip()
    if provided.lower().startswith("sha256="):
        provided = provided[len("sha256="):]

    return _hmac.compare_digest(computed, provided.strip().lower())


def _verify_api_signature(
    secret: str,
    method: str,
    path: str,
    raw_body: bytes,
    provided_signature: str,
    provided_timestamp: str | None = None,
    max_skew_seconds: int = 300,
) -> bool:
    """Constant-time HMAC-SHA256 verification of an inbound two-way API call.

    Audit item B8: the generic API proxy at
    ``/{domain}/api/{integration_id}/{path}`` historically used the
    integration UUID itself as the only credential. UUIDs leak via logs,
    browser history, JSON responses — anyone who has seen one had full
    bidirectional API access for the lifetime of the integration.

    When an integration configures ``api_secret`` in its ``user_config``,
    the proxy now requires a valid HMAC-SHA256 signature over a canonical
    request string:

        <METHOD>\n<path>\n<raw_body>

    Supported header:
      - ``X-Api-Signature``: ``<hex digest>`` (required when api_secret set)
      - ``X-Api-Timestamp``: ``<epoch_seconds>`` (optional but recommended;
        if present, request is rejected when skew > ``max_skew_seconds``
        and the timestamp is folded into the signed payload to prevent
        replay)

    Returns True iff the computed HMAC matches the provided signature
    AND (when timestamp is supplied) the timestamp is within the allowed
    skew window. Returns False when ``secret`` or ``provided_signature``
    is empty.
    """
    import hashlib
    import hmac as _hmac
    import time

    if not secret or not provided_signature:
        return False

    canonical_parts = [method.upper().encode("utf-8"), b"\n", path.encode("utf-8"), b"\n"]
    if provided_timestamp:
        # Fold the timestamp into the signed payload so a captured signature
        # cannot be replayed after the skew window.
        try:
            ts_int = int(provided_timestamp)
        except (ValueError, TypeError):
            return False
        now = int(time.time())
        if abs(now - ts_int) > max_skew_seconds:
            return False
        canonical_parts.append(provided_timestamp.encode("utf-8") + b"\n")
    canonical_parts.append(raw_body)
    canonical = b"".join(canonical_parts)

    computed = _hmac.new(
        secret.encode("utf-8"), canonical, hashlib.sha256
    ).hexdigest()

    return _hmac.compare_digest(computed, provided_signature.strip().lower())


@router.post("/{domain}/webhook/{integration_id}")
async def integration_webhook(
    domain: str,
    integration_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Handle incoming webhooks for a specific integration.

    When a ``webhook_secret`` is configured on the integration's
    ``user_config``, the request MUST carry a valid HMAC-SHA256 signature
    header; otherwise the request is rejected with 401. Integrations without
    a configured secret retain the legacy behaviour (integration_id acts as
    the secret) for backward compatibility — operators are encouraged to set
    a webhook_secret.
    """
    try:
        integration_uuid = UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration ID format")

    stmt = select(UserIntegration).where(
        UserIntegration.id == integration_uuid,
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
        
    if not hasattr(provider, "handle_webhook"):
        raise HTTPException(status_code=400, detail="Provider does not support webhooks")

    # If the integration configured a ``webhook_secret``, verify the
    # HMAC-SHA256 signature of the raw body before processing. This
    # prevents unauthorized POSTs even if the integration UUID leaks.
    # Read the raw body ONCE here so the signature check and the downstream
    # JSON parse share the exact bytes.
    raw_body = await request.body()

    cfg = getattr(integration, "user_config", None) or {}
    webhook_secret = cfg.get("webhook_secret") if isinstance(cfg, dict) else None
    if webhook_secret:
        # Accept any of the conventional signature headers.
        provided_sig = (
            request.headers.get("X-Webhook-Signature")
            or request.headers.get("X-Webhook-Signature-256")
            or request.headers.get("X-Hub-Signature-256")
        )
        if not provided_sig or not _verify_webhook_signature(
            webhook_secret, raw_body, provided_sig
        ):
            logger.warning(
                "Webhook signature verification failed for %s (Integration: %s)",
                domain,
                integration_id,
            )
            raise HTTPException(
                status_code=401, detail="Webhook signature verification failed"
            )

    from app.models.user_integration import IntegrationSyncLog

    try:
        payload = (
            raw_body.decode("utf-8") if raw_body else ""
        )
        import json as _json

        payload = _json.loads(payload) if payload else {}
    except Exception:
        payload = {}  # Maybe it's a form or empty body, let the provider handle it

    try:
        start_time = datetime.datetime.now(datetime.timezone.utc)
        observations_data = await provider.handle_webhook(integration, payload, request)
        count = 0
        if observations_data:
            from app.models.fhir import Observation
            from app.models.biomarker_model import BiomarkerDefinition
            
            # Convert to ORM models BEFORE passing to mapping
            observations = []
            for obs_data in observations_data:
                obs_dict = obs_data.model_dump(exclude_unset=True) if hasattr(obs_data, "model_dump") else obs_data.dict(exclude_unset=True) if hasattr(obs_data, "dict") else obs_data
                obs = Observation(**obs_dict)
                observations.append(obs)
                
            from app.services.fhir_service import map_observations_to_biomarkers
            await map_observations_to_biomarkers(db, observations)
            
            # Fetch all definitions used
            b_ids = list(set([obs.biomarker_id for obs in observations if obs.biomarker_id]))
            b_defs_map = {}
            if b_ids:
                stmt = select(BiomarkerDefinition).where(BiomarkerDefinition.id.in_(b_ids))
                res = await db.execute(stmt)
                for b in res.scalars().all():
                    b_defs_map[b.id] = b

            from app.models.telemetry_model import TelemetryDataModel

            telemetry_records = []
            fhir_records = []

            for obs in observations:
                is_telemetry = False
                if obs.biomarker_id and obs.biomarker_id in b_defs_map:
                    is_telemetry = b_defs_map[obs.biomarker_id].is_telemetry
                
                if is_telemetry:
                    # Convert observation to telemetry data point
                    slug = b_defs_map[obs.biomarker_id].slug.lower() if b_defs_map[obs.biomarker_id].slug else ""
                    val = getattr(obs, "normalized_value", None) or getattr(obs, "raw_value", None) or (obs.value_quantity.get("value") if obs.value_quantity else None)
                    
                    hr = val if slug == "8867-4" or "heart-rate" in slug else None
                    steps = val if slug == "41950-7" or "steps" in slug else None
                    cal = val if "calories" in slug else None
                    
                    data_payload = {}
                    if not hr and not steps and not cal:
                        data_payload[slug] = val
                        data_payload[f"{slug}_unit"] = obs.value_quantity.get("unit", "") if obs.value_quantity else ""

                    telemetry_records.append(TelemetryDataModel(
                        tenant_id=integration.tenant_id,
                        device_id=integration.instance_name or integration.provider,
                        timestamp=obs.effective_datetime,
                        heart_rate=hr,
                        steps=steps,
                        calories=cal,
                        data=data_payload if data_payload else None
                    ))
                else:
                    if not obs.performer:
                        obs.performer = [{"type": "Integration", "display": integration.instance_name or integration.provider, "reference": f"Integration/{integration.id}"}]
                    fhir_records.append(obs)
            
            if telemetry_records:
                db.add_all(telemetry_records)
            if fhir_records:
                db.add_all(fhir_records)
            count += len(telemetry_records) + len(fhir_records)
                
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
        # Best-effort notification dispatch (baseline + provider-authored).
        # Closes the webhook→notification gap: previously webhooks bypassed
        # run_sync entirely, so neither the "synced N records" baseline nor
        # any provider-authored threshold/HITL notifications fired for
        # webhook-driven integrations.
        try:
            from app.services.integration_sync_service import post_sync_notifications
            await post_sync_notifications(
                provider,
                integration,
                pulled=count,
                fhir_persisted=count,  # webhook handler doesn't split FHIR vs telemetry today
                telemetry_persisted=0,
                status="success",
                started_at=start_time,
                completed_at=integration.last_synced_at,
                observations=observations_data or [],
            )
        except Exception as webhook_notif_err:
            logger.warning(
                "Webhook post-sync notification dispatch failed for %s: %s",
                domain, webhook_notif_err,
            )
        return {"message": "Webhook processed successfully", "metrics_synced": count}
    except Exception as e:
        await db.rollback()
        logger.error(f"Webhook failed for {domain} (Integration: {integration_id}): {e}")
        
        if integration.is_debug_enabled and hasattr(provider, "log_debug_payload"):
            try:
                await provider.log_debug_payload(integration, "Webhook Error", {"error": str(e)}, level="error")
            except Exception:
                pass
                
        # Log failure
        failure_completed = datetime.datetime.now(datetime.timezone.utc)
        sync_log = IntegrationSyncLog(
            integration_id=integration.id,
            tenant_id=integration.tenant_id,
            status="failed",
            records_synced=0,
            started_at=start_time,
            completed_at=failure_completed,
            error_message=str(e)
        )
        db.add(sync_log)
        await db.commit()
        # Best-effort failure notification — webhooks previously failed silently
        # from the user's POV (no row in the inbox, no admin escalation).
        try:
            from app.services.integration_sync_service import post_sync_notifications
            await post_sync_notifications(
                provider,
                integration,
                pulled=0,
                fhir_persisted=0,
                telemetry_persisted=0,
                status="failed",
                started_at=start_time,
                completed_at=failure_completed,
                error=str(e),
                error_type="data",
                observations=[],
            )
        except Exception as webhook_notif_err:
            logger.warning(
                "Webhook failure-notification dispatch failed for %s: %s",
                domain, webhook_notif_err,
            )
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")

@router.api_route("/{domain}/api/{integration_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def integration_api_proxy(
    domain: str,
    integration_id: str,
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Handle generic two-way API requests for a specific integration.

    Audit item B8: the integration UUID is no longer the only credential.
    When an integration configures ``api_secret`` in its ``user_config``,
    this route requires a valid ``X-Api-Signature`` header (HMAC-SHA256
    of ``METHOD\\n<path>\\n[<timestamp>\\n]<raw_body>``) before any work
    is done. Without the secret the legacy UUID-only behaviour is
    preserved for backward compatibility, but a warning is logged so
    operators are nudged toward configuring a secret.

    See ``_verify_api_signature`` for the canonical signing scheme.
    """
    try:
        integration_uuid = UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid integration ID format")

    stmt = select(UserIntegration).where(
        UserIntegration.id == integration_uuid,
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

    if not hasattr(provider, "handle_api_request"):
        raise HTTPException(status_code=400, detail="Provider does not support API requests")

    # API-secret verification (B8). The raw body MUST be read once so
    # downstream JSON parsing shares the exact bytes used to verify.
    raw_body = await request.body()
    cfg = getattr(integration, "user_config", None) or {}
    api_secret = cfg.get("api_secret") if isinstance(cfg, dict) else None
    if api_secret:
        provided_sig = request.headers.get("X-Api-Signature")
        provided_ts = request.headers.get("X-Api-Timestamp")
        if not provided_sig or not _verify_api_signature(
            api_secret,
            method=request.method,
            path=path,
            raw_body=raw_body,
            provided_signature=provided_sig,
            provided_timestamp=provided_ts,
        ):
            raise HTTPException(
                status_code=401,
                detail=(
                    "Invalid or missing X-Api-Signature. This integration has an "
                    "api_secret configured; supply an HMAC-SHA256 of "
                    "METHOD\\n<path>\\n[<timestamp>\\n]<raw_body> in X-Api-Signature."
                ),
            )
    else:
        # Legacy UUID-only mode. Log once per call so operators notice the
        # gap and configure an api_secret. (We don't rate-limit the log to
        # avoid needing per-integration state.)
        logger.warning(
            "Integration %s (provider=%s) has no api_secret configured — "
            "API proxy is relying solely on the UUID, which is not a credential. "
            "Set user_config.api_secret to enable HMAC verification (audit B8).",
            integration_id,
            domain,
        )

    try:
        response_data = await provider.handle_api_request(
            integration=integration,
            path=path,
            method=request.method,
            request=request
        )
        # Commit any configuration changes the provider made (e.g., sync cursor update)
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(integration, "user_config")
        await db.commit()
        return response_data
    except NotImplementedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        # Pass ValueErrors (like validation or user errors) as 400 Bad Request
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"API request failed for {domain} (Integration: {integration_id}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"API request failed: {str(e)}")

