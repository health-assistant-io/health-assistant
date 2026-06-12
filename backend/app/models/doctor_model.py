from sqlalchemy import Column, String, ForeignKey, Table, UUID, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from app.models.base import Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin
from app.models.associations import examination_doctors, organization_doctors


class DoctorModel(Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin):
    __tablename__ = "doctors"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name = Column(String(255), nullable=False)
    specialty = Column(String(255), nullable=True)
    license_number = Column(String(100), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)

    # Enriched contact & office info (FHIR-aligned)
    telecom = Column(JSONB, nullable=True)  # List of ContactPoints
    address = Column(JSONB, nullable=True)  # Structured address
    office_number = Column(String(50), nullable=True)
    office_details = Column(Text, nullable=True)

    # Relationships
    examinations = relationship(
        "ExaminationModel", secondary=examination_doctors, back_populates="doctors"
    )

    organizations = relationship(
        "OrganizationModel", secondary=organization_doctors, back_populates="doctors"
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "name": self.name,
            "specialty": self.specialty,
            "license_number": self.license_number,
            "email": self.email,
            "phone": self.phone,
            "telecom": self.telecom,
            "address": self.address,
            "office_number": self.office_number,
            "office_details": self.office_details,
        }
