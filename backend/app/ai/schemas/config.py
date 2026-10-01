from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import AIModelCapability, AIScope


def _mask_api_key_for_response(value: str | None) -> str | None:
    """Never return a plaintext api_key in any response shape.

    Accepts either the encrypted at-rest form or legacy plaintext and
    returns a ``***<last4>`` mask. Returns None if the input is None/empty.
    """
    if value is None or value == "":
        return None
    from app.core.encryption import mask_secret

    return mask_secret(value)


def _compute_has_api_key(value: str | None) -> bool:
    return bool(value) and value != ""


class AIProviderCreate(BaseModel):
    """Schema for creating a new AI provider"""

    name: str = Field(..., min_length=1, max_length=100, description="Display name of provider")
    scope: AIScope = Field(default=AIScope.SYSTEM, description="Scope of the provider")
    provider_type: str = Field(
        ..., min_length=1, max_length=50, description="Type: openai, tesseract"
    )
    api_base: str = Field(..., min_length=1, max_length=500, description="API base URL")
    api_key: str | None = Field(None, max_length=500, description="API key")
    is_active: bool = Field(default=True, description="Enable/disable provider")
    settings: dict[str, Any] | None = Field(
        default_factory=dict, description="Provider-specific settings"
    )
    is_local: bool = Field(default=False, description="Whether the provider is run locally")
    company_name: str | None = Field(None, max_length=200, description="Company Name")
    company_website: str | None = Field(None, max_length=500, description="Company Website")
    company_country: str | None = Field(
        None, pattern=r"^[A-Z]{2}$", description="Company Country (ISO 3166-1 alpha-2)"
    )
    tenant_id: UUID | None = Field(None, description="Tenant ID (nullable for global providers)")
    user_id: UUID | None = Field(None, description="User ID (nullable for global/tenant providers)")

    model_config = ConfigDict(from_attributes=True)


class AIProviderUpdate(BaseModel):
    """Schema for updating an AI provider"""

    name: str | None = Field(None, min_length=1, max_length=100)
    provider_type: str | None = Field(None, min_length=1, max_length=50)
    api_base: str | None = Field(None, min_length=1, max_length=500)
    api_key: str | None = Field(None, max_length=500)
    is_active: bool | None = Field(None)
    settings: dict[str, Any] | None = Field(None)
    is_local: bool | None = Field(None)
    company_name: str | None = Field(None, max_length=200)
    company_website: str | None = Field(None, max_length=500)
    company_country: str | None = Field(None, pattern=r"^[A-Z]{2}$")

    model_config = ConfigDict(from_attributes=True)


class AIProviderResponse(BaseModel):
    """Schema for AI provider response.

    ``api_key`` is masked in every response (``***<last4>``) regardless of
    whether the stored form is encrypted or legacy plaintext. The companion
    ``has_api_key`` field lets the UI indicate a key is configured without
    exposing it.
    """

    id: UUID
    name: str
    scope: AIScope
    provider_type: str
    api_base: str
    api_key: str | None = None
    has_api_key: bool = False
    is_active: bool
    settings: dict[str, Any] | None = None
    is_local: bool = False
    company_name: str | None = None
    company_website: str | None = None
    company_country: str | None = Field(None, pattern=r"^[A-Z]{2}$")
    preset_key: str | None = None
    tenant_id: UUID | None = None
    user_id: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _mask_api_key(self) -> "AIProviderResponse":
        # Mask on read so no caller can accidentally bypass it. has_api_key
        # is computed from the ORIGINAL value before masking.
        original = self.api_key
        self.has_api_key = _compute_has_api_key(original)
        self.api_key = _mask_api_key_for_response(original)
        return self


