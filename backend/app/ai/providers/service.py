# ruff: noqa: B904,E501 -- long immutable strings; reflow when touched
import logging
from typing import Any
from uuid import UUID

from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai import chat_models
from app.ai.call_audit import attach_call_audit, context_for_task
from app.ai.providers.enums import ProviderType, TaskType
from app.ai.providers.registry import ensure_wired_provider_type, get_llm_builder
from app.ai.providers.resolution import resolve_task
from app.ai.providers.task_defs import ensure_task_def, task_def_id
from app.ai.providers.task_registry import get_task_def
from app.ai.providers.workflows import build_workflows
from app.ai.schemas.config import (
    AIConfigSummary,
    AIModelCreate,
    AIModelResponse,
    AIModelUpdate,
    AIProviderCreate,
    AIProviderResponse,
    AIProviderUpdate,
    AITaskAssignmentCreate,
    AITaskAssignmentResponse,
    AITaskAssignmentUpdate,
    TaskTypeAssignment,
)
from app.core.config import settings
from app.models.ai_provider_model import (
    AIModel,
    AIProviderModel,
    AIScope,
    AITaskAssignment,
)
from app.models.system_setting import SystemSetting
from app.models.tenant_model import TenantModel

logger = logging.getLogger(__name__)


def guard_api_base(api_base: str, *, allow_private: bool = False) -> None:
    """Audit 2026-09-11 S-2: validate a provider ``api_base`` for SSRF safety.

    Blocks non-http(s) schemes, unresolvable URLs, and hosts resolving to
    loopback/private/link-local/metadata ranges — including hostnames whose
    DNS resolves internally (the gap the endpoint-level literal-IP check
    couldn't close). Explicitly local providers (``is_local=True`` or a
    local preset) pass ``allow_private=True`` so a self-hosted Ollama/vLLM
    on localhost works in production; the env knobs
    (``INTEGRATION_ALLOWED_HOSTS`` / ``INTEGRATION_BLOCK_PRIVATE_RANGES``)
    and ``DEBUG`` keep working too.

    Raises ``ValueError`` with a client-facing message on violation.
    """
    from app.extensions import polite_http
    from app.extensions.runtime.http import net_guard_config

    if not api_base:
        return
    try:
        polite_http.check_url(
            api_base,
            config=net_guard_config(allow_private=settings.DEBUG or allow_private),
        )
    except polite_http.PoliteHttpError as exc:
        raise ValueError(
            "api_base must be an http(s) URL that does not resolve to an "
            "internal, private, or loopback address."
        ) from exc


def _model_reasoning_effort(model: AIModel | None) -> str | None:
    """Reasoning-effort model setting (``settings.reasoning_effort``), if any.

    Stored on the model row's ``settings`` JSONB (e.g. ``"none"`` for a
    reasoning model that must serve tool-calling chat). ``None`` = provider
    default.
    """
    settings_dict = getattr(model, "settings", None) or {}
    value = settings_dict.get("reasoning_effort") if isinstance(settings_dict, dict) else None
    return str(value) if value else None


