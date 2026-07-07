from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from typing import Optional, List, Any, Dict
from datetime import date, datetime


from app.schemas.doctor import DoctorResponse


from app.schemas.concept import ConceptResponse
from app.schemas.medication import MedicationRecordResponse
from app.schemas.observation import ObservationResponse
from app.schemas.organization import Organization


class DocumentStatus(BaseModel):
    id: UUID
    status: str
    progress: int
    include_in_extraction: bool = True


class ExaminationBase(BaseModel):
    patient_id: Optional[UUID] = None
    examination_date: Optional[date] = None
    notes: Optional[str] = None
    patient_notes: Optional[str] = None
    category: Optional[str] = Field(
        None, description="Category name for resolution or suggestion"
    )
    category_concept_id: Optional[UUID] = Field(
        None, description="Direct ID for the managed category concept"
    )
    organization_id: Optional[UUID] = Field(
        None, description="Direct ID for the linked facility"
    )
    source_integration_id: Optional[UUID] = Field(
        None, description="ID of the integration that synced this examination"
    )
    external_id: Optional[str] = Field(
        None, description="External ID from the source integration"
    )
    auto_extract_metadata: Optional[bool] = False
    doctor_ids: Optional[List[UUID]] = Field(default_factory=list)
    diagnoses: Optional[List[str]] = Field(default_factory=list)
    impressions: Optional[str] = None
    extraction_status: Optional[str] = None
    extraction_progress: Optional[int] = 0
    error_message: Optional[str] = None
    medications: Optional[List[MedicationRecordResponse]] = Field(default_factory=list)
    observations: Optional[List[ObservationResponse]] = Field(default_factory=list)


class ExaminationCreate(ExaminationBase):
    pass


class ExaminationUpdate(BaseModel):
    patient_id: Optional[UUID] = None
    examination_date: Optional[date] = None
    notes: Optional[str] = None
    patient_notes: Optional[str] = None
    category: Optional[str] = None
    category_concept_id: Optional[UUID] = None
    organization_id: Optional[UUID] = None
    source_integration_id: Optional[UUID] = None
    external_id: Optional[str] = None
    doctor_ids: Optional[List[UUID]] = None
    diagnoses: Optional[List[str]] = None
    impressions: Optional[str] = None
    extraction_status: Optional[str] = None
    extraction_progress: Optional[int] = None
    auto_extract_metadata: Optional[bool] = None


class ExaminationSummaryResponse(BaseModel):
    id: UUID
    patient_id: Optional[UUID] = None
    examination_date: Optional[date] = None
    notes: Optional[str] = None
    patient_notes: Optional[str] = None
    category: Optional[str] = None
    doctor_ids: Optional[List[UUID]] = Field(default_factory=list)
    extraction_status: Optional[str] = None
    extraction_progress: Optional[int] = 0
    error_message: Optional[str] = None
    diagnoses: Optional[List[str]] = Field(default_factory=list)
    impressions: Optional[str] = None
    category_concept: Optional[ConceptResponse] = None
    organization: Optional[Organization] = None
    doctors: List[DoctorResponse] = []
    document_statuses: List[DocumentStatus] = []
    observation_count: Optional[int] = 0
    medication_count: Optional[int] = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ExaminationResponse(ExaminationBase):
    id: UUID
    category_concept: Optional[ConceptResponse] = None
    organization: Optional[Organization] = None
    doctors: List[DoctorResponse] = []
    document_statuses: List[DocumentStatus] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ExaminationExtractRequest(BaseModel):
    mode: str = "full"  # "full" (OCR + Extract), "extract_only" (Only Extract)


class ExaminationBulkDeleteRequest(BaseModel):
    examination_ids: List[UUID]


class ExaminationStatusResponse(BaseModel):
    id: UUID
    extraction_status: Optional[str] = None
    extraction_progress: Optional[int] = 0
    error_message: Optional[str] = None
    documents: List[DocumentStatus] = []
