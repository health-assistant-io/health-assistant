from pydantic import BaseModel, ConfigDict
from uuid import UUID
from typing import Optional, List


class ContactPoint(BaseModel):
    system: str  # phone, email, fax, url, pager, sms, other
    value: str
    use: Optional[str] = "work"  # home, work, temp, old, mobile


class Address(BaseModel):
    line: Optional[List[str]] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postalCode: Optional[str] = None
    country: Optional[str] = None


class DoctorBase(BaseModel):
    name: str
    user_id: Optional[UUID] = None
    # Backward-compat: ``specialty`` remains a readable string (the linked
    # concept's name). For writes, prefer ``specialty_concept_id`` —
    # ``specialty`` is best-effort resolved to a concept in doctor_service.
    specialty: Optional[str] = None
    specialty_concept_id: Optional[UUID] = None
    license_number: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    telecom: Optional[List[ContactPoint]] = None
    # FHIR Practitioner.address is 0..* (a list). Stored as a JSONB list; a
    # non-list value (e.g. a stray single dict) is a format error and will
    # raise a Pydantic validation error here rather than being silently coerced.
    address: Optional[List[Address]] = None
    office_number: Optional[str] = None
    office_details: Optional[str] = None


class DoctorCreate(DoctorBase):
    pass


class DoctorUpdate(BaseModel):
    name: Optional[str] = None
    specialty: Optional[str] = None
    specialty_concept_id: Optional[UUID] = None
    license_number: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    telecom: Optional[List[ContactPoint]] = None
    address: Optional[List[Address]] = None
    office_number: Optional[str] = None
    office_details: Optional[str] = None


class DoctorResponse(DoctorBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)
