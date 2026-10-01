from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ObservationBase(BaseModel):
    status: str
    category: list[dict[str, Any]] | None = None
    code: dict[str, Any]
    effective_datetime: datetime | None = None
    value_quantity: dict[str, Any] | None = None
    value_string: str | None = None
    reference_range: list[dict[str, Any]] | None = None
    interpretation: str | None = None
    biomarker_id: UUID | None = None
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
    method: str | None = None
    document_id: UUID | None = None
    examination_id: UUID | None = None


class ObservationResponse(ObservationBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)
