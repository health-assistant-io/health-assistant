from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from app.schemas.doctor import DoctorResponse
from app.models.enums import OrganizationType


class OrganizationBase(BaseModel):
    name: str = Field(..., description="Name of the organization")
    active: bool = True
    org_type: OrganizationType = Field(
        default=OrganizationType.HOUSEHOLD, description="Internal organization type"
    )
    type: Optional[List[dict]] = Field(
        None, description="FHIR Kind of organization (Hospital, Clinic, etc.)"
    )
    alias: Optional[List[str]] = None
    telecom: Optional[List[dict]] = None
    address: Optional[List[dict]] = None
    part_of_id: Optional[UUID] = None
    contact: Optional[List[dict]] = None


class OrganizationCreate(OrganizationBase):
    doctor_ids: Optional[List[UUID]] = None


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    active: Optional[bool] = None
    org_type: Optional[OrganizationType] = None
    type: Optional[List[dict]] = None
    alias: Optional[List[str]] = None
    telecom: Optional[List[dict]] = None
    address: Optional[List[dict]] = None
    part_of_id: Optional[UUID] = None
    contact: Optional[List[dict]] = None
    doctor_ids: Optional[List[UUID]] = None


class Organization(OrganizationBase):
    id: UUID
    tenant_id: UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class OrganizationWithDetails(Organization):
    doctors: List[DoctorResponse] = []
    departments: List[Organization] = []

    model_config = ConfigDict(from_attributes=True)
