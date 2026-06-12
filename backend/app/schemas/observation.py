from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from typing import Optional, Dict, Any, List
from datetime import datetime


class ObservationBase(BaseModel):
    status: str
    category: Optional[List[Dict[str, Any]]] = None
    code: Dict[str, Any]
    effective_datetime: Optional[datetime] = None
    value_quantity: Optional[Dict[str, Any]] = None
    value_string: Optional[str] = None
    reference_range: Optional[List[Dict[str, Any]]] = None
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
    method: Optional[str] = None
    document_id: Optional[str] = None
    examination_id: Optional[UUID] = None


class ObservationResponse(ObservationBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)
