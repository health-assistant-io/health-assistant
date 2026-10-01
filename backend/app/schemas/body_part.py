from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BodyPartBase(BaseModel):
    name: str
    slug: str | None = None
    snomed_code: str | None = None
    description: str | None = None
    is_custom: bool = False


class BodyPartCreate(BodyPartBase):
    pass


class BodyPartUpdate(BaseModel):
    name: str | None = None
    snomed_code: str | None = None
    description: str | None = None


class BodyPartResponse(BodyPartBase):
    id: UUID
    tenant_id: UUID | None = None

    model_config = ConfigDict(from_attributes=True)
