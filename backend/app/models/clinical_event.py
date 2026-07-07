from sqlalchemy import Column, String, Text, ForeignKey, DateTime, Enum, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import (
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
)
from app.models.enums import ClinicalEventStatus, CodingSystem
from app.services.fhir_helpers import build_fhir_resource, build_meta, fhir_isoformat


class ClinicalEventType(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "clinical_event_types"

    name = Column(String(100), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    icon = Column(JSONB, nullable=True)  # { "type": "lucide", "value": "Activity" }
    color = Column(String(50), nullable=True)
    metadata_schema = Column(JSONB, nullable=True)  # { "fields": [...] }

    category_concept_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,  # Nullable for global event types
        index=True,
    )

    # Relationships
    category_concept = relationship(
        "Concept",
        foreign_keys="[ClinicalEventType.category_concept_id]",
        lazy="selectin",
    )
    events = relationship("ClinicalEvent", back_populates="type_entity")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id) if self.id else None,
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "metadata_schema": self.metadata_schema,
            "category_concept_id": str(self.category_concept_id)
            if self.category_concept_id
            else None,
            "category_concept": self.category_concept.to_dict()
            if self.category_concept
            else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
        }


class EventExaminationLink(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "event_examination_links"

    event_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinical_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    examination_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("examinations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason = Column(
        Text, nullable=True
    )  # Why this examination is related to this event

    # Relationships
    event = relationship("ClinicalEvent", back_populates="examination_links")
    examination = relationship("ExaminationModel", backref="event_links")


class EventObservationLink(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "event_observation_links"

    event_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinical_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    observation_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_observations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notes = Column(Text, nullable=True)

    # Relationships
    event = relationship("ClinicalEvent", back_populates="observation_links")
    observation = relationship("Observation", backref="event_links")


class ClinicalEvent(
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    __tablename__ = "clinical_events"

    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinical_event_types.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = Column(
        Enum(ClinicalEventStatus), default=ClinicalEventStatus.ACTIVE, nullable=False
    )
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    onset_date = Column(DateTime(timezone=True), nullable=True, index=True)
    resolved_date = Column(DateTime(timezone=True), nullable=True, index=True)

    # occurrences: JSONB list of events, e.g. [{"date": "2024-03-20", "intensity": 8, "notes": "..."}]
    occurrences = Column(JSONB, nullable=True, default=list)

    # event_metadata: JSONB for specific event data like pregnancy LMP, EDD
    event_metadata = Column(JSONB, nullable=True, default=dict)

    # FHIR / Standard coding
    coding_system = Column(Enum(CodingSystem), nullable=True)
    code = Column(String(100), nullable=True)

    # Relationships
    patient = relationship("Patient", backref="clinical_events")
    type_entity = relationship("ClinicalEventType", back_populates="events")
    examination_links = relationship(
        "EventExaminationLink", back_populates="event", cascade="all, delete-orphan"
    )
    observation_links = relationship(
        "EventObservationLink", back_populates="event", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_clinical_event_patient_type", "patient_id", "type_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id) if self.id else None,
            "patient_id": str(self.patient_id) if self.patient_id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "type_id": str(self.type_id) if self.type_id else None,
            "type_details": self.type_entity.to_dict() if self.type_entity else None,
            "status": self.status.value,
            "title": self.title,
            "description": self.description,
            "onset_date": self.onset_date.isoformat() if self.onset_date else None,
            "resolved_date": self.resolved_date.isoformat()
            if self.resolved_date
            else None,
            "occurrences": self.occurrences,
            "event_metadata": self.event_metadata,
            "coding_system": self.coding_system.value if self.coding_system else None,
            "code": self.code,
            "examinations": [
                {
                    "id": str(link.examination.id),
                    "examination_date": link.examination.examination_date.isoformat()
                    if link.examination.examination_date
                    else None,
                    "notes": link.examination.notes,
                    "reason": link.reason,
                    "examination_id": str(link.examination_id),
                }
                for link in self.examination_links
                if link.examination
            ],
            "observations": [
                {
                    **link.observation.to_dict(),
                    "notes": link.notes,
                    "observation_id": str(link.observation_id),
                }
                for link in self.observation_links
                if link.observation
            ],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_fhir_dict(self) -> dict:
        """Project this ClinicalEvent to a FHIR R4B Condition resource.

        Maps the metadata-driven event to a FHIR Condition:
        - ``status`` enum → Condition.clinicalStatus (HL7 condition-clinical)
        - ``code`` + ``coding_system`` + ``title`` → Condition.code
        - ``patient_id`` → Condition.subject
        - ``onset_date`` → Condition.onsetDateTime
        - ``resolved_date`` → Condition.abatementDateTime
        - ``description`` → Condition.note
        - ``created_at`` → Condition.recordedDate

        Validated by ``fhir.resources`` via ``build_fhir_resource`` so invalid
        data can never be persisted through the FHIR facade.

        Audit items C7 + C16: Clinical Events gain a FHIR projection so the
        facade can expose them at ``/fhir/R4/Condition`` without a new table.
        """
        # Condition.clinicalStatus uses HL7 condition-clinical codes:
        # active | recurrence | relapse | inactive | resolved | remission
        status_value = (self.status.value if self.status else "UNKNOWN").lower()
        clinical_map = {
            "active": "active",
            "resolved": "resolved",
            "on_hold": "active",  # ON_HOLD has no direct equivalent; active is closest
            "unknown": "active",  # Condition requires clinicalStatus in practice
        }
        clinical_code = clinical_map.get(status_value, "active")
        clinical_status = {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": clinical_code,
                }
            ]
        }

        # Condition.code is required. Build from code/coding_system, with
        # title as fallback text.
        condition_code: dict = {"text": self.title}
        if self.code:
            system_url = self.coding_system.fhir_system if self.coding_system else None
            coding = [{"code": self.code}]
            if system_url:
                coding[0]["system"] = system_url
            condition_code["coding"] = coding

        # Abatement: only set if resolved (otherwise Condition is still active).
        abatement_datetime = None
        if self.resolved_date:
            abatement_datetime = fhir_isoformat(self.resolved_date)

        data = {
            "resourceType": "Condition",
            "id": str(self.id) if self.id else None,
            "clinicalStatus": clinical_status,
            "code": condition_code,
            "subject": {"reference": f"Patient/{self.patient_id}"}
            if self.patient_id
            else None,
            "onsetDateTime": fhir_isoformat(self.onset_date)
            if self.onset_date
            else None,
            "abatementDateTime": abatement_datetime,
            "recordedDate": fhir_isoformat(self.created_at)
            if self.created_at
            else None,
            "note": [{"text": self.description}] if self.description else None,
            "meta": build_meta(str(self.id) if self.id else None),
        }
        return build_fhir_resource("Condition", data)


class EventAnatomyLink(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "event_anatomy_links"

    event_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinical_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    anatomy_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("anatomy_structures.id", ondelete="CASCADE"),
        nullable=False,
    )

    # E.g., 'primary_site', 'radiates_to'
    relation_type = Column(String(50), nullable=True)

    __table_args__ = (
        Index("idx_event_anatomy_link", "event_id", "anatomy_id", unique=True),
    )
