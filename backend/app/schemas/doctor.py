from pydantic import BaseModel, ConfigDict, Field
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
    specialty: Optional[str] = None
    license_number: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    telecom: Optional[List[ContactPoint]] = None
    address: Optional[Address] = None
    office_number: Optional[str] = None
    office_details: Optional[str] = None


class DoctorCreate(DoctorBase):
    pass


class DoctorUpdate(BaseModel):
    name: Optional[str] = None
    specialty: Optional[str] = None
    license_number: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    telecom: Optional[List[ContactPoint]] = None
    address: Optional[Address] = None
    office_number: Optional[str] = None
    office_details: Optional[str] = None


class DoctorResponse(DoctorBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)
