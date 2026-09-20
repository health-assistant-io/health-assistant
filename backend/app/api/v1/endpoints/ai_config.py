from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List, Dict, Any
from uuid import UUID

from app.core.database import get_db
from app.core.config import settings
from app.ai.providers.service import AIProviderService
from app.ai.providers import setup as byok_setup
from app.ai.schemas.config import (
    AIProviderCreate,
    AIProviderUpdate,
    AIProviderResponse,
    AIModelCreate,
    AIModelUpdate,
    AIModelResponse,
    AITaskAssignmentCreate,
    AITaskAssignmentUpdate,
    AITaskAssignmentResponse,
    AIProviderWithModelsResponse,
    AIConfigSummary,
    AIConfigUpdate,
    ProviderSetupRequest,
    ProviderSetupResponse,
    ProviderSetDefaultRequest,
    ProviderSetDefaultResponse,
    ProviderPresetsResponse,
    ProviderPresetResponse,
)
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.models.ai_provider_model import AIScope
from app.models.enums import Role


router = APIRouter(prefix="/ai-config", tags=["AI Configuration"])


def check_scope_access(
    scope: AIScope,
    current_user: TokenData,
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
):
    """Verify if the user has access to a specific configuration scope"""
    if scope == AIScope.SYSTEM:
        if current_user.role != Role.SYSTEM_ADMIN.value:
            raise HTTPException(
                status_code=403,
                detail="Only system admins can manage system configuration",
            )
    elif scope == AIScope.TENANT:
        if current_user.role not in [Role.SYSTEM_ADMIN.value, Role.ADMIN.value]:
            raise HTTPException(
                status_code=403, detail="Only admins can manage tenant configuration"
            )
        if current_user.role == Role.ADMIN.value and str(tenant_id) != str(
            current_user.tenant_id
        ):
            raise HTTPException(
                status_code=403, detail="Cannot manage configuration for another tenant"
            )
    elif scope == AIScope.USER:
        if (
            str(user_id) != str(current_user.user_id)
            and current_user.role != Role.SYSTEM_ADMIN.value
        ):
            # Standard admins can't even see other users' personal keys
            raise HTTPException(
                status_code=403,
                detail="Not authorized to manage this user's configuration",
            )


def verify_provider_access(provider, current_user: TokenData) -> None:
    """Ensure ``current_user`` may read or modify ``provider``.

    Provider records carry an api_key, so reads + writes are scope-checked
    against the row's ``tenant_id``/``user_id``/``scope`` before any response
    is built.
    """
    check_scope_access(
        provider.scope,
        current_user,
        tenant_id=provider.tenant_id,
        user_id=provider.user_id,
    )


def verify_model_access(model, provider, current_user: TokenData) -> None:
    """Scope-check via the model's owning provider."""
    if provider is None:
        # Caller couldn't resolve the provider — treat as forbidden rather
        # than leak the existence/absence of the model.
        raise HTTPException(status_code=403, detail="Access denied")
    verify_provider_access(provider, current_user)


