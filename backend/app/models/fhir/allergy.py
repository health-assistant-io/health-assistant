from sqlalchemy import Column, String, DateTime, Enum, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.models.base import (
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
)
from app.models.enums import AllergyCategory, AllergyCriticality, AllergyClinicalStatus
from app.services.fhir_helpers import (
    _enum_value,
    build_fhir_resource,
    build_meta,
    fhir_isoformat,
)


class AllergyCatalog(Base, UUIDMixin, TimestampMixin, AuditMixin):
    __tablename__ = "allergy_catalog"

    name = Column(String(255), nullable=False)
    category = Column(Enum(AllergyCategory), nullable=False)
    description = Column(Text, nullable=True)
    typical_reactions = Column(JSONB, nullable=True)  # List of common symptoms
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,  # NULL means system-wide default
        index=True,
    )

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
    Base,
    UUIDMixin,
    TenantMixin,
    TimestampMixin,
    AuditMixin,
    VersionedMixin,
    SoftDeleteMixin,
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
    onset_date = Column(DateTime(timezone=True), nullable=True)
    resolved_date = Column(DateTime(timezone=True), nullable=True)
    last_occurrence = Column(DateTime(timezone=True), nullable=True)

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

    def to_fhir_dict(self) -> dict:
        """Serialize to a FHIR R4B AllergyIntolerance resource via fhir.resources (validated)."""
        # Use _enum_value for defensive enum-or-string handling: pre-flush
        # SQLAlchemy rows may carry raw strings assigned at construction
        # (the service passes "ACTIVE" not AllergyClinicalStatus.ACTIVE).
        clinical = _enum_value(self.clinical_status, "").lower()
        verification = (self.verification_status or "confirmed").lower()
        category = _enum_value(self.category, "").lower() or None
        criticality = _enum_value(self.criticality, "").lower() or None

        clinical_status = (
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                        "code": clinical,
                    }
                ]
            }
            if clinical
            else None
        )
        verification_status = (
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                        "code": verification,
                    }
                ]
            }
            if verification
            else None
        )

        code = self.code or {}
        allergy_code = {"text": code.get("text")} if isinstance(code, dict) else None
        if isinstance(code, dict) and code.get("coding"):
            allergy_code = {"text": code.get("text"), "coding": code.get("coding")}

        reactions = []
        for r in self.reactions or []:
            reaction = {}
            if r.get("manifestation"):
                reaction["manifestation"] = [{"text": r["manifestation"]}]
            if r.get("severity"):
                reaction["severity"] = str(r["severity"]).lower()
            if r.get("date"):
                reaction["onset"] = r["date"]
            if reaction:
                reactions.append(reaction)

        return build_fhir_resource(
            "AllergyIntolerance",
            {
                "resourceType": "AllergyIntolerance",
                "id": str(self.id),
                "clinicalStatus": clinical_status,
                "verificationStatus": verification_status,
                "category": [category] if category else None,
                "criticality": criticality,
                "code": allergy_code,
                "patient": {"reference": f"Patient/{self.patient_id}"}
                if self.patient_id
                else None,
                "onsetDateTime": fhir_isoformat(self.onset_date),
                "lastOccurrence": fhir_isoformat(self.last_occurrence),
                "note": [{"text": self.note}] if self.note else None,
                "reaction": reactions or None,
                "meta": build_meta(str(self.id)),
            },
        )
