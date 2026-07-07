from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from typing import Optional, List, Dict, Any
from datetime import date, datetime
from app.models.enums import ClinicalEventStatus, CodingSystem


from app.schemas.biomarker import BiomarkerResponse
from app.schemas.concept import ConceptResponse


class EventObservationLinkBase(BaseModel):
    observation_id: UUID
    notes: Optional[str] = None


class EventObservationLinkResponse(EventObservationLinkBase):
    id: UUID
    # Include some observation details if needed
    observation: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)


class ClinicalEventTypeBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    icon: Optional[Dict[str, Any]] = None
    color: Optional[str] = None
    metadata_schema: Optional[Dict[str, Any]] = None
    severity_scale: Optional[Dict[str, Any]] = None
    phases: Optional[List[Dict[str, Any]]] = None
    milestones: Optional[List[Dict[str, Any]]] = None
    default_duration_days: Optional[int] = None
    category_concept_id: Optional[UUID] = None


class ClinicalEventTypeCreate(ClinicalEventTypeBase):
    pass


class ClinicalEventTypeResponse(ClinicalEventTypeBase):
    id: UUID
    tenant_id: Optional[UUID] = None
    category_concept: Optional[ConceptResponse] = None
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
    anatomy_links: List[Dict[str, Any]] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ClinicalEventOccurrenceCreate(BaseModel):
    """Payload for ``POST /clinical-events/{id}/occurrences``.

    ``occurred_at`` is required (an occurrence without a timestamp is meaningless
    and the column is NOT NULL). ``intensity`` is an optional 1..10 scale for
    pain-style journeys; ``severity`` is a free-text ordinal ('mild'/'moderate'/
    'severe'); ``anatomy_id`` optionally ties the episode to a body site.
    """

    occurred_at: datetime
    title: Optional[str] = None
    severity: Optional[str] = None
    intensity: Optional[int] = Field(default=None, ge=1, le=10)
    notes: Optional[str] = None
    anatomy_id: Optional[UUID] = None
    metadata: Optional[Dict[str, Any]] = None


class EventAnatomyLinkCreate(BaseModel):
    """Payload for ``POST /clinical-events/{id}/link-anatomy``.

    ``relation_type`` distinguishes ``primary_site`` / ``radiates_to`` /
    ``referred_to`` (free-text; defaults to ``primary_site``).
    """

    anatomy_id: UUID
    relation_type: Optional[str] = "primary_site"


class BiomarkerCorrelationCreate(BaseModel):
    """Payload for ``POST /clinical-events/types/{id}/biomarkers``.

    Binds a biomarker definition to an event type so the engine can recommend
    it for journeys of that type. ``correlation_type`` is free-text
    ('monitoring' / 'diagnostic'); defaults to 'monitoring'.
    """

    biomarker_id: UUID
    correlation_type: str = "monitoring"
    description: Optional[str] = None
