from sqlalchemy import Column, String, DateTime, Enum, Text, ForeignKey, Date
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.models.base import (
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
)
from app.models.enums import MedicationStatus


class MedicationCatalog(Base, UUIDMixin, TimestampMixin, AuditMixin):
    __tablename__ = "medication_catalog"

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    indications = Column(Text, nullable=True)  # "What it cures"
    side_effects = Column(JSONB, nullable=True)  # List of strings
    contraindications = Column(Text, nullable=True)  # Allergies info, etc.
    dosage_info = Column(Text, nullable=True)

    tenant_id = Column(
        UUID(as_uuid=True), nullable=True, index=True
    )  # NULL means system-wide default

    @property
    def is_custom(self):
        return self.tenant_id is not None

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "indications": self.indications,
            "side_effects": self.side_effects or [],
            "contraindications": self.contraindications,
            "dosage_info": self.dosage_info,
            "is_custom": self.is_custom,
        }


class Medication(
    Base, UUIDMixin, TenantMixin, TimestampMixin, AuditMixin, VersionedMixin
):
    __tablename__ = "fhir_medications"

    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    examination_id = Column(
        UUID(as_uuid=True),
        ForeignKey("examinations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    status = Column(
        Enum(MedicationStatus),
        default=MedicationStatus.ACTIVE,
        nullable=False,
    )

    # The medication information
    # {"text": "Aspirin", "catalog_id": "..."}
    code = Column(JSONB, nullable=False)

    # Timeline
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)

    # Dosage & Frequency
    dosage = Column(String(255), nullable=True)
    frequency = Column(JSONB, nullable=True)

    # Documentation
    reason = Column(Text, nullable=True)
    note = Column(Text, nullable=True)

    # For FHIR compatibility
    subject = Column(JSONB, nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "patient_id": str(self.patient_id),
            "tenant_id": str(self.tenant_id),
            "status": self.status.value,
            "code": self.code,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "dosage": self.dosage,
            "frequency": self.frequency,
            "reason": self.reason,
            "note": self.note,
        }