class AIProviderService:
    """Service for managing AI providers, models and task assignments"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # Providers
    async def get_provider(self, provider_id: UUID) -> AIProviderModel | None:
        """Get a specific provider by ID"""
        result = await self.db.execute(
            select(AIProviderModel).where(AIProviderModel.id == provider_id)
        )
        return result.scalars().first()

    async def get_providers(
        self,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        is_active: bool | None = None,
        include_models: bool = False,
        scope: AIScope | None = None,
    ) -> list[AIProviderModel]:
        """Get providers with optional filtering"""
        query = select(AIProviderModel)

        if scope:
            query = query.where(AIProviderModel.scope == scope)
            if scope == AIScope.TENANT and tenant_id:
                query = query.where(AIProviderModel.tenant_id == tenant_id)
            elif scope == AIScope.USER and user_id:
                query = query.where(AIProviderModel.user_id == user_id)
        else:
            # Default: show what the user has access to (Personal + Org + System)
            conditions = [AIProviderModel.scope == AIScope.SYSTEM]
            if tenant_id:
                conditions.append(
                    (AIProviderModel.scope == AIScope.TENANT)
                    & (AIProviderModel.tenant_id == tenant_id)
                )
            if user_id:
                conditions.append(
                    (AIProviderModel.scope == AIScope.USER) & (AIProviderModel.user_id == user_id)
                )
            query = query.where(or_(*conditions))

        if is_active is not None:
            query = query.where(AIProviderModel.is_active == is_active)

        if include_models:
            query = query.options(selectinload(AIProviderModel.models))

        query = query.order_by(AIProviderModel.name)
        result = await self.db.execute(query)
        return result.scalars().unique().all()

    async def create_provider(self, provider_data: AIProviderCreate) -> AIProviderModel:
        """Create a new provider. The api_key is encrypted at rest before persistence."""
        ensure_wired_provider_type(provider_data.provider_type)
        guard_api_base(provider_data.api_base or "", allow_private=provider_data.is_local)
        from app.core.encryption import encrypt_secret

        payload = provider_data.model_dump()
        if payload.get("api_key"):
            payload["api_key"] = encrypt_secret(payload["api_key"])
        provider = AIProviderModel(**payload)
        self.db.add(provider)
        await self.db.commit()
        await self.db.refresh(provider)
        return provider

    async def update_provider(
        self, provider_id: UUID, provider_data: AIProviderUpdate
    ) -> AIProviderModel | None:
        """Update a provider.

        Encrypts any newly-provided api_key before persistence.
        ``api_key`` semantics on update:

          - absent from the patch (``exclude_unset``) → preserve existing key
          - ``None`` or ``""`` → explicitly clear (set to NULL)
          - ``"***xxxx"`` (masked form returned by the response schema) →
            preserve existing key (the UI re-sent the masked value rather
            than the real key)
          - any other string → encrypt and store
        """
        from app.core.encryption import encrypt_secret, looks_masked

        update_dict = provider_data.model_dump(exclude_unset=True)

        if "provider_type" in update_dict:
            ensure_wired_provider_type(update_dict["provider_type"])

        if "api_base" in update_dict:
            allow_local = update_dict.get("is_local")
            if allow_local is None:
                allow_local = await self.db.scalar(
                    select(AIProviderModel.is_local).where(AIProviderModel.id == provider_id)
                )
            guard_api_base(update_dict["api_base"] or "", allow_private=bool(allow_local))

        if "api_key" in update_dict:
            incoming = update_dict["api_key"]
            if looks_masked(incoming):
                # UI re-sent the masked form — preserve the existing key.
                update_dict.pop("api_key")
            elif incoming is None or incoming == "":
                # Explicit clear — pass through to the UPDATE as NULL.
                update_dict["api_key"] = None
            else:
                update_dict["api_key"] = encrypt_secret(incoming)

        if not update_dict:
            return await self.get_provider(provider_id)

        await self.db.execute(
            update(AIProviderModel).where(AIProviderModel.id == provider_id).values(**update_dict)
        )
        await self.db.commit()
        return await self.get_provider(provider_id)

    async def delete_provider(self, provider_id: UUID) -> bool:
        """Delete a provider"""
        await self.db.execute(delete(AIProviderModel).where(AIProviderModel.id == provider_id))
        await self.db.commit()
        return True

    async def fetch_external_models(self, provider_id: UUID) -> list[dict[str, Any]]:
        """Fetch available models from the provider's external API"""
        provider = await self.get_provider(provider_id)
        if not provider:
            raise ValueError("Provider not found")

        if provider.provider_type == "openai":
            import httpx

            url = f"{provider.api_base.rstrip('/')}/models"
            guard_api_base(url, allow_private=provider.is_local)
            headers = {"Authorization": f"Bearer {provider.get_api_key_plaintext()}"}

            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(url, headers=headers, timeout=10.0)
                    response.raise_for_status()
                    data = response.json()

                    # OpenAI returns a list of model objects in the 'data' field
                    models = []
                    for model in data.get("data", []):
                        models.append(
                            {
                                "id": model.get("id"),
                                "name": model.get("id"),
                                "created": model.get("created"),
                                "owned_by": model.get("owned_by"),
                            }
                        )

                    # Sort models by name
                    models.sort(key=lambda x: x["name"])
                    return models
                except Exception as e:
                    logger.error(f"Failed to fetch models from {url}: {e}")
                    raise RuntimeError(f"Failed to fetch external models: {e!s}")

        return []

    # Models
    async def get_model(self, model_id: UUID) -> AIModel | None:
        """Get a specific model by ID"""
        result = await self.db.execute(select(AIModel).where(AIModel.id == model_id))
        return result.scalars().first()

    async def get_models(
        self, provider_id: UUID | None = None, is_active: bool | None = None
    ) -> list[AIModel]:
        """Get models for a provider"""
        query = select(AIModel)
        if provider_id:
            query = query.where(AIModel.provider_id == provider_id)
        if is_active is not None:
            query = query.where(AIModel.is_active == is_active)

        query = query.order_by(AIModel.name)
        result = await self.db.execute(query)
        return result.scalars().all()

    async def create_model(self, model_data: AIModelCreate) -> AIModel:
        """Create a new model under a wired provider."""
        from app.core.errors import NotFoundError

        provider = await self.get_provider(model_data.provider_id)
        if provider is None:
            raise NotFoundError("Provider not found")
        ensure_wired_provider_type(provider.provider_type)

        model = AIModel(**model_data.model_dump())
        self.db.add(model)
        await self.db.commit()
        await self.db.refresh(model)
        return model

    async def update_model(self, model_id: UUID, model_data: AIModelUpdate) -> AIModel | None:
        """Update a model (refused when its provider type is unwired)."""
        update_dict = model_data.model_dump(exclude_unset=True)
        if not update_dict:
            return await self.get_model(model_id)

        model = await self.get_model(model_id)
        if model is not None:
            provider = await self.get_provider(model.provider_id)
            if provider is not None:
                ensure_wired_provider_type(provider.provider_type)

        await self.db.execute(update(AIModel).where(AIModel.id == model_id).values(**update_dict))
        await self.db.commit()
        return await self.get_model(model_id)

    async def delete_model(self, model_id: UUID) -> bool:
        """Delete a model"""
        await self.db.execute(delete(AIModel).where(AIModel.id == model_id))
        await self.db.commit()
        return True

    # Task Assignments
    async def get_task_assignment(self, assignment_id: UUID) -> AITaskAssignment | None:
        """Get a specific task assignment"""
        result = await self.db.execute(
            select(AITaskAssignment).where(AITaskAssignment.id == assignment_id)
        )
        return result.scalars().first()

    async def get_task_assignments(
        self,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        task_type: str | None = None,
        is_active: bool | None = True,
        scope: AIScope | None = None,
    ) -> list[AITaskAssignment]:
        """Get task assignments with optional filtering.

        ``is_active`` is a *derived* filter ("active" = ``revoked_at IS NULL``,
        the same definition the DB unique index uses); it is never a stored
        column.
        """
        query = select(AITaskAssignment).options(selectinload(AITaskAssignment.task_def))

        if scope:
            query = query.where(AITaskAssignment.scope == scope)
            if scope == AIScope.TENANT and tenant_id:
                query = query.where(AITaskAssignment.tenant_id == tenant_id)
            elif scope == AIScope.USER and user_id:
                query = query.where(AITaskAssignment.user_id == user_id)
        else:
            conditions = [AITaskAssignment.scope == AIScope.SYSTEM]
            if tenant_id:
                conditions.append(
                    (AITaskAssignment.scope == AIScope.TENANT)
                    & (AITaskAssignment.tenant_id == tenant_id)
                )
            if user_id:
                conditions.append(
                    (AITaskAssignment.scope == AIScope.USER) & (AITaskAssignment.user_id == user_id)
                )
            query = query.where(or_(*conditions))

        if task_type:
            definition = get_task_def(task_type)
            if definition is None:
                return []
            query = query.where(AITaskAssignment.task_type_id == task_def_id(definition.id))
        if is_active is not None:
            query = query.where(
                AITaskAssignment.revoked_at.is_(None)
                if is_active
                else AITaskAssignment.revoked_at.is_not(None)
            )

        query = query.order_by(AITaskAssignment.created_at, AITaskAssignment.id)
        result = await self.db.execute(query)
        return list(result.scalars().unique().all())

    async def create_task_assignment(
        self, assignment_data: AITaskAssignmentCreate
    ) -> AITaskAssignment:
        """Create a new active task assignment (the DB enforces single-slot)."""
        definition = get_task_def(assignment_data.task_type)
        if definition is None:
            raise ValueError(f"unknown task type '{assignment_data.task_type}'")
        await ensure_task_def(self.db, definition.id)
        payload = assignment_data.model_dump()
        payload.pop("task_type", None)
        assignment = AITaskAssignment(
            **payload,
            task_type_id=task_def_id(definition.id),
            revoked_at=None,
        )
        self.db.add(assignment)
        await self.db.commit()
        await self.db.refresh(assignment)
        return assignment

    async def update_task_assignment(
        self, assignment_id: UUID, assignment_data: AITaskAssignmentUpdate
    ) -> AITaskAssignment | None:
        """Update a task assignment's binding (task identity is immutable)."""
        update_dict = assignment_data.model_dump(exclude_unset=True)
        if not update_dict:
            return await self.get_task_assignment(assignment_id)

        await self.db.execute(
            update(AITaskAssignment)
            .where(AITaskAssignment.id == assignment_id)
            .values(**update_dict)
        )
        await self.db.commit()
        return await self.get_task_assignment(assignment_id)

    async def revoke_task_assignment(self, assignment_id: UUID) -> bool:
        """Revoke an assignment — rows are revoked, never deleted."""
        await self.db.execute(
            update(AITaskAssignment)
            .where(AITaskAssignment.id == assignment_id, AITaskAssignment.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
        await self.db.commit()
        return True

    async def get_active_assignment_for_task(
        self,
        task_type: str,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> AITaskAssignment | None:
        """The winning assignment row for a task (see providers.resolution).

        Precedence (specialist primary → specialist fallback → capability
        default) exists once, in ``app.ai.providers.resolution.resolve_task``;
        this accessor only returns the row for API/read consumers.
        """
        resolved = await resolve_task(self.db, str(task_type), tenant_id, user_id)
        return resolved.assignment if resolved is not None else None

    async def get_config_summary(
        self,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        scope: AIScope | None = None,
    ) -> AIConfigSummary:
        """Get a complete summary of AI configuration for the UI"""
        providers = await self.get_providers(tenant_id=tenant_id, user_id=user_id, scope=scope)

        # Load models for all visible providers
        provider_ids = {p.id for p in providers}
        models_res = await self.db.execute(
            select(AIModel).where(
                AIModel.provider_id.in_(list(provider_ids)) if provider_ids else text("FALSE")
            )
        )
        visible_models = models_res.scalars().all()

        assignments = await self.get_task_assignments(
            tenant_id=tenant_id, user_id=user_id, scope=scope
        )

        # Resolve every registry task. The task list is the one registry —
        # there is no per-task field list to drift (plan 4 W1; this is the
        # structure that used to hide define_anatomy_graph).
        task_assignments: dict[str, TaskTypeAssignment] = {}

        for task_type in TaskType.all_values():
            assignment = await self.get_active_assignment_for_task(task_type, tenant_id, user_id)
            if assignment:
                provider = (
                    await self.get_provider(assignment.provider_id)
                    if assignment.provider_id
                    else None
                )
                model = await self.get_model(assignment.model_id) if assignment.model_id else None
                task_assignments[task_type] = TaskTypeAssignment(
                    task_type=task_type,
                    provider=AIProviderResponse.model_validate(provider) if provider else None,
                    model=AIModelResponse.model_validate(model) if model else None,
                    assignment_id=assignment.id,
                )

        # Define abstract workflows for the frontend (composition table lives
        # in app.ai.providers.workflows so it can be unit-tested standalone).
        workflows = build_workflows(task_assignments)

        # Get max iterations
        max_iterations = settings.AI_AGENT_MAX_ITERATIONS

        # Check system DB setting first if looking at system scope or if no tenant specified
        system_db_max = await SystemSetting.get_value(self.db, "ai_agent_max_iterations")
        if system_db_max is not None:
            max_iterations = int(system_db_max)

        if tenant_id:
            tenant_res = await self.db.execute(
                select(TenantModel.settings).where(TenantModel.id == tenant_id)
            )
            tenant_settings = tenant_res.scalar_one_or_none()
            if tenant_settings and "ai_agent_max_iterations" in tenant_settings:
                max_iterations = int(tenant_settings["ai_agent_max_iterations"])

        return AIConfigSummary(
            providers=[AIProviderResponse.model_validate(p) for p in providers],
            models=[AIModelResponse.model_validate(m) for m in visible_models],
            task_assignments=[AITaskAssignmentResponse.model_validate(a) for a in assignments],
            tasks=task_assignments,
            workflows=workflows,
            ai_agent_max_iterations=max_iterations,
        )

    async def update_ai_settings(
        self,
        config_data: Any,  # AIConfigUpdate
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        current_user_id: UUID | None = None,
    ) -> bool:
        """Update AI-specific settings for a tenant or user"""
        if not tenant_id and not user_id:
            # Update System-wide settings
            if config_data.ai_agent_max_iterations is not None:
                await SystemSetting.set_value(
                    self.db,
                    "ai_agent_max_iterations",
                    config_data.ai_agent_max_iterations,
                    user_id=current_user_id,
                )
            return True

        if tenant_id:
            result = await self.db.execute(select(TenantModel).where(TenantModel.id == tenant_id))
            tenant = result.scalars().first()
            if not tenant:
                return False

            if tenant.settings is None:
                tenant.settings = {}

            # Merge settings
            if config_data.ai_agent_max_iterations is not None:
                tenant.settings["ai_agent_max_iterations"] = config_data.ai_agent_max_iterations

            # Flag for SQLAlchemy to detect change in JSONB
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(tenant, "settings")

            await self.db.commit()
            return True

        return False

    async def _resolve_config(
        self,
        task_type: str,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> tuple[AIProviderModel | None, AIModel | None]:
        """Resolve the active provider and model for a task type"""
        assignment = await self.get_active_assignment_for_task(task_type, tenant_id, user_id)

        provider = None
        model = None

        if assignment:
            if assignment.provider_id:
                provider = await self.get_provider(assignment.provider_id)
            if assignment.model_id:
                model = await self.get_model(assignment.model_id)

        return provider, model

    async def get_llm(
        self,
        task_type: str,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        *,
        patient_id: UUID | None = None,
        trace_id: str | None = None,
    ) -> BaseChatModel:
        """Get an initialized LangChain chat model for a specific task.

        Resolution goes through the ONE precedence implementation
        (``app.ai.providers.resolution.resolve_task``); the returned model
        carries the per-call audit handler (one ``ai_call_logs`` row per model
        call).
        """
        if TaskType.is_decision(task_type) or TaskType.is_decision_default(task_type):
            raise ValueError(
                f"task '{task_type}' is a decision slot — it resolves through "
                "the decide funnel, never an LLM builder (D4/D13)"
            )
        resolved = await resolve_task(self.db, str(task_type), tenant_id, user_id)

        provider = resolved.provider if resolved is not None else None
        model = resolved.model if resolved is not None else None

        if resolved is not None:
            logger.info(
                "AI Resolution [%s]: %s hop -> %s. Provider: %s, Model: %s",
                task_type,
                resolved.hop.value,
                resolved.task_id,
                provider.name if provider else "None",
                model.model_name if model else "Default",
            )

            if provider:
                if provider.provider_type == ProviderType.DECISION.value:
                    # D13: routing a decision-only provider at chat is a
                    # config error the resolver rejects — never fall through
                    # to the OpenAI-compatible builder.
                    raise ValueError(
                        f"provider '{provider.name}' is decision-only — assign a "
                        "chat-capable provider for this task"
                    )
                # Resolve the LLM builder for this provider_type via the registry.
                # Unknown/unwired provider types fall back to the OpenAI-compatible
                # builder (with a logged warning) instead of 500ing.
                builder = get_llm_builder(provider.provider_type)
                return attach_call_audit(
                    builder(
                        api_key=provider.get_api_key_plaintext(),
                        base_url=provider.api_base or "https://api.openai.com/v1",
                        model_name=model.model_name if model else "gpt-4o-mini",
                        temperature=model.temperature if model else None,
                        max_tokens=model.max_tokens if model else 65536,
                        reasoning_effort=_model_reasoning_effort(model),
                        chat_transport=model.chat_transport if model else None,
                    ),
                    context_for_task(
                        str(task_type),
                        provider=provider,
                        model=model,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        patient_id=patient_id,
                        trace_id=trace_id,
                    ),
                )

        # Fallback to environment variables
        logger.warning(
            f"AI Resolution [{task_type}]: No DB assignment found. Falling back to ENV settings. "
            f"Model: {settings.OPENAI_MODEL or 'gpt-4o-mini'}"
        )
        return attach_call_audit(
            chat_models.build_openai(
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_API_BASE or "https://api.openai.com/v1",
                model_name=settings.OPENAI_MODEL or "gpt-4o-mini",
                # Unset: the provider default applies (ADR-0038 addendum).
                temperature=None,
                max_tokens=settings.OPENAI_MAX_TOKENS or 4096,
                chat_transport=None,
            ),
            context_for_task(
                str(task_type),
                tenant_id=tenant_id,
                user_id=user_id,
                patient_id=patient_id,
                trace_id=trace_id,
            ),
        )

    async def get_ocr_processor(self, tenant_id: UUID | None = None, user_id: UUID | None = None):
        """Get a configured OCR processor for the tenant/user"""
        from app.ai.processors.ocr import get_ocr_processor

        resolved = await resolve_task(self.db, TaskType.OCR.value, tenant_id, user_id)
        provider = resolved.provider if resolved is not None else None
        model = resolved.model if resolved is not None else None

        if resolved is not None:
            logger.info(
                "AI Resolution [ocr]: %s hop -> %s. Provider: %s, Model: %s",
                resolved.hop.value,
                resolved.task_id,
                provider.name if provider else "None",
                model.model_name if model else "Default",
            )

            if provider:
                llm = None
                # OCR is LLM-backed only when the provider is OpenAI-compatible
                # today (vision models). Tesseract/etc. skip the LLM entirely.
                if provider.provider_type == ProviderType.OPENAI.value:
                    llm = attach_call_audit(
                        chat_models.build_openai(
                            api_key=provider.get_api_key_plaintext(),
                            base_url=provider.api_base or "https://api.openai.com/v1",
                            model_name=model.model_name if model else "gpt-4o",
                            temperature=model.temperature if model else None,
                            max_tokens=model.max_tokens if model else 65536,
                            reasoning_effort=_model_reasoning_effort(model),
                            chat_transport=model.chat_transport if model else None,
                        ),
                        context_for_task(
                            TaskType.OCR.value,
                            provider=provider,
                            model=model,
                            tenant_id=tenant_id,
                            user_id=user_id,
                        ),
                    )

                return get_ocr_processor(
                    provider=provider.provider_type,
                    api_key=provider.get_api_key_plaintext(),
                    api_base=provider.api_base,
                    model=model.model_name if model else "gpt-4o",
                    max_tokens=model.max_tokens if model else 65536,
                    temperature=model.temperature if model else None,
                    llm=llm,
                )

        # Fallback to settings
        logger.warning(
            f"AI Resolution [ocr]: No DB assignment found. Falling back to ENV settings. Provider: {settings.OCR_PROVIDER}"
        )
        return get_ocr_processor(
            provider=settings.OCR_PROVIDER,
            api_key=settings.OPENAI_API_KEY
            if ProviderType.OPENAI.value == settings.OCR_PROVIDER
            else None,
        )

    async def get_nlp_extractor(self, tenant_id: UUID | None = None, user_id: UUID | None = None):
        """Get a configured NLP extractor for the tenant/user"""
        from app.ai.processors.nlp import get_nlp_extractor

        resolved = await resolve_task(self.db, TaskType.NLP.value, tenant_id, user_id)
        provider = resolved.provider if resolved is not None else None
        model = resolved.model if resolved is not None else None

        if resolved is not None:
            logger.info(
                "AI Resolution [nlp]: %s hop -> %s. Provider: %s, Model: %s",
                resolved.hop.value,
                resolved.task_id,
                provider.name if provider else "None",
                model.model_name if model else "Default",
            )

            if provider:
                llm = None
                # NLP structured extraction is LLM-backed only for OpenAI-
                # compatible providers today; spaCy is the rule-based fallback.
                if provider.provider_type == ProviderType.OPENAI.value:
                    llm = attach_call_audit(
                        chat_models.build_openai(
                            api_key=provider.get_api_key_plaintext(),
                            base_url=provider.api_base or "https://api.openai.com/v1",
                            model_name=model.model_name if model else "gpt-4o-mini",
                            temperature=model.temperature if model else None,
                            max_tokens=model.max_tokens if model else 65536,
                            reasoning_effort=_model_reasoning_effort(model),
                            chat_transport=model.chat_transport if model else None,
                        ),
                        context_for_task(
                            TaskType.NLP.value,
                            provider=provider,
                            model=model,
                            tenant_id=tenant_id,
                            user_id=user_id,
                        ),
                    )

                return get_nlp_extractor(
                    provider=provider.provider_type,
                    api_key=provider.get_api_key_plaintext(),
                    api_base=provider.api_base,
                    model=model.model_name if model else "gpt-4o-mini",
                    temperature=model.temperature if model else None,
                    llm=llm,
                )

        # Fallback to defaults
        if settings.OPENAI_API_KEY:
            logger.warning(
                "AI Resolution [nlp]: No DB assignment found, but OPENAI_API_KEY is present in environment. Falling back to OpenAI extractor."
            )
            return get_nlp_extractor(
                provider=ProviderType.OPENAI.value,
                api_key=settings.OPENAI_API_KEY,
                api_base=settings.OPENAI_API_BASE or "https://api.openai.com/v1",
                model=settings.OPENAI_MODEL or "gpt-4o-mini",
                temperature=None,
            )

        logger.warning(
            "AI Resolution [nlp]: No DB assignment found and no API keys in environment. Falling back to standard SPACY extractor."
        )
        return get_nlp_extractor(provider=ProviderType.SPACY.value)

    async def get_stt_target(self, tenant_id: UUID | None = None, user_id: UUID | None = None):
        """Resolve the speech-to-text target (provider + model) for the
        ``transcription`` task.

        Returns an :class:`app.ai.assistance.stt.STTTarget` describing the
        api_key / api_base / model_name to call. Validates the resolved model
        advertises the ``stt`` capability (rejects a misconfigured
        chat-only assignment early). Falls back to the ``OPENAI_*`` env STT
        model when no DB assignment exists.
        """
        from app.ai.assistance.stt import STTTarget, _resolve_stt_target

        resolved = await resolve_task(self.db, TaskType.TRANSCRIPTION.value, tenant_id, user_id)
        provider = resolved.provider if resolved is not None else None
        model = resolved.model if resolved is not None else None
        if resolved is not None and provider is not None:
            logger.info(
                "AI Resolution [transcription]: %s hop -> %s. Provider: %s, Model: %s",
                resolved.hop.value,
                resolved.task_id,
                provider.name,
                model.model_name if model else "Default",
            )
            return _resolve_stt_target(provider, model)

        # Fallback to env (OPENAI_API_KEY + OPENAI_STT_MODEL).
        logger.warning(
            "AI Resolution [transcription]: No DB assignment found. Falling back to ENV settings."
        )
        return STTTarget(
            api_key=settings.OPENAI_API_KEY,
            api_base=(settings.OPENAI_API_BASE or "https://api.openai.com/v1"),
            model_name=settings.OPENAI_STT_MODEL,
        )
