from sqlalchemy import Column, String, Boolean, ForeignKey, Index, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import relationship
from app.models.base import Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin
from app.models.enums import OrganizationType
from app.services.fhir_helpers import _as_list, build_fhir_resource, build_meta


class OrganizationModel(Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin):
    __tablename__ = "fhir_organizations"

    # FHIR Organization resource fields
    active = Column(Boolean, default=True)
    type = Column(JSONB, nullable=True)  # FHIR CodeableConcept (Hospital, Insurance, etc.)
    org_type = Column(
        SQLEnum(OrganizationType),
        nullable=False,
        default=OrganizationType.HOUSEHOLD,
        index=True,
    )
    name = Column(String(255), nullable=False)
    alias = Column(JSONB, nullable=True)
    telecom = Column(JSONB, nullable=True)
    address = Column(JSONB, nullable=True)

    # Hierarchy
    part_of_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_organizations.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Contact information (FHIR Contact)
    contact = Column(JSONB, nullable=True)

    # Relationships
    part_of = relationship(
        "OrganizationModel",
        remote_side="OrganizationModel.id",
        foreign_keys="[OrganizationModel.part_of_id]",
        backref="departments",
    )

    doctors = relationship(
        "DoctorModel", secondary="organization_doctors", back_populates="organizations"
    )

    examinations = relationship("ExaminationModel", back_populates="organization")

    def to_dict(self):
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "active": self.active,
            "type": self.type,
            "org_type": self.org_type.value if self.org_type else None,
            "name": self.name,
            "alias": self.alias,
            "telecom": self.telecom,
            "address": self.address,
            "part_of_id": str(self.part_of_id) if self.part_of_id else None,
            "contact": self.contact,
        }

    def to_fhir_dict(self) -> dict:
        """Serialize to a FHIR R4B Organization resource via fhir.resources (validated)."""
        return build_fhir_resource(
            "Organization",
            {
                "resourceType": "Organization",
                "id": str(self.id),
                "active": self.active,
                "type": _as_list(self.type),
                "name": self.name,
                "alias": self.alias,
                "telecom": self.telecom,
                "address": self.address,
                "partOf": {"reference": f"Organization/{self.part_of_id}"}
                if self.part_of_id
                else None,
                "contact": self.contact,
                "meta": build_meta(str(self.id)),
            },
        )

    __table_args__ = (Index("idx_organization_tenant_name", "tenant_id", "name"),)
