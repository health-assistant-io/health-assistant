"""Observation FHIR schemas"""

from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class ObservationBase(BaseModel):
    """Base observation schema"""

    status: str = Field(default="final", description="Observation status")
    code: Dict[str, Any] = Field(..., description="LOINC code object")
    subject: Dict[str, Any] = Field(..., description="Patient reference")


class ObservationCreate(ObservationBase):
    """Observation creation schema"""

    tenant_id: UUID
    value_quantity: Optional[Dict[str, Any]] = Field(
        None, description="Value with unit"
    )
    value_string: Optional[str] = None
    effective_datetime: Optional[datetime] = None
    category: Optional[Dict[str, Any]] = None
    reference_range: Optional[Dict[str, Any]] = None
    interpretation: Optional[str] = None
    biomarker_id: Optional[UUID] = None
    biomarker_slug: Optional[str] = None
    biomarker_info: Optional[str] = None
    biomarker_aliases: Optional[List[str]] = None
    biomarker_reference_range_min: Optional[float] = None
    biomarker_reference_range_max: Optional[float] = None
    raw_value: Optional[float] = None
    normalized_value: Optional[float] = None
    normalized_unit: Optional[str] = None
    lab_reference_range: Optional[Dict[str, Any]] = None
    relative_score: Optional[float] = None
    comment: Optional[str] = None
    performer: Optional[Dict[str, Any]] = None

class ObservationUpdate(BaseModel):
    """Observation update schema"""

    status: Optional[str] = None
    code: Optional[Dict[str, Any]] = None
    subject: Optional[Dict[str, Any]] = None
    value_quantity: Optional[Dict[str, Any]] = None
    value_string: Optional[str] = None
    effective_datetime: Optional[datetime] = None
    category: Optional[Dict[str, Any]] = None
    reference_range: Optional[Dict[str, Any]] = None
    interpretation: Optional[str] = None
    comment: Optional[str] = None
    performer: Optional[Dict[str, Any]] = None

class ObservationResponse(ObservationBase):
    """Observation response schema"""

    id: UUID
    value_quantity: Optional[Dict[str, Any]] = None
    value_string: Optional[str] = None
    effective_datetime: Optional[datetime] = None
    category: Optional[Dict[str, Any]] = None
    reference_range: Optional[Dict[str, Any]] = None
    interpretation: Optional[str] = None
    comment: Optional[str] = None
    performer: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class ObservationList(BaseModel):
    """Observation list response schema"""

    items: List[ObservationResponse]
    total: int = Field(..., description="Total number of observations")
