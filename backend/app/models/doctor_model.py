from sqlalchemy import Column, String, ForeignKey, UUID, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import relationship
from app.models.base import Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin
from app.models.associations import examination_doctors, organization_doctors
from app.services.fhir_helpers import build_fhir_resource, build_meta


class DoctorModel(Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin):
    __tablename__ = "doctors"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name = Column(String(255), nullable=False)
    specialty_concept_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    license_number = Column(String(100), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)

    # Enriched contact & office info (FHIR-aligned)
    telecom = Column(JSONB, nullable=True)  # List of ContactPoints
    address = Column(JSONB, nullable=True)  # Structured address
    office_number = Column(String(50), nullable=True)
    office_details = Column(Text, nullable=True)

    # Relationships
    specialty_concept = relationship("Concept", lazy="selectin")
    examinations = relationship(
        "ExaminationModel", secondary=examination_doctors, back_populates="doctors"
    )

    organizations = relationship(
        "OrganizationModel", secondary=organization_doctors, back_populates="doctors"
    )

    @property
    def specialty(self):
        """Convenience accessor returning the concept name (backward compat)."""
        return self.specialty_concept.name if self.specialty_concept else None

    def to_dict(self):
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "name": self.name,
            "specialty_concept_id": str(self.specialty_concept_id)
            if self.specialty_concept_id
            else None,
            "specialty": self.specialty,
            "license_number": self.license_number,
            "email": self.email,
            "phone": self.phone,
            "telecom": self.telecom,
            "address": self.address,
            "office_number": self.office_number,
            "office_details": self.office_details,
        }

    def to_fhir_dict(self) -> dict:
        """Serialize to a FHIR R4B Practitioner resource via fhir.resources (validated)."""
        name = self.name or ""
        name_parts = name.split(" ", 1)
        given = [name_parts[0]] if name_parts[0] else []
        family = name_parts[1] if len(name_parts) > 1 else (name_parts[0] or None)

        telecom = []
        if self.email:
            telecom.append({"system": "email", "value": self.email})
        if self.phone:
            telecom.append({"system": "phone", "value": self.phone})
        if self.telecom:
            telecom = self.telecom

        qualifications = []
        specialty_name = self.specialty
        if specialty_name or self.license_number:
            q = {}
            if specialty_name:
                q["code"] = {"text": specialty_name}
            if self.license_number:
                q["identifier"] = [{"value": self.license_number}]
            qualifications.append(q)

        return build_fhir_resource(
            "Practitioner",
            {
                "resourceType": "Practitioner",
                "id": str(self.id),
                "name": [{"family": family, "given": given, "text": name}]
                if name
                else None,
                "qualification": qualifications or None,
                "telecom": telecom or None,
                "address": self.address,
                "meta": build_meta(str(self.id)),
            },
        )