# Provider endpoints
@router.post(
    "/providers", response_model=AIProviderResponse, status_code=status.HTTP_201_CREATED
)
async def create_provider(
    provider_data: AIProviderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create a new AI provider"""
    service = AIProviderService(db)

    check_scope_access(
        provider_data.scope,
        current_user,
        tenant_id=provider_data.tenant_id,
        user_id=provider_data.user_id or current_user.user_id,
    )

    if provider_data.scope == AIScope.TENANT and not provider_data.tenant_id:
        provider_data.tenant_id = current_user.tenant_id

    if provider_data.scope == AIScope.USER and not provider_data.user_id:
        provider_data.user_id = current_user.user_id

    provider = await service.create_provider(provider_data)
    return AIProviderResponse.model_validate(provider)


@router.get("/providers", response_model=List[AIProviderResponse])
async def get_providers(
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    scope: Optional[AIScope] = None,
    is_active: Optional[bool] = True,
    include_models: Optional[bool] = False,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get all AI providers with optional filtering"""
    service = AIProviderService(db)

    # Security: validate requested scope
    if scope:
        check_scope_access(scope, current_user, tenant_id=tenant_id, user_id=user_id)

    # If no scope specified, we filter based on user context
    search_tenant_id = tenant_id
    search_user_id = user_id

    if not scope:
        # Standard users see system and their own
        # Admins see system, their tenant, and their own
        if current_user.role == Role.USER.value:
            # We need to fetch both system and user scope.
            # The service.get_providers might need to be called multiple times or updated.
            # For simplicity, if no scope is requested, we show what they have access to.
            search_user_id = current_user.user_id
            search_tenant_id = None  # Don't show tenant providers to users
        elif current_user.role == Role.ADMIN.value:
            search_tenant_id = current_user.tenant_id
            search_user_id = current_user.user_id
        # SYSTEM_ADMIN sees everything if no filters applied

    providers = await service.get_providers(
        tenant_id=search_tenant_id,
        user_id=search_user_id,
        is_active=is_active,
        include_models=include_models,
        scope=scope,
    )

    return [AIProviderResponse.model_validate(p) for p in providers]


@router.get("/providers/{provider_id}", response_model=AIProviderResponse)
async def get_provider(
    provider_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get a specific AI provider.

    Tenant/user scoped via :func:`verify_provider_access` so cross-tenant
    reads are impossible (provider rows carry an api_key).
    """
    service = AIProviderService(db)
    provider = await service.get_provider(provider_id)

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    verify_provider_access(provider, current_user)
    return AIProviderResponse.model_validate(provider)


@router.get(
    "/providers/{provider_id}/with-models", response_model=AIProviderWithModelsResponse
)
async def get_provider_with_models(
    provider_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get a specific AI provider with its models (scope-checked)."""
    service = AIProviderService(db)
    provider = await service.get_provider(provider_id)

    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    verify_provider_access(provider, current_user)

    models = await service.get_models(provider_id=provider_id, is_active=True)

    provider_dict = provider.to_dict()
    provider_dict["models"] = [AIModelResponse.model_validate(m) for m in models]

    return AIProviderWithModelsResponse(**provider_dict)


@router.put("/providers/{provider_id}", response_model=AIProviderResponse)
async def update_provider(
    provider_id: UUID,
    provider_data: AIProviderUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Update an AI provider"""
    service = AIProviderService(db)

    # Security: Fetch existing to check scope
    existing = await service.get_provider(provider_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Provider not found")

    check_scope_access(
        existing.scope,
        current_user,
        tenant_id=existing.tenant_id,
        user_id=existing.user_id,
    )

    updated = await service.update_provider(provider_id, provider_data)
    return AIProviderResponse.model_validate(updated)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Delete an AI provider"""
    service = AIProviderService(db)

    # Security: Check ownership
    existing = await service.get_provider(provider_id)
    if not existing:
        return None  # Already gone

    check_scope_access(
        existing.scope,
        current_user,
        tenant_id=existing.tenant_id,
        user_id=existing.user_id,
    )

    await service.delete_provider(provider_id)
    return None


@router.get("/providers/{provider_id}/fetch-external-models")
async def fetch_external_models(
    provider_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Fetch available models from the provider's external API.

    Scope-checked against the provider row; ``api_base`` is SSRF-guarded
    (scheme + DNS-aware private/loopback blocking) inside the service via
    ``guard_api_base`` — audit 2026-09-11 S-2.
    """
    service = AIProviderService(db)
    provider = await service.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    verify_provider_access(provider, current_user)

    try:
        models = await service.fetch_external_models(provider_id)
        return models
    except ValueError as e:
        # guard_api_base's client-facing SSRF/config violation message.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger = __import__("logging").getLogger(__name__)
        logger.exception("fetch_external_models failed for provider %s", provider_id)
        raise HTTPException(status_code=502, detail="Upstream provider request failed")


# Model endpoints
@router.post(
    "/providers/{provider_id}/models",
    response_model=AIModelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_model(
    provider_id: UUID,
    model_data: AIModelCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create a new AI model for a provider (scope-checked)."""
    service = AIProviderService(db)

    provider = await service.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    verify_provider_access(provider, current_user)

    model = await service.create_model(model_data)
    return AIModelResponse.model_validate(model)


@router.get("/providers/{provider_id}/models", response_model=List[AIModelResponse])
async def get_models_for_provider(
    provider_id: UUID,
    is_active: Optional[bool] = True,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get all models for a specific provider (scope-checked)."""
    service = AIProviderService(db)
    provider = await service.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    verify_provider_access(provider, current_user)

    models = await service.get_models(provider_id=provider_id, is_active=is_active)
    return [AIModelResponse.model_validate(m) for m in models]


@router.get("/models/{model_id}", response_model=AIModelResponse)
async def get_model(
    model_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get a specific AI model (scope-checked via owning provider)."""
    service = AIProviderService(db)
    model = await service.get_model(model_id)

    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    provider = await service.get_provider(model.provider_id)
    verify_model_access(model, provider, current_user)

    return AIModelResponse.model_validate(model)


@router.put("/models/{model_id}", response_model=AIModelResponse)
async def update_model(
    model_id: UUID,
    model_data: AIModelUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Update an AI model (scope-checked via owning provider)."""
    service = AIProviderService(db)

    model = await service.get_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    provider = await service.get_provider(model.provider_id)
    verify_model_access(model, provider, current_user)

    updated = await service.update_model(model_id, model_data)
    return AIModelResponse.model_validate(updated)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Delete an AI model (scope-checked via owning provider)."""
    service = AIProviderService(db)

    model = await service.get_model(model_id)
    if not model:
        return None

    provider = await service.get_provider(model.provider_id)
    verify_model_access(model, provider, current_user)

    await service.delete_model(model_id)
    return None


# Task assignment endpoints
@router.post(
    "/task-assignments",
    response_model=AITaskAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_assignment(
    assignment_data: AITaskAssignmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create a new task assignment"""
    service = AIProviderService(db)

    check_scope_access(
        assignment_data.scope,
        current_user,
        tenant_id=assignment_data.tenant_id,
        user_id=assignment_data.user_id or current_user.user_id,
    )

    if assignment_data.scope == AIScope.TENANT and not assignment_data.tenant_id:
        assignment_data.tenant_id = current_user.tenant_id

    if assignment_data.scope == AIScope.USER and not assignment_data.user_id:
        assignment_data.user_id = current_user.user_id

    # Check if trying to set as active - ensure only one active per task type per scope
    if assignment_data.is_active:
        existing_active = await service.get_task_assignments(
            tenant_id=assignment_data.tenant_id,
            user_id=assignment_data.user_id,
            task_type=assignment_data.task_type,
            is_active=True,
            scope=assignment_data.scope,
        )
        for existing in existing_active:
            await service.update_task_assignment(
                existing.id, AITaskAssignmentUpdate(is_active=False)
            )

    assignment = await service.create_task_assignment(assignment_data)
    return AITaskAssignmentResponse.model_validate(assignment)


@router.get("/task-assignments", response_model=List[AITaskAssignmentResponse])
async def get_task_assignments(
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    scope: Optional[AIScope] = None,
    task_type: Optional[str] = None,
    is_active: Optional[bool] = True,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get task assignments with optional filtering"""
    service = AIProviderService(db)

    # Security: validate requested scope
    if scope:
        check_scope_access(scope, current_user, tenant_id=tenant_id, user_id=user_id)

    search_tenant_id = tenant_id
    search_user_id = user_id

    if not scope:
        if current_user.role == Role.USER.value:
            search_user_id = current_user.user_id
            search_tenant_id = None
        elif current_user.role == Role.ADMIN.value:
            search_tenant_id = current_user.tenant_id
            search_user_id = current_user.user_id

    assignments = await service.get_task_assignments(
        tenant_id=search_tenant_id,
        user_id=search_user_id,
        task_type=task_type,
        is_active=is_active,
        scope=scope,
    )
    return [AITaskAssignmentResponse.model_validate(a) for a in assignments]


@router.get(
    "/task-assignments/{assignment_id}", response_model=AITaskAssignmentResponse
)
async def get_task_assignment(
    assignment_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get a specific task assignment"""
    service = AIProviderService(db)
    assignment = await service.get_task_assignment(assignment_id)

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    check_scope_access(
        assignment.scope,
        current_user,
        tenant_id=assignment.tenant_id,
        user_id=assignment.user_id,
    )

    return AITaskAssignmentResponse.model_validate(assignment)


@router.put(
    "/task-assignments/{assignment_id}", response_model=AITaskAssignmentResponse
)
async def update_task_assignment(
    assignment_id: UUID,
    assignment_data: AITaskAssignmentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Update a task assignment"""
    service = AIProviderService(db)

    assignment = await service.get_task_assignment(assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    check_scope_access(
        assignment.scope,
        current_user,
        tenant_id=assignment.tenant_id,
        user_id=assignment.user_id,
    )

    # Check if trying to set as active - ensure only one active per task type per tenant/user/system
    if assignment_data.is_active:
        existing_active = await service.get_task_assignments(
            tenant_id=assignment.tenant_id,
            user_id=assignment.user_id,
            task_type=assignment.task_type,
            is_active=True,
            scope=assignment.scope,
        )
        for existing in existing_active:
            if existing.is_active and existing.id != assignment_id:
                await service.update_task_assignment(
                    existing.id, AITaskAssignmentUpdate(is_active=False)
                )

    updated = await service.update_task_assignment(assignment_id, assignment_data)

    if not updated:
        raise HTTPException(status_code=404, detail="Assignment not found")

    return AITaskAssignmentResponse.model_validate(updated)


@router.delete(
    "/task-assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_task_assignment(
    assignment_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Delete a task assignment"""
    service = AIProviderService(db)

    assignment = await service.get_task_assignment(assignment_id)
    if not assignment:
        return None

    check_scope_access(
        assignment.scope,
        current_user,
        tenant_id=assignment.tenant_id,
        user_id=assignment.user_id,
    )

    await service.delete_task_assignment(assignment_id)
    return None


@router.get(
    "/task-assignments/active/{task_type}", response_model=AITaskAssignmentResponse
)
async def get_active_task_assignment(
    task_type: str,
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get the active assignment for a specific task type"""
    service = AIProviderService(db)

    # Use explicitly requested user_id or fall back to current user
    search_user_id = user_id or current_user.user_id
    search_tenant_id = tenant_id or current_user.tenant_id

    assignment = await service.get_active_assignment_for_task(
        task_type, search_tenant_id, search_user_id
    )

    if not assignment:
        raise HTTPException(
            status_code=404, detail="No active assignment found for task type"
        )

    return AITaskAssignmentResponse.model_validate(assignment)


# Summary endpoint
@router.get("/summary", response_model=AIConfigSummary)
async def get_config_summary(
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    scope: Optional[AIScope] = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get a summary of AI configuration"""
    service = AIProviderService(db)

    # Security check if scope is specified
    if scope:
        check_scope_access(scope, current_user, tenant_id=tenant_id, user_id=user_id)

    search_tenant_id = tenant_id
    search_user_id = user_id

    if not scope:
        if current_user.role == Role.USER.value:
            search_user_id = current_user.user_id
            search_tenant_id = None
        elif current_user.role == Role.ADMIN.value:
            search_tenant_id = current_user.tenant_id
            search_user_id = current_user.user_id

    return await service.get_config_summary(
        tenant_id=search_tenant_id, user_id=search_user_id, scope=scope
    )


@router.put("/settings", status_code=status.HTTP_204_NO_CONTENT)
async def update_ai_settings(
    config_data: AIConfigUpdate,
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    scope: AIScope = AIScope.TENANT,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Update AI-specific settings"""
    service = AIProviderService(db)

    # Security check
    check_scope_access(scope, current_user, tenant_id=tenant_id, user_id=user_id)

    target_tenant_id = tenant_id
    if scope == AIScope.TENANT and not target_tenant_id:
        target_tenant_id = current_user.tenant_id

    success = await service.update_ai_settings(
        config_data=config_data,
        tenant_id=target_tenant_id,
        user_id=user_id,
        current_user_id=current_user.user_id,
    )

    if not success:
        raise HTTPException(status_code=400, detail="Failed to update settings")

    return None


@router.get("/default-for-task/{task_type}", response_model=Dict[str, Any])
async def get_default_for_task(
    task_type: str,
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get the default provider and model for a specific task type"""
    service = AIProviderService(db)

    search_user_id = user_id or current_user.user_id
    search_tenant_id = tenant_id or current_user.tenant_id

    assignment = await service.get_active_assignment_for_task(
        task_type, search_tenant_id, search_user_id
    )

    if not assignment:
        raise HTTPException(
            status_code=404, detail="No active assignment found for task type"
        )

    provider = await service.get_provider(assignment.provider_id)
    model = (
        await service.get_model(assignment.model_id) if assignment.model_id else None
    )

    return {
        "provider": AIProviderResponse.model_validate(provider),
        "model": AIModelResponse.model_validate(model) if model else None,
    }


# BYOK one-click setup (§15) — scope-aware (SYSTEM / TENANT / USER)


@router.get("/provider-presets", response_model=ProviderPresetsResponse)
async def list_provider_presets():
    """The one-click setup tile surface (§15 canonical data + health overlay).

    Public metadata only — no keys, no secrets. Disabled registry-native
    presets are reported with their recorded reasons so the UI stays
    explainable.
    """
    from app.ai.providers.presets import (
        DISABLED_PRESET_REASONS,
        PRESET_ORDER,
        SETUP_PRESETS,
    )

    presets = {
        key: ProviderPresetResponse(
            key=key,
            name=row["name"],
            provider_type=row["type"],
            wire_type=row["wire_type"],
            base_url=row["base_url"],
            fixed_base=row["fixed_base"],
            local=row["local"],
            key_url=row["key_url"],
            preferred_model=row["preferred_model"],
            curated_models=row["curated_models"],
            stt_model=row["stt_model"],
            steps=row["steps"],
            free_tier_note=row["free_tier_note"],
        )
        for key, row in SETUP_PRESETS.items()
    }
    return ProviderPresetsResponse(
        order=[key for key in PRESET_ORDER if key in presets],
        presets=presets,
        disabled=dict(DISABLED_PRESET_REASONS),
    )


def _scope_context(scope: AIScope, current_user: TokenData) -> Dict[str, Any]:
    """The (tenant_id, user_id) pair the requested scope binds to.

    Mirrors ``create_provider``: SYSTEM rows carry neither id, TENANT rows
    the caller's tenant, USER rows the caller's own ids.
    """
    if scope == AIScope.SYSTEM:
        return {"tenant_id": None, "user_id": None}
    if scope == AIScope.TENANT:
        return {"tenant_id": current_user.tenant_id, "user_id": None}
    return {"tenant_id": current_user.tenant_id, "user_id": current_user.user_id}


@router.post(
    "/providers/{preset_key}/setup",
    response_model=ProviderSetupResponse,
)
async def setup_provider(
    preset_key: str,
    body: ProviderSetupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """One-click provider setup from a curated preset (ai-features §15).

    Scope-aware: SYSTEM (system admins), TENANT (tenant admins) or USER
    (personal). Fetch-first validation: the key is verified against the
    vendor's real catalog before anything persists. Failures return the
    §15 error enum (``code`` + optional ``suspected_vendor``) — never a raw
    vendor dump.
    """
    check_scope_access(
        body.scope,
        current_user,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
    )

    try:
        outcome = await byok_setup.setup_provider_from_preset(
            db,
            preset_key,
            body.api_key,
            scope=body.scope,
            name=body.name,
            options=byok_setup.SetupOptions(
                curated_ids=body.options.curated_ids,
                bind_chat=body.options.bind_chat,
                bind_vision=body.options.bind_vision,
                bind_stt=body.options.bind_stt,
            ),
            **_scope_context(body.scope, current_user),
        )
    except byok_setup.UnknownPresetError:
        raise HTTPException(status_code=404, detail="Unknown provider preset")
    except byok_setup.SetupError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": exc.classified.code.value,
                "suspected_vendor": exc.classified.suspected_vendor,
                "message": exc.vendor_message,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    provider = await AIProviderService(db).get_provider(outcome.provider_id)
    return ProviderSetupResponse(
        provider=AIProviderResponse.model_validate(provider),
        catalog_count=outcome.catalog_count,
        curated_missed=outcome.curated_missed,
        assigned_chat_model=outcome.assigned_chat_model,
        assigned_vision_model=outcome.assigned_vision_model,
        assigned_stt_model=outcome.assigned_stt_model,
    )


@router.put(
    "/providers/{provider_id}/set-default",
    response_model=ProviderSetDefaultResponse,
)
async def set_provider_default(
    provider_id: UUID,
    body: ProviderSetDefaultRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Bind one of the provider's models to a task slot at the provider's
    own scope (SYSTEM/TENANT/USER, guarded by ``verify_provider_access``).

    Capability-guarded (the model must advertise the task's required
    capability) and cross-provider-safe (another provider's model id → 409).
    """
    service = AIProviderService(db)
    provider = await service.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    verify_provider_access(provider, current_user)

    try:
        model = await byok_setup.set_default_model(db, provider, body.model_name, body.task)
    except byok_setup.CrossProviderModelError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return ProviderSetDefaultResponse(
        task=body.task, model=AIModelResponse.model_validate(model)
    )
