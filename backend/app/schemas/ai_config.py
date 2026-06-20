from pydantic import BaseModel, ConfigDict, Field, ConfigDict, field_validator, model_validator
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime


from enum import Enum


from app.models.enums import AIScope


def _mask_api_key_for_response(value: Optional[str]) -> Optional[str]:
    """Audit B1: never return a plaintext api_key in any response shape.

    Accepts either the encrypted at-rest form or legacy plaintext and
    returns a ``***<last4>`` mask. Returns None if the input is None/empty.
    """
    if value is None or value == "":
        return None
    from app.core.encryption import mask_secret

    return mask_secret(value)


def _compute_has_api_key(value: Optional[str]) -> bool:
    return bool(value) and value != ""


class AIProviderCreate(BaseModel):
    """Schema for creating a new AI provider"""

    name: str = Field(
        ..., min_length=1, max_length=100, description="Display name of provider"
    )
    scope: AIScope = Field(default=AIScope.SYSTEM, description="Scope of the provider")
    provider_type: str = Field(
        ..., min_length=1, max_length=50, description="Type: openai, tesseract"
    )
    api_base: str = Field(..., min_length=1, max_length=500, description="API base URL")
    api_key: Optional[str] = Field(None, max_length=500, description="API key")
    is_active: bool = Field(default=True, description="Enable/disable provider")
    settings: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Provider-specific settings"
    )
    is_local: bool = Field(default=False, description="Whether the provider is run locally")
    company_name: Optional[str] = Field(None, max_length=200, description="Company Name")
    company_website: Optional[str] = Field(None, max_length=500, description="Company Website")
    company_country: Optional[str] = Field(None, max_length=100, description="Company Country")
    tenant_id: Optional[UUID] = Field(
        None, description="Tenant ID (nullable for global providers)"
    )
    user_id: Optional[UUID] = Field(
        None, description="User ID (nullable for global/tenant providers)"
    )

    model_config = ConfigDict(from_attributes=True)


class AIProviderUpdate(BaseModel):
    """Schema for updating an AI provider"""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    provider_type: Optional[str] = Field(None, min_length=1, max_length=50)
    api_base: Optional[str] = Field(None, min_length=1, max_length=500)
    api_key: Optional[str] = Field(None, max_length=500)
    is_active: Optional[bool] = Field(None)
    settings: Optional[Dict[str, Any]] = Field(None)
    is_local: Optional[bool] = Field(None)
    company_name: Optional[str] = Field(None, max_length=200)
    company_website: Optional[str] = Field(None, max_length=500)
    company_country: Optional[str] = Field(None, max_length=100)

    model_config = ConfigDict(from_attributes=True)


class AIProviderResponse(BaseModel):
    """Schema for AI provider response.

    Audit B1: ``api_key`` is now MASKED in every response (``***<last4>``)
    regardless of whether the stored form is encrypted or legacy plaintext.
    The previous plaintext leak allowed any authenticated user to read
    other tenants' provider keys by UUID. The companion ``has_api_key``
    field lets the UI indicate a key is configured without exposing it.
    """

    id: UUID
    name: str
    scope: AIScope
    provider_type: str
    api_base: str
    api_key: Optional[str] = None
    has_api_key: bool = False
    is_active: bool
    settings: Optional[Dict[str, Any]] = None
    is_local: bool = False
    company_name: Optional[str] = None
    company_website: Optional[str] = None
    company_country: Optional[str] = None
    tenant_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

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
    description: Optional[str] = Field(None, description="Description")
    is_active: bool = Field(default=True, description="Enable/disable model")
    max_tokens: int = Field(default=65536, ge=1, description="Max tokens for model")
    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="Temperature setting"
    )
    is_local: Optional[bool] = Field(None, description="Override provider's is_local setting")
    settings: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Model-specific settings"
    )

    model_config = ConfigDict(from_attributes=True)


