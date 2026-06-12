from typing import Optional, List, Any, Dict
from pydantic import BaseModel, ConfigDict, field_validator
from uuid import UUID
from datetime import datetime


class ExaminationCategoryBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[Dict[str, Any]] = None

    @field_validator("icon", mode="before")
    @classmethod
    def transform_legacy_icon(cls, v: Any) -> Any:
        if isinstance(v, str):
            return {"type": "lucide", "value": v}
        return v


class ExaminationCategoryCreate(ExaminationCategoryBase):
    pass


class ExaminationCategoryUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[Dict[str, Any]] = None


class ExaminationCategoryResponse(ExaminationCategoryBase):
    id: UUID
    tenant_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
