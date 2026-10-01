"""Pydantic schemas for vaccines (Phase 5)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ImmunizationStatus

# --- Vaccine Catalog ---


class VaccineCodeableConcept(BaseModel):
    """Carried on the patient-instance ``vaccine_code`` JSONB."""

    text: str
    coding: list[dict] | None = None
    catalog_id: UUID | None = None


class VaccineCatalogBase(BaseModel):
    slug: str
    name: str
    description: str | None = None
    coding_system: str | None = "cvx"
    code: str | None = None
    target_diseases: list[str] | None = None
    dose_schedule: dict[str, Any] | None = None
    contraindications: str | None = None
    side_effects: list[str] | None = None
    class_concept_id: UUID | None = None


class VaccineCatalogCreate(VaccineCatalogBase):
    pass


class VaccineCatalogUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    code: str | None = None
    target_diseases: list[str] | None = None
    dose_schedule: dict[str, Any] | None = None
    contraindications: str | None = None
    side_effects: list[str] | None = None
    class_concept_id: UUID | None = None


class VaccineCatalogResponse(VaccineCatalogBase):
    id: UUID
    is_custom: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# --- Patient Immunization (instance) ---


class PatientImmunizationBase(BaseModel):
    patient_id: UUID | None = None
    vaccine_catalog_id: UUID | None = None
    examination_id: UUID | None = None
    status: ImmunizationStatus = ImmunizationStatus.COMPLETED
    vaccine_code: VaccineCodeableConcept
    administered_at: datetime | None = None
    dose_number: str | None = Field(default=None, description="e.g. '1', '2', 'booster'")
    lot_number: str | None = None
    manufacturer: str | None = None
    location: str | None = None
    note: str | None = None


class PatientImmunizationCreate(PatientImmunizationBase):
    # Integration dedup key (Phase 4 of the fhir-server multi-resource sync
    # plan). Set by integration providers on the objects they return from
    # ``pull_immunizations``; the engine reads it and forwards it to the
    # service. ``source_integration_id`` is NOT on the schema — the engine
    # always supplies it (= the integration's own id).
    external_id: str | None = None


class PatientImmunizationUpdate(BaseModel):
    examination_id: UUID | None = None
    status: ImmunizationStatus | None = None
    vaccine_code: VaccineCodeableConcept | None = None
    administered_at: datetime | None = None
    dose_number: str | None = None
    lot_number: str | None = None
    manufacturer: str | None = None
    location: str | None = None
    note: str | None = None


class PatientImmunizationResponse(PatientImmunizationBase):
    id: UUID
    patient_id: UUID
    source_integration_id: UUID | None = None
    external_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
