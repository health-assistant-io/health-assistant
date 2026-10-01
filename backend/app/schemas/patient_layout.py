from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PatientLayoutBase(BaseModel):
    name: str = Field(default="Default Layout")
    is_default: bool = Field(default=False)
    layout_config: dict[str, Any] = Field(default_factory=dict)
    cards_config: list[dict[str, Any]] = Field(default_factory=list)


class PatientLayoutCreate(PatientLayoutBase):
    patient_id: UUID


class PatientLayoutUpdate(BaseModel):
    name: str | None = None
    is_default: bool | None = None
    layout_config: dict[str, Any] | None = None
    cards_config: list[dict[str, Any]] | None = None


class PatientLayoutResponse(PatientLayoutBase):
    id: UUID
    user_id: UUID
    patient_id: UUID

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
