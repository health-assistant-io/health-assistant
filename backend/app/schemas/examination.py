from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.concept import ConceptResponse
from app.schemas.doctor import DoctorResponse
from app.schemas.medication import MedicationRecordResponse
from app.schemas.observation import ObservationResponse
from app.schemas.organization import Organization


class DocumentStatus(BaseModel):
    id: UUID
    status: str
    progress: int
    include_in_extraction: bool = True


class ExaminationBase(BaseModel):
    patient_id: UUID | None = None
    examination_date: date | None = None
    notes: str | None = None
    patient_notes: str | None = None
    category: str | None = Field(None, description="Category name for resolution or suggestion")
    category_concept_id: UUID | None = Field(
        None, description="Direct ID for the managed category concept"
    )
    organization_id: UUID | None = Field(None, description="Direct ID for the linked facility")
    source_integration_id: UUID | None = Field(
        None, description="ID of the integration that synced this examination"
    )
    external_id: str | None = Field(None, description="External ID from the source integration")
    auto_extract_metadata: bool | None = False
    doctor_ids: list[UUID] | None = Field(default_factory=list)
    diagnoses: list[str] | None = Field(default_factory=list)
    impressions: str | None = None
    extraction_status: str | None = None
    extraction_progress: int | None = 0
    error_message: str | None = None
    medications: list[MedicationRecordResponse] | None = Field(default_factory=list)
    observations: list[ObservationResponse] | None = Field(default_factory=list)


class ExaminationCreate(ExaminationBase):
    pass


class ExaminationUpdate(BaseModel):
    patient_id: UUID | None = None
    examination_date: date | None = None
    notes: str | None = None
    patient_notes: str | None = None
    category: str | None = None
    category_concept_id: UUID | None = None
    organization_id: UUID | None = None
    source_integration_id: UUID | None = None
    external_id: str | None = None
    doctor_ids: list[UUID] | None = None
    diagnoses: list[str] | None = None
    impressions: str | None = None
    extraction_status: str | None = None
    extraction_progress: int | None = None
    auto_extract_metadata: bool | None = None


class ExaminationSummaryResponse(BaseModel):
    id: UUID
    patient_id: UUID | None = None
    examination_date: date | None = None
    notes: str | None = None
    patient_notes: str | None = None
    category: str | None = None
    doctor_ids: list[UUID] | None = Field(default_factory=list)
    extraction_status: str | None = None
    extraction_progress: int | None = 0
    error_message: str | None = None
    diagnoses: list[str] | None = Field(default_factory=list)
    impressions: str | None = None
    category_concept: ConceptResponse | None = None
    organization: Organization | None = None
    doctors: list[DoctorResponse] = []
    document_statuses: list[DocumentStatus] = []
    observation_count: int | None = 0
    medication_count: int | None = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ExaminationResponse(ExaminationBase):
    id: UUID
    category_concept: ConceptResponse | None = None
    organization: Organization | None = None
    doctors: list[DoctorResponse] = []
    document_statuses: list[DocumentStatus] = []
    clinical_events: list[dict[str, Any]] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ExaminationExtractRequest(BaseModel):
    mode: str = "full"  # "full" (OCR + Extract), "extract_only" (Only Extract)


class ExaminationBulkDeleteRequest(BaseModel):
    examination_ids: list[UUID]


class ExaminationStatusResponse(BaseModel):
    id: UUID
    extraction_status: str | None = None
    extraction_progress: int | None = 0
    error_message: str | None = None
    documents: list[DocumentStatus] = []
