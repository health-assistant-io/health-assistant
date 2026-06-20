from sqlalchemy import Column, String, Enum, Text, ForeignKey, Date
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
from app.services.fhir_helpers import _enum_value, _normalize_timing, build_fhir_resource, build_meta


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
            "status": _enum_value(self.status),
            "code": self.code,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "dosage": self.dosage,
            "frequency": self.frequency,
            "reason": self.reason,
            "note": self.note,
        }

    def to_fhir_dict(self) -> dict:
        """Serialize to a FHIR R4B MedicationStatement resource via fhir.resources (validated)."""
        status = _enum_value(self.status, "active").lower()
        code = self.code or {}
        med_cc = {"text": code.get("text")} if isinstance(code, dict) else None
        if isinstance(code, dict) and code.get("coding"):
            med_cc = {"text": code.get("text"), "coding": code.get("coding")}

        dosage = []
        dose_entry = {}
        if self.dosage:
            dose_entry["text"] = self.dosage
        if self.frequency:
            dose_entry["timing"] = _normalize_timing(self.frequency)
        if dose_entry:
            dosage.append(dose_entry)

        effective = {}
        if self.start_date or self.end_date:
            effective = {
                "start": self.start_date.isoformat() if self.start_date else None,
                "end": self.end_date.isoformat() if self.end_date else None,
            }

        return build_fhir_resource(
            "MedicationStatement",
            {
                "resourceType": "MedicationStatement",
                "id": str(self.id),
                "status": status,
                "medicationCodeableConcept": med_cc,
                "subject": {"reference": f"Patient/{self.patient_id}"}
                if self.patient_id
                else None,
                "effectivePeriod": effective or None,
                "dosage": dosage or None,
                "reasonCode": [{"text": self.reason}] if self.reason else None,
                "note": [{"text": self.note}] if self.note else None,
                "meta": build_meta(str(self.id)),
            },
        )
