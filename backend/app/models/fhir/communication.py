"""FHIR R4B Communication resource model.

Audit item C15: clinical Communication is distinct from push notifications.
The existing ``notifications`` table is push-infra (VAPID, web push,
triggers); Communication is a clinical messaging resource. They can be
linked via ``notification.communication_id`` (optional FK) but not merged.

The audit also calls out the ``Notification.fhir_resource_type = "Communication"``
column hack — that column was never honored (Notification has no
to_fhir_dict()). The migration adds the proper fhir_communications table;
the column on notifications is left in place for backward-compat (we don't
drop columns in P1).

Spec: https://hl7.org/fhir/R4/communication.html
"""
from sqlalchemy import Column, String, ForeignKey, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB

from app.models.base import (
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
)
from app.services.fhir_helpers import build_fhir_resource, build_meta, fhir_isoformat


class CommunicationModel(
    Base,
    UUIDMixin,
    TenantMixin,
    AuditMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    """A FHIR Communication resource representing a clinical message."""

    __tablename__ = "fhir_communications"

    status = Column(String(50), nullable=False, default="completed")
    # preparation | in-progress | not-done | on-hold | stopped | completed | entered-in-error | unknown

    category = Column(JSONB, nullable=True)  # [CodeableConcept]
    priority = Column(String(50), nullable=True)  # routine | urgent | asap | stat

    subject_patient_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fhir_patients.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    encounter_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("examinations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    topic = Column(JSONB, nullable=True)  # CodeableConcept
    payload = Column(JSONB, nullable=True)  # [{contentString | contentAttachment | contentReference}]

    sent = Column(DateTime(timezone=True), nullable=True)
    received = Column(DateTime(timezone=True), nullable=True)

    sender = Column(JSONB, nullable=True)  # {reference: "Practitioner/uuid"}
    recipient = Column(JSONB, nullable=True)  # [{reference: "Patient/uuid"}]

    __table_args__ = (
        # FHIR sort: Communication?_sort=sent
        Index("ix_fhir_communications_sent", "sent"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id) if self.id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "status": self.status,
            "category": self.category,
            "priority": self.priority,
            "subject_patient_id": str(self.subject_patient_id) if self.subject_patient_id else None,
            "encounter_id": str(self.encounter_id) if self.encounter_id else None,
            "topic": self.topic,
            "payload": self.payload,
            "sent": self.sent.isoformat() if self.sent else None,
            "received": self.received.isoformat() if self.received else None,
            "sender": self.sender,
            "recipient": self.recipient,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }

    def to_fhir_dict(self) -> dict:
        """Serialize to a FHIR R4B Communication resource via fhir.resources (validated)."""
        data = {
            "resourceType": "Communication",
            "id": str(self.id) if self.id else None,
            "status": self.status,
            "category": self.category,
            "priority": self.priority,
            "topic": self.topic,
            "payload": self.payload,
            "sent": fhir_isoformat(self.sent),
            "received": fhir_isoformat(self.received),
            "sender": self.sender,
            "recipient": self.recipient,
            "meta": build_meta(str(self.id) if self.id else None),
        }
        if self.subject_patient_id:
            data["subject"] = {"reference": f"Patient/{self.subject_patient_id}"}
        if self.encounter_id:
            data["encounter"] = {"reference": f"Encounter/{self.encounter_id}"}
        return build_fhir_resource("Communication", data)