class AIModelCreate(BaseModel):
    """Schema for creating a new AI model"""

    provider_id: UUID = Field(..., description="Provider ID")
    name: str = Field(..., min_length=1, max_length=200, description="Display name")
    model_name: str = Field(
        ..., min_length=1, max_length=200, description="Actual model name for API"
    )
    description: str | None = Field(None, description="Description")
    capabilities: list[AIModelCapability] = Field(
        default_factory=lambda: [AIModelCapability.TEXT],
        description=(
            "Modalities this model supports (its features): 'text' (baseline, "
            "every model), 'vision' (image input — multimodal chat / vision "
            "OCR), 'stt' (speech-to-text — the 'transcription' task), plus "
            "'tools' / 'tts' / 'embeddings' (§15 family vocabulary). Tasks "
            "require specific capabilities, so the task-assignment picker "
            "only offers eligible models."
        ),
    )
    is_active: bool = Field(default=True, description="Enable/disable model")
    max_tokens: int = Field(default=65536, ge=1, description="Max tokens for model")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Temperature setting")
    is_local: bool | None = Field(None, description="Override provider's is_local setting")
    settings: dict[str, Any] | None = Field(
        default_factory=dict, description="Model-specific settings"
    )

    model_config = ConfigDict(from_attributes=True)


class AIModelUpdate(BaseModel):
    """Schema for updating an AI model"""

    name: str | None = Field(None, min_length=1, max_length=200)
    model_name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None)
    capabilities: list[AIModelCapability] | None = Field(None)
    is_active: bool | None = Field(None)
    max_tokens: int | None = Field(None, ge=1)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    is_local: bool | None = Field(None)
    settings: dict[str, Any] | None = Field(None)

    model_config = ConfigDict(from_attributes=True)


class AIModelResponse(BaseModel):
    """Schema for AI model response"""

    id: UUID
    provider_id: UUID
    provider_name: str | None = None
    name: str
    model_name: str
    description: str | None
    capabilities: list[AIModelCapability] = Field(default_factory=lambda: [AIModelCapability.TEXT])
    is_active: bool
    max_tokens: int | None = 65536
    temperature: float | None = 0.7
    is_local: bool | None = None
    settings: dict[str, Any] | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AITaskAssignmentResponse(BaseModel):
    """Schema for task assignment response"""

    id: UUID
    task_type: str
    scope: AIScope
    provider_id: UUID | None
    provider_name: str | None = None
    model_id: UUID | None
    model_name: str | None = None
    is_active: bool
    priority: int
    tenant_id: UUID | None
    user_id: UUID | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class AITaskAssignmentCreate(BaseModel):
    """Schema for creating a new task assignment"""

    task_type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Task type: ocr, nlp, medication_interaction, anomaly_detection",
    )
    scope: AIScope = Field(default=AIScope.SYSTEM, description="Scope of assignment")
    provider_id: UUID | None = Field(None, description="Provider ID")
    model_id: UUID | None = Field(None, description="Model ID")
    is_active: bool = Field(default=True, description="Enable/disable assignment")
    priority: int = Field(default=0, ge=0, description="Priority for ordering")
    tenant_id: UUID | None = Field(None, description="Tenant ID (nullable for global)")
    user_id: UUID | None = Field(None, description="User ID (nullable for global/tenant)")

    model_config = ConfigDict(from_attributes=True)


class AITaskAssignmentUpdate(BaseModel):
    """Schema for updating a task assignment"""

    task_type: str | None = Field(None, min_length=1, max_length=50)
    provider_id: UUID | None = Field(None)
    model_id: UUID | None = Field(None)
    is_active: bool | None = Field(None)
    priority: int | None = Field(None, ge=0)

    model_config = ConfigDict(from_attributes=True)


class AIProviderWithModelsResponse(BaseModel):
    """Schema for provider with models (api_key is masked)."""

    id: UUID
    name: str
    provider_type: str
    api_base: str
    api_key: str | None = None
    has_api_key: bool = False
    is_active: bool
    settings: dict[str, Any] | None = None
    is_local: bool = False
    company_name: str | None = None
    company_website: str | None = None
    company_country: str | None = Field(None, pattern=r"^[A-Z]{2}$")
    preset_key: str | None = None
    tenant_id: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    models: list[AIModelResponse]

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _mask_api_key(self) -> "AIProviderWithModelsResponse":
        original = self.api_key
        self.has_api_key = _compute_has_api_key(original)
        self.api_key = _mask_api_key_for_response(original)
        return self


