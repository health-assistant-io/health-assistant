"""Patient FHIR schemas"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

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

    name: list[dict[str, Any]] = Field(
        ..., description="Patient name objects (FHIR HumanName, 0..*)"
    )
    user_id: UUID | None = None
    gender: Gender
    birth_date: date | None = None
    mrn: str | None = Field(None, description="Medical Record Number")
    dashboard_layout: dict[str, Any] | None = Field(None, description="Custom dashboard layout")
    extensions: dict[str, Any] | None = Field(
        None,
        description=(
            "Local-keyed FHIR R4 extensions map (race / ethnicity / "
            "preferred_language / insurance_provider). Validated against the "
            "registry in app.services.fhir_extensions."
        ),
    )


class PatientCreate(PatientBase):
    """Patient creation schema"""

    tenant_id: UUID
    emergency_contact: dict[str, Any] | None = None
    address: list[dict[str, Any]] | None = None
    telecom: list[dict[str, Any]] | None = None


class PatientUpdate(BaseModel):
    """Patient update schema.

    All FHIR list-typed fields are lists here too (see ``PatientBase``).
    """

    name: list[dict[str, Any]] | None = None
    gender: Gender | None = None
    birth_date: date | None = None
    mrn: str | None = None
    emergency_contact: dict[str, Any] | None = None
    address: list[dict[str, Any]] | None = None
    telecom: list[dict[str, Any]] | None = None
    extensions: dict[str, Any] | None = Field(
        None,
        description=(
            "Replacement FHIR R4 extensions map (see PatientBase). The whole "
            "object is replaced on update; partial-merge is not supported."
        ),
    )


class PatientResponse(PatientBase):
    """Patient response schema"""

    id: UUID
    deceased_boolean: bool | None = None
    deceased_datetime: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
