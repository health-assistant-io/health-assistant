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
)
import enum
from app.models.enums import ClinicalEventStatus, CodingSystem


class ClinicalEventCategory(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "clinical_event_categories"

    name = Column(String(100), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    icon = Column(JSONB, nullable=True)  # { "type": "lucide", "value": "Activity" }
    color = Column(String(50), nullable=True)

    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,  # Nullable for global categories
        index=True,
    )

    # Relationships
    event_types = relationship("ClinicalEventType", back_populates="category_entity")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id) if self.id else None,
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
        }


class ClinicalEventType(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "clinical_event_types"

    name = Column(String(100), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    icon = Column(JSONB, nullable=True)  # { "type": "lucide", "value": "Activity" }
    color = Column(String(50), nullable=True)
    metadata_schema = Column(JSONB, nullable=True)  # { "fields": [...] }

    category_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinical_event_categories.id", ondelete="SET NULL"),
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
    category_entity = relationship(
        "ClinicalEventCategory", back_populates="event_types"
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
            "category_id": str(self.category_id) if self.category_id else None,
            "category": self.category_entity.to_dict()
            if self.category_entity
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
    Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin, TimestampMixin
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
