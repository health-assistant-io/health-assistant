from pydantic import BaseModel, ConfigDict
from uuid import UUID
from typing import Optional


class BodyPartBase(BaseModel):
    name: str
    slug: Optional[str] = None
    snomed_code: Optional[str] = None
    description: Optional[str] = None
    is_custom: bool = False


class BodyPartCreate(BodyPartBase):
    pass


class BodyPartUpdate(BaseModel):
    name: Optional[str] = None
    snomed_code: Optional[str] = None
    description: Optional[str] = None


class BodyPartResponse(BodyPartBase):
    id: UUID
    tenant_id: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)
