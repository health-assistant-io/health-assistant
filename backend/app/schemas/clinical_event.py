from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from uuid import UUID
from typing import Optional, List, Dict, Any
from datetime import date, datetime
from app.models.enums import (
    CatalogRelationType,
    CatalogType,
    ClinicalEventStatus,
    CodingSystem,
    ConceptKind,
    MetadataFieldType,
)


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


# ---------------------------------------------------------------------------
# metadata_schema — typed descriptor for ClinicalEventType.metadata_schema.
#
# The JSONB column stores ``{"fields": [MetadataField, ...]}`` verbatim; this
# Pydantic model validates the shape on create/update (fail-loud: a bad seed
# or API payload raises instead of silently rendering nothing in the form).
# The response model keeps the raw ``Dict[str, Any]`` shape so the stored
# payload round-trips unchanged.
# ---------------------------------------------------------------------------


class MetadataField(BaseModel):
    """One field descriptor in a ``ClinicalEventType.metadata_schema``.

    The ``type`` discriminator drives the frontend renderer switch in
    ``DynamicMetadataForm`` (exhaustive — a missing branch is a TS compile
    error). ``CATALOG_SELECT`` fields declare which catalogs the picker may
    search (``catalogs``) and, when narrowed to concepts, which ``concept_kind``
    (e.g. ``examination_category``).
    """

    name: str
    label: str
    type: MetadataFieldType
    required: bool = False
    # Optional input placeholder for text/number fields (shown greyed when the
    # field is empty). Helps the user understand the field's scope without a
    # separate description.
    placeholder: Optional[str] = None
    # CATALOG_SELECT only — which catalogs the picker may search.
    catalogs: Optional[List[CatalogType]] = None
    # Only valid when catalogs == [CONCEPT]: narrows to one ConceptKind
    # (e.g. EVENT_CATEGORY, EXAMINATION_CATEGORY, SPECIALTY).
    concept_kind: Optional[ConceptKind] = None
    # CATALOG_SELECT only — single vs multi selection.
    multi: bool = False
    # Optional: how the picked item relates to the event (semantic hint).
    relation: Optional[CatalogRelationType] = None
    # NUMBER only — inclusive bounds.
    min: Optional[float] = None
    max: Optional[float] = None

    @model_validator(mode="after")
    def _validate_catalog_select_constraints(self) -> "MetadataField":
        # CATALOG_SELECT requires at least one catalog.
        if self.type == MetadataFieldType.CATALOG_SELECT:
            if not self.catalogs:
                raise ValueError(
                    "A 'catalog-select' field requires a non-empty 'catalogs' "
                    "list (e.g. [\"anatomy\"] or [\"concept\"])."
                )
        # concept_kind is only meaningful when the field picks from concepts.
        if self.concept_kind is not None:
            if self.catalogs != [CatalogType.CONCEPT]:
                raise ValueError(
                    "'concept_kind' may only be set when 'catalogs' is exactly "
                    "[\"concept\"] — a kind filter is meaningless for other "
                    "catalogs."
                )
        # catalogs/relation/multi are silently ignored for non-catalog types;
        # we don't raise (a seed may carry harmless defaults) but they won't
        # render. Keeping this lenient avoids over-constraining authoring.
        return self


class MetadataSchema(BaseModel):
    """The typed top-level ``metadata_schema`` payload stored on a
    ``ClinicalEventType``."""

    fields: List[MetadataField]

    @field_validator("fields")
    @classmethod
    def _non_empty(cls, v: List[MetadataField]) -> List[MetadataField]:
        if not v:
            raise ValueError("metadata_schema.fields must contain at least one field")
        return v

    @field_validator("fields")
    @classmethod
    def _unique_field_names(
        cls, v: List[MetadataField]
    ) -> List[MetadataField]:
        names = [f.name for f in v]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(
                f"metadata_schema.fields has duplicate name(s): {sorted(dupes)}"
            )
        return v


class ClinicalEventTypeBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    icon: Optional[Dict[str, Any]] = None
    color: Optional[str] = None
    # Raw JSONB passthrough on output (round-trips the stored dict unchanged).
    # Input validation happens on ClinicalEventTypeCreate below.
    metadata_schema: Optional[Dict[str, Any]] = None
    severity_scale: Optional[Dict[str, Any]] = None
    phases: Optional[List[Dict[str, Any]]] = None
    milestones: Optional[List[Dict[str, Any]]] = None
    default_duration_days: Optional[int] = None
    category_concept_id: Optional[UUID] = None


class ClinicalEventTypeCreate(ClinicalEventTypeBase):
    # Override to validate the metadata_schema shape on input (fail-loud).
    # model_dump() still yields a JSONB-compatible dict.
    metadata_schema: Optional[MetadataSchema] = None


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