class TaskTypeAssignment(BaseModel):
    """Schema for task type with its assignment"""

    task_type: str
    provider: AIProviderResponse | None
    model: AIModelResponse | None
    assignment_id: UUID | None

    model_config = ConfigDict(from_attributes=True)


class AIConfigSummary(BaseModel):
    """Summary of AI configuration"""

    providers: list[AIProviderResponse]
    models: list[AIModelResponse]
    task_assignments: list[AITaskAssignmentResponse]
    default: TaskTypeAssignment | None
    ocr: TaskTypeAssignment | None
    nlp: TaskTypeAssignment | None
    medication_interaction: TaskTypeAssignment | None
    anomaly_detection: TaskTypeAssignment | None
    fill_biomarker_form: TaskTypeAssignment | None
    fill_medication_form: TaskTypeAssignment | None
    magic_fill_examination: TaskTypeAssignment | None
    define_biomarker: TaskTypeAssignment | None
    define_medication: TaskTypeAssignment | None
    suggest_category_icon: TaskTypeAssignment | None
    generate_category_icon: TaskTypeAssignment | None
    chat: TaskTypeAssignment | None
    transcription: TaskTypeAssignment | None
    workflows: dict[str, list[TaskTypeAssignment]] | None = None
    ai_agent_max_iterations: int = 20

    model_config = ConfigDict(from_attributes=True)


class AIConfigUpdate(BaseModel):
    """Schema for updating AI configuration settings"""

    ai_agent_max_iterations: int | None = Field(None, ge=1, le=100)


class ProviderSetupOptions(BaseModel):
    """§15 options body (editable review surfaces).

    Field names ARE the contract (snake_case in Python payloads — byok gate
    R5): ``curated_ids / bind_chat / bind_vision / bind_stt``.
    """

    curated_ids: list[str] | None = None
    bind_chat: bool = True
    bind_vision: bool = True
    bind_stt: bool = True


class ProviderSetupRequest(BaseModel):
    """Request for one-click setup of a provider preset.

    ``scope`` picks the config layer (SYSTEM: system admins, TENANT: tenant
    admins, USER: personal — the default). Role access is enforced by the
    endpoint's ``check_scope_access``.
    """

    api_key: str | None = Field(
        None,
        max_length=500,
        description="Vendor API key (not needed for local presets)",
    )
    name: str | None = Field(None, max_length=100, description="Connection name")
    scope: AIScope = Field(AIScope.USER, description="Config layer to create/adopt in")
    options: ProviderSetupOptions = Field(default_factory=ProviderSetupOptions)


class ProviderSetupResponse(BaseModel):
    """Outcome of a successful setup run (provider key is masked)."""

    provider: AIProviderResponse
    catalog_count: int
    curated_missed: bool
    assigned_chat_model: str | None = None
    assigned_vision_model: str | None = None
    assigned_stt_model: str | None = None


class ProviderSetDefaultRequest(BaseModel):
    """Bind one of the provider's models to a USER-scope task slot."""

    model_name: str = Field(..., min_length=1, max_length=200)
    task: str = Field(
        "default",
        min_length=1,
        max_length=50,
        description="Task slot (TaskType value; 'default' is the chat/text fallback)",
    )


class ProviderSetDefaultResponse(BaseModel):
    task: str
    model: AIModelResponse


class ProviderPresetResponse(BaseModel):
    """One enabled setup preset (§15 canonical data, health overlay applied)."""

    key: str
    name: str
    provider_type: str
    wire_type: str
    base_url: str
    fixed_base: bool
    local: bool
    key_url: str | None = None
    preferred_model: dict[str, Any] | None = None
    curated_models: list[str] | None = None
    stt_model: str | None = None
    steps: list[str] | None = None
    free_tier_note: str | None = None


class ProviderPresetsResponse(BaseModel):
    """The setup tile surface: enabled presets (family order) + disabled
    registry-native presets with their recorded reasons (§15 overlay)."""

    order: list[str]
    presets: dict[str, ProviderPresetResponse]
    disabled: dict[str, str]
