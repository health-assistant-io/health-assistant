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
    text,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
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
from app.models.enums import Gender
from app.services.fhir_helpers import (
    _as_list,
    _clean_quantity,
    _coerce_human_name_list,
    _primary_human_name,
    build_fhir_resource,
    build_meta,
)


class Patient(
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
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
    deceased_datetime = Column(DateTime(timezone=True), nullable=True)
    address = Column(JSONB, nullable=True)
    telecom = Column(JSONB, nullable=True)

    # Additional metadata
    mrn = Column(String, nullable=True)  # Medical Record Number (per-tenant unique via index)
    emergency_contact = Column(JSONB, nullable=True)
    dashboard_layout = Column(JSONB, nullable=True)  # Custom dashboard layout

    # Indexes for common queries
    __table_args__ = (
        Index("idx_patient_tenant_mrn", "tenant_id", "mrn"),
        Index("idx_patient_tenant_name", "tenant_id", "name"),
        # FHIR sort: Patient?_sort=birthdate
        Index("ix_fhir_patients_birth_date", "birth_date"),
        # Prevent empty-string MRNs — Postgres treats NULLs as distinct
        # (multiple NULLs are fine) but "" would collide on the unique index.
        # App code (fhir_service / import_service) normalizes "" → None.
        CheckConstraint("mrn IS NULL OR mrn <> ''", name="mrn_not_empty"),
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
            "name": _primary_human_name(self.name),
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

    def to_fhir_dict(self) -> dict:
        """Serialize to a FHIR R4B Patient resource via fhir.resources (validated)."""
        identifiers = []
        if self.mrn:
            identifiers.append(
                {"system": "urn:healthassistant:mrn", "value": str(self.mrn)}
            )
        return build_fhir_resource(
            "Patient",
            {
                "resourceType": "Patient",
                "id": str(self.id),
                "identifier": identifiers or None,
                "name": _coerce_human_name_list(self.name),
                "gender": self.gender.value.lower() if self.gender else None,
                "birthDate": self.birth_date.isoformat() if self.birth_date else None,
                "deceasedBoolean": self.deceased_boolean,
                "deceasedDateTime": self.deceased_datetime.isoformat()
                if self.deceased_datetime
                else None,
                "address": self.address,
                "telecom": self.telecom,
                "meta": build_meta(str(self.id)),
            },
        )


class Observation(
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    __tablename__ = "fhir_observations"

    # Custom linkage for Health Assistant
    document_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Patient FK maintained from the ``subject`` JSONB reference (audit B3).
    # Kept in sync on every write path; enables ON DELETE CASCADE + btree
    # patient scoping without parsing JSONB on every read. ``subject`` remains
    # the FHIR-serialization projection.
    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
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
    effective_datetime = Column(DateTime(timezone=True), nullable=True)
    value_quantity = Column(JSONB, nullable=True)
    value_string = Column(String, nullable=True)
    value_codeableConcept = Column(JSONB, nullable=True)
    reference_range = Column(JSONB, nullable=True)
    interpretation = Column(JSONB, nullable=True)
    comment = Column(String, nullable=True)
    performer = Column(JSONB, nullable=True)
    component = Column(JSONB, nullable=True)  # FHIR R4 0..* component (BP, panels)

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
        index=True,
    )
    normalized_value = Column(Float, nullable=True)
    relative_score = Column(Float, nullable=True)
    lab_reference_range = Column(JSONB, nullable=True)

    # Relationships
    biomarker = relationship("BiomarkerDefinition", lazy="selectin")
    lab = relationship("Laboratory", lazy="selectin")
    raw_unit = relationship("Unit", lazy="selectin")

    def to_dict(self):
        from app.services.fhir_helpers import _flatten_interpretation

        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "status": self.status,
            "category": self.category,
            "code": self.code,
            "subject": self.subject,
            "effective_datetime": self.effective_datetime.isoformat()
            if self.effective_datetime
            else None,
            "value_quantity": self.value_quantity,
            "value_string": self.value_string,
            "value_codeable_concept": self.value_codeableConcept,
            "reference_range": self.reference_range,
            "interpretation": _flatten_interpretation(self.interpretation),
            "component": self.component,
            "comment": self.comment,
            "performer": self.performer,
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
            "document_id": str(self.document_id) if self.document_id else None,
            "patient_id": str(self.patient_id) if self.patient_id else None,
        }

    def to_fhir_dict(self) -> dict:
        """Serialize to a FHIR R4B Observation resource via fhir.resources (validated)."""
        from app.services.fhir_helpers import _normalize_interpretation, fhir_isoformat

        return build_fhir_resource(
            "Observation",
            {
                "resourceType": "Observation",
                "id": str(self.id),
                "status": (self.status or "final").lower(),
                "category": self.category,
                "code": self.code,
                "subject": self.subject,
                "effectiveDateTime": fhir_isoformat(self.effective_datetime),
                "valueQuantity": _clean_quantity(self.value_quantity),
                "valueString": self.value_string,
                "valueCodeableConcept": self.value_codeableConcept,
                "referenceRange": self.reference_range,
                "interpretation": _normalize_interpretation(self.interpretation),
                "component": self.component,
                "note": [{"text": self.comment}] if self.comment else None,
                "performer": self.performer,
                "method": {"text": self.method} if self.method else None,
                "meta": build_meta(str(self.id)),
            },
        )

    # Indexes for common queries
    __table_args__ = (
        Index("idx_observation_tenant_patient", "tenant_id", "subject"),
        Index("idx_observation_tenant_code", "tenant_id", "code"),
        Index("idx_observation_tenant_date", "tenant_id", "effective_datetime"),
        # Expression index on the extracted JSONB path — the most-used query
        # pattern in the codebase (12+ call sites do
        # ``subject["reference"].astext == "Patient/<uuid>"``). Without this
        # index every per-patient observation query is a full scan within
        # tenant.
        Index(
            "ix_fhir_observations_subject_ref",
            text("(subject->>'reference')"),
        ),
    )


class DiagnosticReport(
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    __tablename__ = "fhir_diagnostic_reports"

    # FHIR DiagnosticReport resource fields
    status = Column(String, nullable=False)
    category = Column(JSONB, nullable=True)
    code = Column(JSONB, nullable=False)
    subject = Column(JSONB, nullable=False)  # Reference to Patient
    # Patient FK maintained from ``subject`` (audit B3) — see Observation.
    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    effective_datetime = Column(DateTime(timezone=True), nullable=True)
    issued = Column(DateTime(timezone=True), nullable=True)
    performer = Column(JSONB, nullable=True)
    conclusion = Column(String, nullable=True)
    conclusion_code = Column(JSONB, nullable=True)
    presented_form = Column(JSONB, nullable=True)  # PDF attachments

    # Indexes for common queries
    __table_args__ = (
        Index("idx_diagnostic_report_tenant_patient", "tenant_id", "subject"),
        Index("idx_diagnostic_report_tenant_date", "tenant_id", "effective_datetime"),
        Index(
            "ix_fhir_diagnostic_reports_subject_ref",
            text("(subject->>'reference')"),
        ),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "status": self.status,
            "category": self.category,
            "code": self.code,
            "subject": self.subject,
            "patient_id": str(self.patient_id) if self.patient_id else None,
            "effective_datetime": self.effective_datetime.isoformat()
            if self.effective_datetime
            else None,
            "issued": self.issued.isoformat() if self.issued else None,
            "performer": self.performer,
            "conclusion": self.conclusion,
            "conclusion_code": self.conclusion_code,
            "presented_form": self.presented_form,
        }

    def to_fhir_dict(self) -> dict:
        """Serialize to a FHIR R4B DiagnosticReport resource via fhir.resources (validated)."""
        from app.services.fhir_helpers import fhir_isoformat

        return build_fhir_resource(
            "DiagnosticReport",
            {
                "resourceType": "DiagnosticReport",
                "id": str(self.id),
                "status": (self.status or "final").lower(),
                "category": _as_list(self.category),
                "code": self.code,
                "subject": self.subject,
                "effectiveDateTime": fhir_isoformat(self.effective_datetime),
                "issued": fhir_isoformat(self.issued),
                "performer": self.performer,
                "conclusion": self.conclusion,
                "conclusionCode": _as_list(self.conclusion_code),
                "presentedForm": _as_list(self.presented_form),
                "meta": build_meta(str(self.id)),
            },
        )
