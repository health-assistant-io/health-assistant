from sqlalchemy import Column, String, Date, ForeignKey, Index, Text, Integer, Boolean
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import Base, UUIDMixin, AuditMixin, VersionedMixin, TimestampMixin
from app.models.associations import examination_doctors
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from app.models.doctor_model import DoctorModel
    from app.models.examination_category import ExaminationCategory


class ExaminationModel(Base, UUIDMixin, AuditMixin, VersionedMixin, TimestampMixin):
    __tablename__ = "examinations"

    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="SET NULL"),
        nullable=True,
    )
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    examination_date = Column(Date, nullable=True, index=True)
    notes = Column(Text, nullable=True)  # Clinician/Doctor notes
    patient_notes = Column(Text, nullable=True)
    category_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("examination_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    organization_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_integration_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_integrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    external_id = Column(String, nullable=True, index=True)
    auto_extract_metadata = Column(Boolean, nullable=True, default=False)

    @property
    def category(self) -> str | None:
        """Dynamic access to category name via relationship"""
        return self.category_entity.name if self.category_entity else None

    # Cumulative extraction fields
    diagnoses = Column(JSONB, nullable=True, default=list)
    impressions = Column(Text, nullable=True)
    extraction_status = Column(String(50), nullable=True)
    extraction_progress = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

    # Relationship to Doctors
    doctors = relationship(
        "DoctorModel",
        secondary=examination_doctors,
        back_populates="examinations",
        lazy="selectin",
    )

    category_entity = relationship(
        "ExaminationCategory",
        back_populates="examinations",
        lazy="selectin",
    )

    organization = relationship(
        "OrganizationModel",
        back_populates="examinations",
        lazy="selectin",
    )

    # Relationship to clinical data
    documents = relationship(
        "DocumentModel",
        backref="examination",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    medications = relationship(
        "Medication",
        backref="examination",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    observations = relationship(
        "Observation",
        backref="examination",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("idx_exam_tenant_patient", "tenant_id", "patient_id"),)

    def to_dict(self) -> dict:
        updated_at_value = getattr(self, "updated_at", None)
        created_at_value = getattr(self, "created_at", None)
        return {
            "id": str(self.id) if self.id else None,
            "patient_id": str(self.patient_id) if self.patient_id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "examination_date": self.examination_date.isoformat()
            if self.examination_date
            else None,
            "notes": self.notes,
            "patient_notes": self.patient_notes,
            "category_id": str(self.category_id) if self.category_id else None,
            "category_details": self.category_entity.to_dict()
            if self.category_entity
            else None,
            "category": self.category,
            "organization_id": str(self.organization_id)
            if self.organization_id
            else None,
            "organization": self.organization.to_dict() if self.organization else None,
            "source_integration_id": str(self.source_integration_id) if self.source_integration_id else None,
            "external_id": self.external_id,
            "auto_extract_metadata": self.auto_extract_metadata,
            "diagnoses": self.diagnoses,
            "impressions": self.impressions,
            "extraction_status": self.extraction_status,
            "extraction_progress": self.extraction_progress,
            "error_message": self.error_message,
            "medications": [m.to_dict() for m in self.medications]
            if self.medications
            else [],
            "observations": [o.to_dict() for o in self.observations]
            if self.observations
            else [],
            "doctors": [d.to_dict() for d in self.doctors] if self.doctors else [],
            "created_at": created_at_value.isoformat() if created_at_value else None,
            "updated_at": updated_at_value.isoformat() if updated_at_value else None,
        }
