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
from app.models.enums import AllergyCategory, AllergyCriticality, AllergyClinicalStatus


class AllergyCatalog(Base, UUIDMixin, TimestampMixin, AuditMixin):
    __tablename__ = "allergy_catalog"

    name = Column(String(255), nullable=False)
    category = Column(Enum(AllergyCategory), nullable=False)
    description = Column(Text, nullable=True)
    typical_reactions = Column(JSONB, nullable=True)  # List of common symptoms
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
            "category": self.category.value if self.category else "other",
            "description": self.description,
            "typical_reactions": self.typical_reactions,
            "is_custom": self.is_custom,
        }


class AllergyIntolerance(
    Base, UUIDMixin, TenantMixin, TimestampMixin, AuditMixin, VersionedMixin
):
    __tablename__ = "fhir_allergy_intolerances"

    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    clinical_status = Column(
        Enum(AllergyClinicalStatus),
        default=AllergyClinicalStatus.ACTIVE,
        nullable=False,
    )
    verification_status = Column(String(50), default="confirmed")

    category = Column(Enum(AllergyCategory), nullable=True)
    criticality = Column(Enum(AllergyCriticality), nullable=True)

    # The substance/allergen
    code = Column(JSONB, nullable=False)  # {"text": "Peanuts", "catalog_id": "..."}

    # Timeline
    onset_date = Column(DateTime, nullable=True)
    resolved_date = Column(DateTime, nullable=True)
    last_occurrence = Column(DateTime, nullable=True)

    # Documentation
    note = Column(Text, nullable=True)

    # Structured reactions history
    # List of [{"manifestation": "Hives", "severity": "mild", "date": "..."}]
    reactions = Column(JSONB, nullable=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "patient_id": str(self.patient_id),
            "tenant_id": str(self.tenant_id),
            "clinical_status": self.clinical_status.value,
            "verification_status": self.verification_status,
            "category": self.category.value if self.category else None,
            "criticality": self.criticality.value if self.criticality else None,
            "code": self.code,
            "onset_date": self.onset_date.isoformat() if self.onset_date else None,
            "resolved_date": self.resolved_date.isoformat()
            if self.resolved_date
            else None,
            "last_occurrence": self.last_occurrence.isoformat()
            if self.last_occurrence
            else None,
            "note": self.note,
            "reactions": self.reactions or [],
        }
