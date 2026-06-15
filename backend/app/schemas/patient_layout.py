from typing import Optional, Dict, Any, List
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class PatientLayoutBase(BaseModel):
    name: str = Field(default="Default Layout")
    is_default: bool = Field(default=False)
    layout_config: Dict[str, Any] = Field(default_factory=dict)
    cards_config: List[Dict[str, Any]] = Field(default_factory=list)


class PatientLayoutCreate(PatientLayoutBase):
    patient_id: UUID


class PatientLayoutUpdate(BaseModel):
    name: Optional[str] = None
    is_default: Optional[bool] = None
    layout_config: Optional[Dict[str, Any]] = None
    cards_config: Optional[List[Dict[str, Any]]] = None


class PatientLayoutResponse(PatientLayoutBase):
    id: UUID
    user_id: UUID
    patient_id: UUID

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
