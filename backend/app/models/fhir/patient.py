from sqlalchemy import (
    Column,
    String,
    Date,
    DateTime,
    Enum,
    Boolean,
    Index,
    Float,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import relationship
from app.models.base import Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin, TimestampMixin
from app.models.enums import Gender


class Patient(Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin, TimestampMixin):
    __tablename__ = "fhir_patients"

    user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # FHIR Patient resource fields
    name = Column(JSONB, nullable=False)
    gender = Column(Enum(Gender), nullable=False)
    birth_date = Column(Date, nullable=True)
    deceased_boolean = Column(Boolean, nullable=True)
    deceased_datetime = Column(DateTime, nullable=True)
    address = Column(JSONB, nullable=True)
    telecom = Column(JSONB, nullable=True)

    # Additional metadata
    mrn = Column(String, nullable=True, unique=True)  # Medical Record Number
    emergency_contact = Column(JSONB, nullable=True)
    dashboard_layout = Column(JSONB, nullable=True)  # Custom dashboard layout

    # Indexes for common queries
    __table_args__ = (
        Index("idx_patient_tenant_mrn", "tenant_id", "mrn"),
        Index("idx_patient_tenant_name", "tenant_id", "name"),
    )

    @property
    def age(self) -> int | None:
        if not self.birth_date:
            return None
        from datetime import date

        today = date.today()
        return (
            today.year
            - self.birth_date.year
            - ((today.month, today.day) < (self.birth_date.month, self.birth_date.day))
        )

    def to_dict(self):
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "name": self.name,
            "gender": self.gender.value if self.gender else None,
            "birth_date": self.birth_date.isoformat() if self.birth_date else None,
            "birthDate": self.birth_date.isoformat() if self.birth_date else None,
            "age": self.age,
            "deceased_boolean": self.deceased_boolean,
            "deceased_datetime": self.deceased_datetime.isoformat()
            if self.deceased_datetime
            else None,
            "address": self.address,
            "telecom": self.telecom,
            "mrn": self.mrn,
            "emergency_contact": self.emergency_contact,
            "dashboard_layout": self.dashboard_layout,
        }


class Observation(Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin, TimestampMixin):
    __tablename__ = "fhir_observations"

    # Custom linkage for Health Assistant
    document_id = Column(String, nullable=True, index=True)
    examination_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("examinations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # FHIR Observation resource fields
    status = Column(String, nullable=False)
    category = Column(JSONB, nullable=True)
    code = Column(JSONB, nullable=False)  # LOINC code
    subject = Column(JSONB, nullable=False)  # Reference to Patient
    effective_datetime = Column(DateTime, nullable=True)
    value_quantity = Column(JSONB, nullable=True)
    value_string = Column(String, nullable=True)
    value_codeableConcept = Column(JSONB, nullable=True)
    reference_range = Column(JSONB, nullable=True)
    interpretation = Column(String, nullable=True)
    comment = Column(String, nullable=True)
    performer = Column(JSONB, nullable=True)

    # New Biomarker Engine Fields
    biomarker_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("biomarker_definitions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    lab_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("laboratories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    method = Column(String(255), nullable=True)
    raw_value = Column(Float, nullable=True)
    raw_unit_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("units.id", ondelete="SET NULL"),
        nullable=True,
    )
    normalized_value = Column(Float, nullable=True)
    relative_score = Column(Float, nullable=True)
    lab_reference_range = Column(JSONB, nullable=True)

    # Relationships
    biomarker = relationship("BiomarkerDefinition", lazy="selectin")
    lab = relationship("Laboratory", lazy="selectin")
    raw_unit = relationship("Unit", lazy="selectin")

    def to_dict(self):
        return {
            "id": str(self.id),
            "status": self.status,
            "category": self.category,
            "code": self.code,
            "effective_datetime": self.effective_datetime.isoformat()
            if self.effective_datetime
            else None,
            "value_quantity": self.value_quantity,
            "value_string": self.value_string,
            "reference_range": self.reference_range,
            "interpretation": self.interpretation,
            "biomarker_id": str(self.biomarker_id) if self.biomarker_id else None,
            "biomarker_slug": self.biomarker.slug if self.biomarker else None,
            "biomarker_info": self.biomarker.info if self.biomarker else None,
            "biomarker_aliases": self.biomarker.aliases if self.biomarker else [],
            "biomarker_reference_range_min": self.biomarker.reference_range_min
            if self.biomarker
            else None,
            "biomarker_reference_range_max": self.biomarker.reference_range_max
            if self.biomarker
            else None,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "lab_reference_range": self.lab_reference_range,
            "normalized_unit": self.biomarker.preferred_unit.symbol
            if (self.biomarker and self.biomarker.preferred_unit)
            else None,
            "relative_score": self.relative_score,
            "method": self.method,
            "examination_id": str(self.examination_id) if self.examination_id else None,
            "document_id": self.document_id,
        }

    # Indexes for common queries
    __table_args__ = (
        Index("idx_observation_tenant_patient", "tenant_id", "subject"),
        Index("idx_observation_tenant_code", "tenant_id", "code"),
        Index("idx_observation_tenant_date", "tenant_id", "effective_datetime"),
    )


class DiagnosticReport(Base, UUIDMixin, TenantMixin, AuditMixin, VersionedMixin, TimestampMixin):
    __tablename__ = "fhir_diagnostic_reports"

    # FHIR DiagnosticReport resource fields
    status = Column(String, nullable=False)
    category = Column(JSONB, nullable=True)
    code = Column(JSONB, nullable=False)
    subject = Column(JSONB, nullable=False)  # Reference to Patient
    effective_datetime = Column(DateTime, nullable=True)
    issued = Column(DateTime, nullable=True)
    performer = Column(JSONB, nullable=True)
    conclusion = Column(String, nullable=True)
    conclusion_code = Column(JSONB, nullable=True)
    presented_form = Column(JSONB, nullable=True)  # PDF attachments

    # Indexes for common queries
    __table_args__ = (
        Index("idx_diagnostic_report_tenant_patient", "tenant_id", "subject"),
        Index("idx_diagnostic_report_tenant_date", "tenant_id", "effective_datetime"),
    )