class AIModelUpdate(BaseModel):
    """Schema for updating an AI model"""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    model_name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None)
    is_active: Optional[bool] = Field(None)
    max_tokens: Optional[int] = Field(None, ge=1)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    is_local: Optional[bool] = Field(None)
    settings: Optional[Dict[str, Any]] = Field(None)

    model_config = ConfigDict(from_attributes=True)


class AIModelResponse(BaseModel):
    """Schema for AI model response"""

    id: UUID
    provider_id: UUID
    provider_name: Optional[str] = None
    name: str
    model_name: str
    description: Optional[str]
    is_active: bool
    max_tokens: Optional[int] = 65536
    temperature: Optional[float] = 0.7
    is_local: Optional[bool] = None
    settings: Optional[Dict[str, Any]]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class AITaskAssignmentResponse(BaseModel):
    """Schema for task assignment response"""

    id: UUID
    task_type: str
    scope: AIScope
    provider_id: Optional[UUID]
    provider_name: Optional[str] = None
    model_id: Optional[UUID]
    model_name: Optional[str] = None
    is_active: bool
    priority: int
    tenant_id: Optional[UUID]
    user_id: Optional[UUID]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

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
    provider_id: Optional[UUID] = Field(None, description="Provider ID")
    model_id: Optional[UUID] = Field(None, description="Model ID")
    is_active: bool = Field(default=True, description="Enable/disable assignment")
    priority: int = Field(default=0, ge=0, description="Priority for ordering")
    tenant_id: Optional[UUID] = Field(
        None, description="Tenant ID (nullable for global)"
    )
    user_id: Optional[UUID] = Field(
        None, description="User ID (nullable for global/tenant)"
    )

    model_config = ConfigDict(from_attributes=True)


class AITaskAssignmentUpdate(BaseModel):
    """Schema for updating a task assignment"""

    task_type: Optional[str] = Field(None, min_length=1, max_length=50)
    provider_id: Optional[UUID] = Field(None)
    model_id: Optional[UUID] = Field(None)
    is_active: Optional[bool] = Field(None)
    priority: Optional[int] = Field(None, ge=0)

    model_config = ConfigDict(from_attributes=True)


class AIProviderWithModelsResponse(BaseModel):
    """Schema for provider with models (audit B1: api_key is masked)."""

    id: UUID
    name: str
    provider_type: str
    api_base: str
    api_key: Optional[str] = None
    has_api_key: bool = False
    is_active: bool
    settings: Optional[Dict[str, Any]] = None
    is_local: bool = False
    company_name: Optional[str] = None
    company_website: Optional[str] = None
    company_country: Optional[str] = None
    tenant_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    models: List[AIModelResponse]

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
    provider: Optional[AIProviderResponse]
    model: Optional[AIModelResponse]
    assignment_id: Optional[UUID]

    model_config = ConfigDict(from_attributes=True)


class AIConfigSummary(BaseModel):
    """Summary of AI configuration"""

    providers: List[AIProviderResponse]
    models: List[AIModelResponse]
    task_assignments: List[AITaskAssignmentResponse]
    default: Optional[TaskTypeAssignment]
    ocr: Optional[TaskTypeAssignment]
    nlp: Optional[TaskTypeAssignment]
    medication_interaction: Optional[TaskTypeAssignment]
    anomaly_detection: Optional[TaskTypeAssignment]
    fill_biomarker_form: Optional[TaskTypeAssignment]
    fill_medication_form: Optional[TaskTypeAssignment]
    magic_fill_examination: Optional[TaskTypeAssignment]
    define_biomarker: Optional[TaskTypeAssignment]
    define_medication: Optional[TaskTypeAssignment]
    suggest_category_icon: Optional[TaskTypeAssignment]
    generate_category_icon: Optional[TaskTypeAssignment]
    chat: Optional[TaskTypeAssignment]
    workflows: Optional[Dict[str, List[TaskTypeAssignment]]] = None
    ai_agent_max_iterations: int = 20

    model_config = ConfigDict(from_attributes=True)


class AIConfigUpdate(BaseModel):
    """Schema for updating AI configuration settings"""

    ai_agent_max_iterations: Optional[int] = Field(None, ge=1, le=100)
