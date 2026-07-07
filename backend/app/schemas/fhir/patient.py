"""Patient FHIR schemas"""

from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field


from app.models.enums import Gender


class PatientBase(BaseModel):
    """Base patient schema.

    Cardinality follows FHIR R4:
    - ``name`` is ``0..*`` (list of ``HumanName``).
    - ``address`` is ``0..*`` (list of ``Address``).
    - ``telecom`` is ``0..*`` (list of ``ContactPoint``).

    The ORM column on ``PatientModel`` tolerates both single-dict and list
    shapes via ``_coerce_*`` helpers (defensive against legacy data), but
    the REST schema must match the FHIR R4 spec so that canonical FHIR JSON
    (``"name": [{"family": "Doe"}]``) is accepted rather than 422'd.
    """

    name: List[Dict[str, Any]] = Field(
        ..., description="Patient name objects (FHIR HumanName, 0..*)"
    )
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
    address: Optional[List[Dict[str, Any]]] = None
    telecom: Optional[List[Dict[str, Any]]] = None


class PatientUpdate(BaseModel):
    """Patient update schema.

    All FHIR list-typed fields are lists here too (see ``PatientBase``).
    """

    name: Optional[List[Dict[str, Any]]] = None
    gender: Optional[Gender] = None
    birth_date: Optional[date] = None
    mrn: Optional[str] = None
    emergency_contact: Optional[Dict[str, Any]] = None
    address: Optional[List[Dict[str, Any]]] = None
    telecom: Optional[List[Dict[str, Any]]] = None


class PatientResponse(PatientBase):
    """Patient response schema"""

    id: UUID
    deceased_boolean: Optional[bool] = None
    deceased_datetime: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
