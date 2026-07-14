from sqlalchemy import Column, String, Enum, Text, ForeignKey, Date, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
from app.models.enums import MedicationIntent, MedicationStatus, CatalogScope
from app.services.fhir_helpers import (
    _enum_value,
    _normalize_timing,
    build_fhir_resource,
    build_meta,
    fhir_isoformat,
)


class MedicationCatalog(Base, UUIDMixin, TimestampMixin, AuditMixin):
    __tablename__ = "medication_catalog"

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    indications = Column(Text, nullable=True)  # "What it cures"
    side_effects = Column(JSONB, nullable=True)  # List of strings
    contraindications = Column(Text, nullable=True)  # Allergies info, etc.
    dosage_info = Column(Text, nullable=True)

    # Taxonomy classification (Phase 2): the drug-class concept (e.g. an ATC
    # class). Follows the established ``class_concept_id`` convention.
    class_concept_id = Column(
        UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,  # NULL means system-wide default
        index=True,
    )

    scope = Column(
        Enum(CatalogScope, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=CatalogScope.SYSTEM,
        index=True,
    )

    # Explicit foreign_keys= so SQLAlchemy resolves the single concept FK
    # unambiguously (mirrors biomarker/anatomy/examination).
    class_concept = relationship(
        "Concept",
        foreign_keys="[MedicationCatalog.class_concept_id]",
        lazy="selectin",
    )

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
            "class_concept_id": str(self.class_concept_id)
            if self.class_concept_id
            else None,
            "class_concept_slug": self.class_concept.slug
            if self.class_concept
            else None,
            "class_concept_name": self.class_concept.name
            if self.class_concept
            else None,
            "scope": self.scope.value if self.scope else "system",
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "created_by": str(self.created_by) if self.created_by else None,
            "is_custom": self.is_custom,
        }

    def to_fhir_dict(self) -> dict:
        """Project this MedicationCatalog row to a FHIR R4B Medication resource.

        MedicationCatalog is the canonical drug definition (the medication
        itself), distinct from Medication (which records what a patient
        takes/was prescribed). Audit item C11: expose MedicationCatalog as
        the FHIR Medication resource.

        Maps:
        - name → Medication.code.text
        - description → Medication.definition.text (or note in newer R4B)
        """
        return build_fhir_resource(
            "Medication",
            {
                "resourceType": "Medication",
                "id": str(self.id),
                "code": {"text": self.name} if self.name else None,
                "status": "active",
                "meta": build_meta(str(self.id)),
            },
        )


class Medication(
    Base,
    UUIDMixin,
    TenantMixin,
    TimestampMixin,
    AuditMixin,
    VersionedMixin,
    SoftDeleteMixin,
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

    # Discriminator: is this a MedicationStatement (what the patient takes)
    # or a MedicationRequest (what was prescribed)? Default is statement.
    # One table serves both FHIR resources via the R4 facade.
    intent = Column(
        Enum(MedicationIntent, values_callable=lambda obj: [e.value for e in obj]),
        default=MedicationIntent.STATEMENT,
        nullable=False,
        index=True,
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

    __table_args__ = (
        # FHIR sort: MedicationStatement?_sort=startdate, MedicationRequest?_sort=authoredon
        Index("ix_fhir_medications_start_date", "start_date"),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "patient_id": str(self.patient_id),
            "tenant_id": str(self.tenant_id),
            "status": _enum_value(self.status),
            "intent": _enum_value(self.intent, "statement"),
            "code": self.code,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "dosage": self.dosage,
            "frequency": self.frequency,
            "reason": self.reason,
            "note": self.note,
        }

    def to_fhir_dict(self) -> dict:
        """Serialize to FHIR R4B based on the ``intent`` discriminator.

        - ``intent == statement`` → MedicationStatement (what the patient takes)
        - ``intent in {order, plan, proposal}`` → MedicationRequest (a prescription)

        Audit items C11 (Medication standalone, handled on MedicationCatalog)
        + C12 (MedicationRequest): one table serves both resources.
        """
        intent_value = _enum_value(self.intent, "statement")
        if intent_value == "statement":
            return self._to_medication_statement()
        return self._to_medication_request()

    def _to_medication_statement(self) -> dict:
        """Emit a FHIR R4B MedicationStatement."""
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

    def _to_medication_request(self) -> dict:
        """Emit a FHIR R4B MedicationRequest."""
        intent_value = _enum_value(self.intent, "order")
        status_map = {
            "active": "active",
            "completed": "completed",
            "cancelled": "cancelled",
            "entered_in_error": "entered-in-error",
            "stopped": "stopped",
            "on_hold": "on-hold",
            "unknown": "unknown",
            "intended": "draft",
            "inactive": "cancelled",
        }
        status = status_map.get(_enum_value(self.status, "active").lower(), "active")

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

        data = {
            "resourceType": "MedicationRequest",
            "id": str(self.id),
            "status": status,
            "intent": intent_value,
            "medicationCodeableConcept": med_cc,
            "subject": {"reference": f"Patient/{self.patient_id}"}
            if self.patient_id
            else None,
            "dosageInstruction": dosage or None,
            "reasonCode": [{"text": self.reason}] if self.reason else None,
            "note": [{"text": self.note}] if self.note else None,
            "meta": build_meta(str(self.id)),
        }

        # authoredOn: use created_at if available.
        if self.created_at:
            data["authoredOn"] = fhir_isoformat(self.created_at)

        # Encounter context.
        if self.examination_id:
            data["encounter"] = {"reference": f"Encounter/{self.examination_id}"}

        return build_fhir_resource("MedicationRequest", data)
