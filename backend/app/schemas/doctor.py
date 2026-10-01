from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ContactPoint(BaseModel):
    system: str  # phone, email, fax, url, pager, sms, other
    value: str
    use: str | None = "work"  # home, work, temp, old, mobile


class Address(BaseModel):
    line: list[str] | None = None
    city: str | None = None
    state: str | None = None
    postalCode: str | None = None
    country: str | None = None


class DoctorBase(BaseModel):
    name: str
    user_id: UUID | None = None
    # Backward-compat: ``specialty`` remains a readable string (the linked
    # concept's name). For writes, prefer ``specialty_concept_id`` —
    # ``specialty`` is best-effort resolved to a concept in doctor_service.
    specialty: str | None = None
    specialty_concept_id: UUID | None = None
    license_number: str | None = None
    email: str | None = None
    phone: str | None = None
    telecom: list[ContactPoint] | None = None
    # FHIR Practitioner.address is 0..* (a list). Stored as a JSONB list; a
    # non-list value (e.g. a stray single dict) is a format error and will
    # raise a Pydantic validation error here rather than being silently coerced.
    address: list[Address] | None = None
    office_number: str | None = None
    office_details: str | None = None


class DoctorCreate(DoctorBase):
    pass


class DoctorUpdate(BaseModel):
    name: str | None = None
    specialty: str | None = None
    specialty_concept_id: UUID | None = None
    license_number: str | None = None
    email: str | None = None
    phone: str | None = None
    telecom: list[ContactPoint] | None = None
    address: list[Address] | None = None
    office_number: str | None = None
    office_details: str | None = None


class DoctorResponse(DoctorBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)
