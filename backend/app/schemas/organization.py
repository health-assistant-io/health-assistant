from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OrganizationType
from app.schemas.doctor import DoctorResponse


class OrganizationBase(BaseModel):
    name: str = Field(..., description="Name of the organization")
    active: bool = True
    org_type: OrganizationType = Field(
        default=OrganizationType.HOUSEHOLD, description="Internal organization type"
    )
    type: list[dict] | None = Field(
        None, description="FHIR Kind of organization (Hospital, Clinic, etc.)"
    )
    alias: list[str] | None = None
    telecom: list[dict] | None = None
    address: list[dict] | None = None
    part_of_id: UUID | None = None
    contact: list[dict] | None = None


class OrganizationCreate(OrganizationBase):
    doctor_ids: list[UUID] | None = None


class OrganizationUpdate(BaseModel):
    name: str | None = None
    active: bool | None = None
    org_type: OrganizationType | None = None
    type: list[dict] | None = None
    alias: list[str] | None = None
    telecom: list[dict] | None = None
    address: list[dict] | None = None
    part_of_id: UUID | None = None
    contact: list[dict] | None = None
    doctor_ids: list[UUID] | None = None


class Organization(OrganizationBase):
    id: UUID
    tenant_id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class OrganizationWithDetails(Organization):
    doctors: list[DoctorResponse] = []
    departments: list[Organization] = []

    model_config = ConfigDict(from_attributes=True)
