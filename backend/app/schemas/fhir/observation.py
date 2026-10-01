"""Observation FHIR schemas"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ObservationBase(BaseModel):
    """Base observation schema"""

    status: str = Field(default="final", description="Observation status")
    code: dict[str, Any] = Field(..., description="LOINC code object")
    subject: dict[str, Any] = Field(..., description="Patient reference")


class ObservationCreate(ObservationBase):
    """Observation creation schema"""

    tenant_id: UUID
    value_quantity: dict[str, Any] | None = Field(None, description="Value with unit")
    value_string: str | None = None
    # Coded categorical value (STATE biomarkers). Tolerates both
    # snake_case (``value_codeable_concept``) and FHIR camelCase
    # (``valueCodeableConcept``) on input — the create path normalizes.
    value_codeable_concept: dict[str, Any] | None = Field(None, alias="value_codeable_concept")
    effective_datetime: datetime | None = None
    category: list[dict[str, Any]] | None = None
    reference_range: list[dict[str, Any]] | None = None
    interpretation: str | list[dict[str, Any]] | None = None
    biomarker_id: UUID | None = None
    examination_id: UUID | None = None
    biomarker_slug: str | None = None
    biomarker_info: str | None = None
    biomarker_aliases: list[str] | None = None
    biomarker_reference_range_min: float | None = None
    biomarker_reference_range_max: float | None = None
    raw_value: float | None = None
    normalized_value: float | None = None
    normalized_unit: str | None = None
    lab_reference_range: dict[str, Any] | None = None
    relative_score: float | None = None
    comment: str | None = None
    performer: list[dict[str, Any]] | None = None
    component: list[dict[str, Any]] | None = None

    model_config = ConfigDict(populate_by_name=True)


class ObservationUpdate(BaseModel):
    """Observation update schema"""

    status: str | None = None
    code: dict[str, Any] | None = None
    subject: dict[str, Any] | None = None
    value_quantity: dict[str, Any] | None = None
    value_string: str | None = None
    value_codeable_concept: dict[str, Any] | None = None
    effective_datetime: datetime | None = None
    category: list[dict[str, Any]] | None = None
    reference_range: list[dict[str, Any]] | None = None
    interpretation: str | list[dict[str, Any]] | None = None
    comment: str | None = None
    performer: list[dict[str, Any]] | None = None
    component: list[dict[str, Any]] | None = None


class ObservationResponse(ObservationBase):
    """Observation response schema"""

    id: UUID
    value_quantity: dict[str, Any] | None = None
    value_string: str | None = None
    value_codeable_concept: dict[str, Any] | None = None
    effective_datetime: datetime | None = None
    category: list[dict[str, Any]] | None = None
    reference_range: list[dict[str, Any]] | None = None
    interpretation: str | list[dict[str, Any]] | None = None
    comment: str | None = None
    performer: list[dict[str, Any]] | None = None
    component: list[dict[str, Any]] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class ObservationList(BaseModel):
    """Observation list response schema"""

    items: list[ObservationResponse]
    total: int = Field(..., description="Total number of observations")
