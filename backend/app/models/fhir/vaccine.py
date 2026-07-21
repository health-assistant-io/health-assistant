"""Vaccine catalog + patient immunization models (Phase 5).

Mirrors the medication pattern: ``VaccineCatalog`` is the canonical reference
definition (the vaccine product, CVX-coded); ``PatientImmunization`` is the
patient-instance record (a dose administered). Both project to FHIR R4:

- ``VaccineCatalog.to_fhir_dict()`` → FHIR ``Medication`` (the product).
- ``PatientImmunization.to_fhir_dict()`` → FHIR ``Immunization`` (the dose).

See ``dev/plans/unified-catalog-architecture-2026-07-08.md`` Phase 5.
"""

from sqlalchemy import Column, String, Text, Enum, DateTime, ForeignKey, Index
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
from app.models.enums import ImmunizationStatus, CatalogScope
from app.services.fhir_helpers import (
    _enum_value,
    build_fhir_resource,
    build_meta,
    fhir_isoformat,
)


class VaccineCatalog(Base, UUIDMixin, TimestampMixin, AuditMixin):
    """Canonical vaccine reference definition (CVX-coded).

    ``tenant_id`` nullable — ``NULL`` = system-wide default, non-null = tenant
    override. ``class_concept_id`` is the taxonomy hook (a ``vaccine_class``
    concept), following the ``<role>_concept_id`` convention.
    """

    __tablename__ = "vaccine_catalog"

    slug = Column(String(255), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    coding_system = Column(String(50), nullable=True, default="cvx")
    code = Column(String(50), nullable=True)  # CVX code
    target_diseases = Column(JSONB, nullable=True)  # list of disease concept slugs
    dose_schedule = Column(JSONB, nullable=True)  # {doses, intervals}
    contraindications = Column(Text, nullable=True)
    side_effects = Column(JSONB, nullable=True)  # list of strings

    class_concept_id = Column(
        UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,  # NULL = system-wide default
        index=True,
    )

    scope = Column(
        Enum(CatalogScope, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=CatalogScope.SYSTEM,
        index=True,
    )

    class_concept = relationship(
        "Concept",
        foreign_keys="[VaccineCatalog.class_concept_id]",
        lazy="selectin",
    )

    @property
    def is_custom(self):
        return self.tenant_id is not None

    def to_dict(self):
        return {
            "id": str(self.id),
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "coding_system": self.coding_system,
            "code": self.code,
            "target_diseases": self.target_diseases or [],
            "dose_schedule": self.dose_schedule,
            "contraindications": self.contraindications,
            "side_effects": self.side_effects or [],
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
        """Project to a FHIR R4B ``Medication`` (the vaccine product)."""
        coding = (
            [{"system": "http://hl7.org/fhir/sid/cvx", "code": self.code}]
            if self.code
            else None
        )
        return build_fhir_resource(
            "Medication",
            {
                "resourceType": "Medication",
                "id": str(self.id),
                "code": {"coding": coding, "text": self.name} if self.name else None,
                "status": "active",
                "meta": build_meta(str(self.id)),
            },
        )


class PatientImmunization(
    Base,
    UUIDMixin,
    TenantMixin,
    TimestampMixin,
    AuditMixin,
    VersionedMixin,
    SoftDeleteMixin,
):
    """A vaccine dose administered to a patient (FHIR ``Immunization``)."""

    __tablename__ = "patient_immunizations"

    patient_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vaccine_catalog_id = Column(
        UUID(as_uuid=True),
        ForeignKey("vaccine_catalog.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    examination_id = Column(
        UUID(as_uuid=True),
        ForeignKey("examinations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    status = Column(
        Enum(
            ImmunizationStatus,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=ImmunizationStatus.COMPLETED,
        nullable=False,
    )
    # Denormalized vaccine code/text so the record stands alone if the catalog
    # row is later removed (catalog FK is SET NULL).
    vaccine_code = Column(
        JSONB, nullable=False
    )  # {"text", "coding":[...], "catalog_id"?}
    administered_at = Column(DateTime(timezone=True), nullable=True)
    dose_number = Column(String(20), nullable=True)  # e.g. "1", "2", "booster"
    lot_number = Column(String(100), nullable=True)
    manufacturer = Column(String(255), nullable=True)
    location = Column(String(255), nullable=True)
    note = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_patient_immunizations_administered_at", "administered_at"),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "patient_id": str(self.patient_id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "vaccine_catalog_id": str(self.vaccine_catalog_id)
            if self.vaccine_catalog_id
            else None,
            "examination_id": str(self.examination_id)
            if self.examination_id
            else None,
            "status": _enum_value(self.status, "completed"),
            "vaccine_code": self.vaccine_code,
            "administered_at": self.administered_at.isoformat()
            if self.administered_at
            else None,
            "dose_number": self.dose_number,
            "lot_number": self.lot_number,
            "manufacturer": self.manufacturer,
            "location": self.location,
            "note": self.note,
        }

    def to_fhir_dict(self) -> dict:
        """Project to a FHIR R4B ``Immunization`` resource."""
        status = _enum_value(self.status, "completed")
        code = self.vaccine_code or {}
        vaccine_code = {"text": code.get("text")} if isinstance(code, dict) else None
        if isinstance(code, dict) and code.get("coding"):
            vaccine_code = {"text": code.get("text"), "coding": code.get("coding")}

        data = {
            "resourceType": "Immunization",
            "id": str(self.id),
            "status": status,
            "vaccineCode": vaccine_code,
            "patient": {"reference": f"Patient/{self.patient_id}"}
            if self.patient_id
            else None,
            "encounter": {"reference": f"Encounter/{self.examination_id}"}
            if self.examination_id
            else None,
            "occurrenceDateTime": fhir_isoformat(self.administered_at),
            "lotNumber": self.lot_number,
            "manufacturer": {"display": self.manufacturer}
            if self.manufacturer
            else None,
            "location": {"display": self.location} if self.location else None,
            "note": [{"text": self.note}] if self.note else None,
            "meta": build_meta(str(self.id)),
        }
        # FHIR R4 Immunization.protocolApplied.doseNumber is a PositiveInt;
        # only emit it when dose_number parses as one (e.g. "1", "2").
        if self.dose_number and self.dose_number.isdigit():
            data["protocolApplied"] = [{"doseNumberPositiveInt": int(self.dose_number)}]
        return build_fhir_resource("Immunization", data)
