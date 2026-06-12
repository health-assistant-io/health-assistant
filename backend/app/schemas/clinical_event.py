from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from typing import Optional, List, Dict, Any
from datetime import date, datetime
from app.models.enums import ClinicalEventStatus, CodingSystem


from app.schemas.biomarker import BiomarkerResponse


class EventObservationLinkBase(BaseModel):
    observation_id: UUID
    notes: Optional[str] = None


class EventObservationLinkResponse(EventObservationLinkBase):
    id: UUID
    # Include some observation details if needed
    observation: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)


class ClinicalEventCategoryBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    icon: Optional[Dict[str, Any]] = None
    color: Optional[str] = None


class ClinicalEventCategoryCreate(ClinicalEventCategoryBase):
    pass


class ClinicalEventCategoryResponse(ClinicalEventCategoryBase):
    id: UUID
    tenant_id: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)


class ClinicalEventTypeBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    icon: Optional[Dict[str, Any]] = None
    color: Optional[str] = None
    metadata_schema: Optional[Dict[str, Any]] = None
    category_id: Optional[UUID] = None


class ClinicalEventTypeCreate(ClinicalEventTypeBase):
    pass


class ClinicalEventTypeResponse(ClinicalEventTypeBase):
    id: UUID
    tenant_id: Optional[UUID] = None
    category: Optional[ClinicalEventCategoryResponse] = None
    correlated_biomarkers: List[BiomarkerResponse] = []

    model_config = ConfigDict(from_attributes=True)


class EventExaminationLinkBase(BaseModel):
    examination_id: UUID
    reason: Optional[str] = None


class EventExaminationLinkResponse(EventExaminationLinkBase):
    id: UUID
    examination_date: Optional[date] = None

    model_config = ConfigDict(from_attributes=True)


class ClinicalEventBase(BaseModel):
    patient_id: UUID
    type_id: Optional[UUID] = None
    status: ClinicalEventStatus = ClinicalEventStatus.ACTIVE
    title: str
    description: Optional[str] = None
    onset_date: Optional[datetime] = None
    resolved_date: Optional[datetime] = None
    occurrences: List[Dict[str, Any]] = Field(default_factory=list)
    event_metadata: Dict[str, Any] = Field(default_factory=dict)
    coding_system: Optional[CodingSystem] = None
    code: Optional[str] = None


class ClinicalEventCreate(ClinicalEventBase):
    examinations: Optional[List[EventExaminationLinkBase]] = Field(default_factory=list)
    observations: Optional[List[EventObservationLinkBase]] = Field(default_factory=list)


class ClinicalEventUpdate(BaseModel):
    type_id: Optional[UUID] = None
    status: Optional[ClinicalEventStatus] = None
    title: Optional[str] = None
    description: Optional[str] = None
    onset_date: Optional[datetime] = None
    resolved_date: Optional[datetime] = None
    occurrences: Optional[List[Dict[str, Any]]] = None
    event_metadata: Optional[Dict[str, Any]] = None
    coding_system: Optional[CodingSystem] = None
    code: Optional[str] = None
    examinations: Optional[List[EventExaminationLinkBase]] = None
    observations: Optional[List[EventObservationLinkBase]] = None


class ClinicalEventResponse(ClinicalEventBase):
    id: UUID
    tenant_id: UUID
    type_details: Optional[ClinicalEventTypeResponse] = None
    examinations: List[Dict[str, Any]] = []
    observations: List[Dict[str, Any]] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
