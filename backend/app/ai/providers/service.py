import logging
from typing import Dict, Any, List, Optional, Tuple
from uuid import UUID
from sqlalchemy import select, update, delete, or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_core.language_models.chat_models import BaseChatModel

from app.ai.providers import factories
from app.ai.providers.enums import ProviderType, TaskType
from app.ai.providers.registry import get_llm_builder
from app.ai.providers.resolution import resolve_active_assignment
from app.ai.providers.workflows import build_workflows
from app.core.config import settings
from app.models.ai_provider_model import (
    AIProviderModel,
    AIModel,
    AITaskAssignment,
    AIScope,
)
from app.models.tenant_model import TenantModel
from app.models.system_setting import SystemSetting
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
    AIConfigSummary,
    TaskTypeAssignment,
)

logger = logging.getLogger(__name__)


class AIProviderService:
    """Service for managing AI providers, models and task assignments"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # Providers
    async def get_provider(self, provider_id: UUID) -> Optional[AIProviderModel]:
        """Get a specific provider by ID"""
        result = await self.db.execute(
            select(AIProviderModel).where(AIProviderModel.id == provider_id)
        )
        return result.scalars().first()

    async def get_providers(
        self,
        tenant_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        is_active: Optional[bool] = None,
        include_models: bool = False,
        scope: Optional[AIScope] = None,
    ) -> List[AIProviderModel]:
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
                    (AIProviderModel.scope == AIScope.USER)
                    & (AIProviderModel.user_id == user_id)
                )
            query = query.where(or_(*conditions))

        if is_active is not None:
            query = query.where(AIProviderModel.is_active == is_active)

        if include_models:
            from sqlalchemy.orm import selectinload

            query = query.options(selectinload(AIProviderModel.models))

        query = query.order_by(AIProviderModel.name)
        result = await self.db.execute(query)
        return result.scalars().unique().all()

    async def create_provider(self, provider_data: AIProviderCreate) -> AIProviderModel:
        """Create a new provider. The api_key is encrypted at rest before persistence."""
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
    ) -> Optional[AIProviderModel]:
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
            update(AIProviderModel)
            .where(AIProviderModel.id == provider_id)
            .values(**update_dict)
        )
        await self.db.commit()
        return await self.get_provider(provider_id)

    async def delete_provider(self, provider_id: UUID) -> bool:
        """Delete a provider"""
        await self.db.execute(
            delete(AIProviderModel).where(AIProviderModel.id == provider_id)
        )
        await self.db.commit()
        return True

    async def fetch_external_models(self, provider_id: UUID) -> List[Dict[str, Any]]:
        """Fetch available models from the provider's external API"""
        provider = await self.get_provider(provider_id)
        if not provider:
            raise ValueError("Provider not found")

        if provider.provider_type == "openai":
            import httpx

            headers = {"Authorization": f"Bearer {provider.get_api_key_plaintext()}"}
            url = f"{provider.api_base.rstrip('/')}/models"

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
                    raise RuntimeError(f"Failed to fetch external models: {str(e)}")

        return []

    # Models
    async def get_model(self, model_id: UUID) -> Optional[AIModel]:
        """Get a specific model by ID"""
        result = await self.db.execute(select(AIModel).where(AIModel.id == model_id))
        return result.scalars().first()

    async def get_models(
        self, provider_id: Optional[UUID] = None, is_active: Optional[bool] = None
    ) -> List[AIModel]:
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
        """Create a new model"""
        model = AIModel(**model_data.model_dump())
        self.db.add(model)
        await self.db.commit()
        await self.db.refresh(model)
        return model

    async def update_model(
        self, model_id: UUID, model_data: AIModelUpdate
    ) -> Optional[AIModel]:
        """Update a model"""
        update_dict = model_data.model_dump(exclude_unset=True)
        if not update_dict:
            return await self.get_model(model_id)

        await self.db.execute(
            update(AIModel).where(AIModel.id == model_id).values(**update_dict)
        )
        await self.db.commit()
        return await self.get_model(model_id)

    async def delete_model(self, model_id: UUID) -> bool:
        """Delete a model"""
        await self.db.execute(delete(AIModel).where(AIModel.id == model_id))
        await self.db.commit()
        return True

    # Task Assignments
    async def get_task_assignment(
        self, assignment_id: UUID
    ) -> Optional[AITaskAssignment]:
        """Get a specific task assignment"""
        result = await self.db.execute(
            select(AITaskAssignment).where(AITaskAssignment.id == assignment_id)
        )
        return result.scalars().first()

    async def get_task_assignments(
        self,
        tenant_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        task_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        scope: Optional[AIScope] = None,
    ) -> List[AITaskAssignment]:
        """Get all task assignments with optional filtering"""
        query = select(AITaskAssignment)

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
                    (AITaskAssignment.scope == AIScope.USER)
                    & (AITaskAssignment.user_id == user_id)
                )
            query = query.where(or_(*conditions))

        if task_type:
            query = query.where(AITaskAssignment.task_type == task_type)
        if is_active is not None:
            query = query.where(AITaskAssignment.is_active == is_active)

        query = query.order_by(
            AITaskAssignment.scope.desc(), AITaskAssignment.priority.desc()
        )
        result = await self.db.execute(query)
        return result.scalars().all()

    async def create_task_assignment(
        self, assignment_data: AITaskAssignmentCreate
    ) -> AITaskAssignment:
        """Create a new task assignment"""
        assignment = AITaskAssignment(**assignment_data.model_dump())
        self.db.add(assignment)
        await self.db.commit()
        await self.db.refresh(assignment)
        return assignment

    async def update_task_assignment(
        self, assignment_id: UUID, assignment_data: AITaskAssignmentUpdate
    ) -> Optional[AITaskAssignment]:
        """Update a task assignment"""
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

    async def delete_task_assignment(self, assignment_id: UUID) -> bool:
        """Delete a task assignment"""
        await self.db.execute(
            delete(AITaskAssignment).where(AITaskAssignment.id == assignment_id)
        )
        await self.db.commit()
        return True

    async def get_active_assignment_for_task(
        self,
        task_type: str,
        tenant_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
    ) -> Optional[AITaskAssignment]:
        """
        Get active assignment for a task type with fallback.
        Resolution Order:
        1. Specific Task (User -> Tenant -> Global)
        2. Default Task (User -> Tenant -> Global)

        Delegates to ``app.ai.providers.resolution.resolve_active_assignment``
        so the scope/priority/fallback rule is unit-testable without the
        service's CRUD surface.
        """
        return await resolve_active_assignment(self.db, task_type, tenant_id, user_id)

    async def get_config_summary(
        self,
        tenant_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        scope: Optional[AIScope] = None,
    ) -> AIConfigSummary:
        """Get a complete summary of AI configuration for the UI"""
        providers = await self.get_providers(
            tenant_id=tenant_id, user_id=user_id, scope=scope
        )
        
        # Load models for all visible providers
        provider_ids = {p.id for p in providers}
        models_res = await self.db.execute(
            select(AIModel).where(AIModel.provider_id.in_(list(provider_ids)) if provider_ids else text("FALSE"))
        )
        visible_models = models_res.scalars().all()

        assignments = await self.get_task_assignments(
            tenant_id=tenant_id, user_id=user_id, scope=scope
        )

        # Get task type assignments. The canonical list of task types now lives
        # in app.ai.providers.enums.TaskType (single source of truth) — previously
        # this was a hard-coded inline list that silently drifted from the enum.
        task_types = TaskType.all_values()
        task_assignments = {}

        for task_type in task_types:
            assignment = await self.get_active_assignment_for_task(
                task_type, tenant_id, user_id
            )
            if assignment:
                provider = (
                    await self.get_provider(assignment.provider_id)
                    if assignment.provider_id
                    else None
                )
                model = (
                    await self.get_model(assignment.model_id)
                    if assignment.model_id
                    else None
                )
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
            task_assignments=[
                AITaskAssignmentResponse.model_validate(a) for a in assignments
            ],
            default=task_assignments.get("default"),
            ocr=task_assignments.get("ocr"),
            nlp=task_assignments.get("nlp"),
            medication_interaction=task_assignments.get("medication_interaction"),
            anomaly_detection=task_assignments.get("anomaly_detection"),
            fill_biomarker_form=task_assignments.get("fill_biomarker_form"),
            fill_medication_form=task_assignments.get("fill_medication_form"),
            magic_fill_examination=task_assignments.get("magic_fill_examination"),
            define_biomarker=task_assignments.get("define_biomarker"),
            define_medication=task_assignments.get("define_medication"),
            suggest_category_icon=task_assignments.get("suggest_category_icon"),
            generate_category_icon=task_assignments.get("generate_category_icon"),
            chat=task_assignments.get("chat"),
            workflows=workflows,
            ai_agent_max_iterations=max_iterations,
        )

    async def update_ai_settings(
        self,
        config_data: Any,  # AIConfigUpdate
        tenant_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        current_user_id: Optional[UUID] = None,
    ) -> bool:
        """Update AI-specific settings for a tenant or user"""
        if not tenant_id and not user_id:
            # Update System-wide settings
            if config_data.ai_agent_max_iterations is not None:
                await SystemSetting.set_value(
                    self.db, 
                    "ai_agent_max_iterations", 
                    config_data.ai_agent_max_iterations,
                    user_id=current_user_id
                )
            return True

        if tenant_id:
            result = await self.db.execute(
                select(TenantModel).where(TenantModel.id == tenant_id)
            )
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
        tenant_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
    ) -> Tuple[Optional[AIProviderModel], Optional[AIModel]]:
        """Resolve the active provider and model for a task type"""
        assignment = await self.get_active_assignment_for_task(
            task_type, tenant_id, user_id
        )

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
        tenant_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
    ) -> BaseChatModel:
        """Get an initialized LangChain chat model for a specific task"""
        assignment = await self.get_active_assignment_for_task(
            task_type, tenant_id, user_id
        )

        provider = None
        model = None

        if assignment:
            if assignment.provider_id:
                provider = await self.get_provider(assignment.provider_id)
            if assignment.model_id:
                model = await self.get_model(assignment.model_id)

            logger.info(
                f"AI Resolution [{task_type}]: Using {assignment.scope} configuration. "
                f"Provider: {provider.name if provider else 'None'}, "
                f"Model: {model.model_name if model else 'Default'}"
            )

            if provider:
                # Resolve the LLM builder for this provider_type via the registry.
                # Unknown/unwired provider types fall back to the OpenAI-compatible
                # builder (with a logged warning) instead of 500ing.
                builder = get_llm_builder(provider.provider_type)
                return builder(
                    api_key=provider.get_api_key_plaintext(),
                    base_url=provider.api_base or "https://api.openai.com/v1",
                    model_name=model.model_name if model else "gpt-4o-mini",
                    temperature=model.temperature if model else 0.7,
                    max_tokens=model.max_tokens if model else 65536,
                )

        # Fallback to environment variables
        logger.warning(
            f"AI Resolution [{task_type}]: No DB assignment found. Falling back to ENV settings. "
            f"Model: {settings.OPENAI_MODEL or 'gpt-4o-mini'}"
        )
        return factories.build_openai(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_API_BASE or "https://api.openai.com/v1",
            model_name=settings.OPENAI_MODEL or "gpt-4o-mini",
            temperature=0.7,
            max_tokens=settings.OPENAI_MAX_TOKENS or 4096,
        )

    async def get_ocr_processor(
        self, tenant_id: Optional[UUID] = None, user_id: Optional[UUID] = None
    ):
        """Get a configured OCR processor for the tenant/user"""
        from app.ai.processors.ocr import get_ocr_processor

        assignment = await self.get_active_assignment_for_task(
            "ocr", tenant_id, user_id
        )

        provider = None
        model = None

        if assignment:
            if assignment.provider_id:
                provider = await self.get_provider(assignment.provider_id)
            if assignment.model_id:
                model = await self.get_model(assignment.model_id)

            logger.info(
                f"AI Resolution [ocr]: Using {assignment.scope} configuration. "
                f"Provider: {provider.name if provider else 'None'}, "
                f"Model: {model.model_name if model else 'Default'}"
            )

            if provider:
                llm = None
                # OCR is LLM-backed only when the provider is OpenAI-compatible
                # today (vision models). Tesseract/etc. skip the LLM entirely.
                if provider.provider_type == ProviderType.OPENAI.value:
                    llm = factories.build_openai(
                        api_key=provider.get_api_key_plaintext(),
                        base_url=provider.api_base or "https://api.openai.com/v1",
                        model_name=model.model_name if model else "gpt-4o",
                        temperature=model.temperature if model else 0.0,
                        max_tokens=model.max_tokens if model else 65536,
                    )

                return get_ocr_processor(
                    provider=provider.provider_type,
                    api_key=provider.get_api_key_plaintext(),
                    api_base=provider.api_base,
                    model=model.model_name if model else "gpt-4o",
                    max_tokens=model.max_tokens if model else 65536,
                    temperature=model.temperature if model else 0.0,
                    llm=llm,
                )

        # Fallback to settings
        logger.warning(
            f"AI Resolution [ocr]: No DB assignment found. Falling back to ENV settings. Provider: {settings.OCR_PROVIDER}"
        )
        return get_ocr_processor(
            provider=settings.OCR_PROVIDER,
            api_key=settings.OPENAI_API_KEY
            if settings.OCR_PROVIDER == ProviderType.OPENAI.value
            else None,
        )

    async def get_nlp_extractor(
        self, tenant_id: Optional[UUID] = None, user_id: Optional[UUID] = None
    ):
        """Get a configured NLP extractor for the tenant/user"""
        from app.ai.processors.nlp import get_nlp_extractor

        assignment = await self.get_active_assignment_for_task(
            "nlp", tenant_id, user_id
        )

        provider = None
        model = None

        if assignment:
            if assignment.provider_id:
                provider = await self.get_provider(assignment.provider_id)
            if assignment.model_id:
                model = await self.get_model(assignment.model_id)

            logger.info(
                f"AI Resolution [nlp]: Using {assignment.scope} configuration. "
                f"Provider: {provider.name if provider else 'None'}, "
                f"Model: {model.model_name if model else 'Default'}"
            )

            if provider:
                llm = None
                # NLP structured extraction is LLM-backed only for OpenAI-
                # compatible providers today; spaCy is the rule-based fallback.
                if provider.provider_type == ProviderType.OPENAI.value:
                    llm = factories.build_openai(
                        api_key=provider.get_api_key_plaintext(),
                        base_url=provider.api_base or "https://api.openai.com/v1",
                        model_name=model.model_name if model else "gpt-4o-mini",
                        temperature=model.temperature if model else 0.7,
                        max_tokens=model.max_tokens if model else 65536,
                    )

                return get_nlp_extractor(
                    provider=provider.provider_type,
                    api_key=provider.get_api_key_plaintext(),
                    api_base=provider.api_base,
                    model=model.model_name if model else "gpt-4o-mini",
                    temperature=model.temperature if model else 0.7,
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
                temperature=0.7,
            )
            
        logger.warning(
            "AI Resolution [nlp]: No DB assignment found and no API keys in environment. Falling back to standard SPACY extractor."
        )
        return get_nlp_extractor(provider=ProviderType.SPACY.value)
