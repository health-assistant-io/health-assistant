"""Patient FHIR schemas"""

from typing import Optional, Dict, Any
from uuid import UUID
from enum import Enum
from datetime import date, datetime
from pydantic import BaseModel, Field


from app.models.enums import Gender


class PatientBase(BaseModel):
    """Base patient schema"""

    name: Dict[str, Any] = Field(..., description="Patient name object")
    user_id: Optional[UUID] = None
    gender: Gender
    birth_date: Optional[date] = None
    mrn: Optional[str] = Field(None, description="Medical Record Number")
    dashboard_layout: Optional[Dict[str, Any]] = Field(
        None, description="Custom dashboard layout"
    )


class PatientCreate(PatientBase):
    """Patient creation schema"""

    tenant_id: UUID
    emergency_contact: Optional[Dict[str, Any]] = None
    address: Optional[Dict[str, Any]] = None
    telecom: Optional[Dict[str, Any]] = None


class PatientUpdate(BaseModel):
    """Patient update schema"""

    name: Optional[Dict[str, Any]] = None
    gender: Optional[Gender] = None
    birth_date: Optional[date] = None
    mrn: Optional[str] = None
    emergency_contact: Optional[Dict[str, Any]] = None
    address: Optional[Dict[str, Any]] = None
    telecom: Optional[Dict[str, Any]] = None


class PatientResponse(PatientBase):
    """Patient response schema"""

    id: UUID
    deceased_boolean: Optional[bool] = None
    deceased_datetime: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
