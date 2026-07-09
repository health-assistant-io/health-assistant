"""Pydantic schemas for vaccines (Phase 5)."""

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ImmunizationStatus


# --- Vaccine Catalog ---


class VaccineCodeableConcept(BaseModel):
    """Carried on the patient-instance ``vaccine_code`` JSONB."""

    text: str
    coding: Optional[List[dict]] = None
    catalog_id: Optional[UUID] = None


class VaccineCatalogBase(BaseModel):
    slug: str
    name: str
    description: Optional[str] = None
    coding_system: Optional[str] = "cvx"
    code: Optional[str] = None
    target_diseases: Optional[List[str]] = None
    dose_schedule: Optional[dict[str, Any]] = None
    contraindications: Optional[str] = None
    side_effects: Optional[List[str]] = None
    class_concept_id: Optional[UUID] = None


class VaccineCatalogCreate(VaccineCatalogBase):
    pass


class VaccineCatalogUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    code: Optional[str] = None
    target_diseases: Optional[List[str]] = None
    dose_schedule: Optional[dict[str, Any]] = None
    contraindications: Optional[str] = None
    side_effects: Optional[List[str]] = None
    class_concept_id: Optional[UUID] = None


class VaccineCatalogResponse(VaccineCatalogBase):
    id: UUID
    is_custom: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# --- Patient Immunization (instance) ---


class PatientImmunizationBase(BaseModel):
    vaccine_catalog_id: Optional[UUID] = None
    status: ImmunizationStatus = ImmunizationStatus.COMPLETED
    vaccine_code: VaccineCodeableConcept
    administered_at: Optional[datetime] = None
    dose_number: Optional[str] = Field(
        default=None, description="e.g. '1', '2', 'booster'"
    )
    lot_number: Optional[str] = None
    manufacturer: Optional[str] = None
    location: Optional[str] = None
    note: Optional[str] = None


class PatientImmunizationCreate(PatientImmunizationBase):
    pass


class PatientImmunizationUpdate(BaseModel):
    status: Optional[ImmunizationStatus] = None
    vaccine_code: Optional[VaccineCodeableConcept] = None
    administered_at: Optional[datetime] = None
    dose_number: Optional[str] = None
    lot_number: Optional[str] = None
    manufacturer: Optional[str] = None
    location: Optional[str] = None
    note: Optional[str] = None


class PatientImmunizationResponse(PatientImmunizationBase):
    id: UUID
    patient_id: UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
