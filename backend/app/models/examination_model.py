from sqlalchemy import Column, String, Date, ForeignKey, Index, Text, Integer, Boolean, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship
from app.models.base import (
    Base,
    UUIDMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
)
from app.models.associations import examination_doctors
from app.services.fhir_helpers import build_fhir_resource, build_meta, fhir_isoformat
from typing import TYPE_CHECKING
import datetime as _dt
from datetime import timezone

if TYPE_CHECKING:
    pass


class ExaminationModel(
    Base, UUIDMixin, AuditMixin, VersionedMixin, TimestampMixin, SoftDeleteMixin
):
    __tablename__ = "examinations"

    patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=True,
    )
    tenant_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    examination_date = Column(Date, nullable=True, index=True)
    notes = Column(Text, nullable=True)  # Clinician/Doctor notes
    patient_notes = Column(Text, nullable=True)
    category_concept_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    organization_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_integration_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_integrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    external_id = Column(String, nullable=True, index=True)
    auto_extract_metadata = Column(Boolean, nullable=True, default=False)

    @property
    def category(self) -> str | None:
        """Dynamic access to category name via relationship"""
        return self.category_concept.name if self.category_concept else None

    # Cumulative extraction fields
    diagnoses = Column(JSONB, nullable=True, default=list)
    impressions = Column(Text, nullable=True)
    extraction_status = Column(String(50), nullable=True)
    extraction_progress = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

    # Relationship to Doctors
    doctors = relationship(
        "DoctorModel",
        secondary=examination_doctors,
        back_populates="examinations",
        lazy="selectin",
    )

    category_concept = relationship(
        "Concept",
        foreign_keys="[ExaminationModel.category_concept_id]",
        lazy="selectin",
    )

    organization = relationship(
        "OrganizationModel",
        back_populates="examinations",
        lazy="selectin",
    )

    # Relationship to clinical data
    documents = relationship(
        "DocumentModel",
        backref="examination",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    medications = relationship(
        "Medication",
        backref="examination",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    observations = relationship(
        "Observation",
        backref="examination",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_exam_tenant_patient", "tenant_id", "patient_id"),
        CheckConstraint(
            "extraction_progress BETWEEN 0 AND 100",
            name="ck_examinations_extraction_progress_bounds",
        ),
    )

    def to_dict(self) -> dict:
        updated_at_value = getattr(self, "updated_at", None)
        created_at_value = getattr(self, "created_at", None)
        return {
            "id": str(self.id) if self.id else None,
            "patient_id": str(self.patient_id) if self.patient_id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "examination_date": self.examination_date.isoformat()
            if self.examination_date
            else None,
            "notes": self.notes,
            "patient_notes": self.patient_notes,
            "category_concept_id": str(self.category_concept_id)
            if self.category_concept_id
            else None,
            "category_concept": self.category_concept.to_dict()
            if self.category_concept
            else None,
            "category": self.category,
            "organization_id": str(self.organization_id)
            if self.organization_id
            else None,
            "organization": self.organization.to_dict() if self.organization else None,
            "source_integration_id": str(self.source_integration_id)
            if self.source_integration_id
            else None,
            "external_id": self.external_id,
            "auto_extract_metadata": self.auto_extract_metadata,
            "diagnoses": self.diagnoses,
            "impressions": self.impressions,
            "extraction_status": self.extraction_status,
            "extraction_progress": self.extraction_progress,
            "error_message": self.error_message,
            "medications": [m.to_dict() for m in self.medications]
            if self.medications
            else [],
            "observations": [o.to_dict() for o in self.observations]
            if self.observations
            else [],
            "doctors": [d.to_dict() for d in self.doctors] if self.doctors else [],
            "clinical_events": self._serialize_clinical_events(),
            "created_at": created_at_value.isoformat() if created_at_value else None,
            "updated_at": updated_at_value.isoformat() if updated_at_value else None,
        }

    def _serialize_clinical_events(self) -> list:
        """Surface the health journeys this examination belongs to.

        Reads from the ``event_links`` backref (from ``EventExaminationLink``)
        only when it has been eager-loaded — avoids triggering a lazy load
        during sync serialization. Returns ``[]`` otherwise. Capped at 20.
        """
        if "event_links" not in self.__dict__:
            return []
        out = []
        for link in self.event_links[:20]:
            event = getattr(link, "event", None)
            if event is None:
                continue
            out.append(
                {
                    "id": str(event.id) if event.id else None,
                    "title": getattr(event, "title", None),
                    "status": getattr(event.status, "value", None)
                    if getattr(event, "status", None) is not None
                    else None,
                    "reason": getattr(link, "reason", None),
                }
            )
        return out

    def to_fhir_dict(self) -> dict:
        """Project this ExaminationModel to a FHIR R4B Encounter resource.

        The Examination is the app-facing vocabulary for a clinical visit;
        FHIR calls it an Encounter. We project existing fields:

        - patient_id → Encounter.subject
        - examination_date → Encounter.period.start (end = start + same day)
        - notes/patient_notes → Encounter.reasonCode (text) or note
        - organization_id → Encounter.serviceProvider
        - diagnoses (JSONB) → Encounter.diagnosis[] (with display text only;
          a real Condition reference would need a Condition resource)
        - category → Encounter.class Coding (if category exists, else AMB)

        Defaults:
        - status: 'finished' (historical visits already happened)
        - class: 'AMB' (ambulatory; the common case)

        Audit item C8: ExaminationModel gains a FHIR projection so the facade
        can expose it at ``/fhir/R4/Encounter`` without a new table. The model
        name stays ExaminationModel (app concept); 'Examination' is the
        user-facing vocabulary in the frontend, 'Encounter' is the FHIR name.
        """
        # Encounter.status: planned | arrived | triaged | in-progress |
        # onleave | finished | cancelled | entered-in-error | unknown
        status = "finished"

        # Encounter.class: a single Coding from the ActCode system.
        # AMB = ambulatory (the common case for an examination).
        encounter_class = {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
            "display": "ambulatory",
        }
        if self.category_concept and self.category_concept.slug:
            # Map a few common category slugs to ActCode; default to AMB.
            slug_map = {
                "hospital": "IMP",
                "emergency": "EMER",
                "telemedicine": "TELE",
                "home": "HH",
            }
            code = slug_map.get(self.category_concept.slug, "AMB")
            encounter_class["code"] = code

        # Period: examination_date (a Date) → period.start at midnight UTC;
        # period.end = same day at 23:59:59 UTC.
        period = None
        if self.examination_date:
            start_dt = _dt.datetime.combine(
                self.examination_date, _dt.time(0, 0, 0), tzinfo=timezone.utc
            )
            end_dt = _dt.datetime.combine(
                self.examination_date, _dt.time(23, 59, 59), tzinfo=timezone.utc
            )
            period = {
                "start": fhir_isoformat(start_dt),
                "end": fhir_isoformat(end_dt),
            }

        # Reason: prefer notes, fall back to patient_notes.
        reason_code = None
        if self.notes or self.patient_notes:
            text = self.notes or self.patient_notes
            reason_code = [{"text": text[:200]}]

        # Diagnosis references: diagnoses is a JSONB list of {text, code, system}
        # structures; we surface them as display-only (no Condition reference
        # unless a ClinicalEvent row is linked via EventExaminationLink).
        diagnosis = None
        if self.diagnoses and isinstance(self.diagnoses, list):
            diagnosis = []
            for d in self.diagnoses:
                if isinstance(d, dict) and d.get("text"):
                    diagnosis.append(
                        {
                            "condition": {"display": d["text"]},
                            "use": {
                                "coding": [
                                    {
                                        "system": "http://terminology.hl7.org/CodeSystem/diagnosis-role",
                                        "code": "AD",
                                        "display": "Admission diagnosis",
                                    }
                                ]
                            },
                        }
                    )
            if not diagnosis:
                diagnosis = None

        # Close the documented gap: when ClinicalEvent rows are linked via
        # EventExaminationLink (eager-loaded), emit real Condition references
        # so the Encounter is properly wired to the patient's problem list.
        # Previously the comment promised this but it was never implemented.
        # Guard on __dict__ to avoid a lazy load during facade bulk search.
        if "event_links" in self.__dict__:
            for link in self.event_links:
                event = getattr(link, "event", None)
                if event is None or event.id is None:
                    continue
                diagnosis = diagnosis or []
                diagnosis.append(
                    {
                        "condition": {"reference": f"Condition/{event.id}"},
                        "use": {
                            "coding": [
                                {
                                    "system": "http://terminology.hl7.org/CodeSystem/diagnosis-role",
                                    "code": "CC",
                                    "display": "Chief complaint",
                                }
                            ]
                        },
                    }
                )

        data = {
            "resourceType": "Encounter",
            "id": str(self.id) if self.id else None,
            "status": status,
            "class": encounter_class,
            "subject": {"reference": f"Patient/{self.patient_id}"}
            if self.patient_id
            else None,
            "period": period,
            "reasonCode": reason_code,
            "diagnosis": diagnosis,
            "serviceProvider": {"reference": f"Organization/{self.organization_id}"}
            if self.organization_id
            else None,
            "meta": build_meta(str(self.id) if self.id else None),
        }
        return build_fhir_resource("Encounter", data)
