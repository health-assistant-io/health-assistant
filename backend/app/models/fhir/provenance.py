"""FHIR R4B Provenance resource model.

Audit item C10: Provenance records who/when/why for every create/update/
delete on a clinical resource. Unlike the internal ``audit_logs`` table,
Provenance is a FHIR resource that travels with the data on export.

Spec: https://hl7.org/fhir/R4/provenance.html

The model is **immutable** — no SoftDeleteMixin, no VersionedMixin. Once
recorded, a Provenance row never changes. The Provenance-on-write hook
(Phase 7) creates one Provenance per facade POST/PUT/DELETE.
"""

from sqlalchemy import Column, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base, UUIDMixin, TenantMixin, TimestampMixin
from app.services.fhir_helpers import build_fhir_resource, build_meta, fhir_isoformat


# HL7 v3 ProvenanceActivityType codes (the canonical "what happened" codes).
# https://terminology.hl7.org/CodeSystem/v3-ProvenanceEventType
ACTIVITY_CREATE = "CREATE"
ACTIVITY_UPDATE = "UPDATE"
ACTIVITY_DELETE = "DELETE"
ACTIVITY_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ProvenanceEventType"


class ProvenanceModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """A FHIR Provenance resource recording the provenance of a clinical write.

    Immutable: no SoftDeleteMixin (Provenance rows are never deleted) and no
    VersionedMixin (no versioning — append-only by spec).
    """

    __tablename__ = "fhir_provenance"

    # target: JSONB list of References to the resources this Provenance covers.
    # Spec requires 1..* target. Format: [{"reference": "Patient/abc"}]
    target = Column(JSONB, nullable=False)

    # recorded: when the provenance was recorded (server time).
    # Spec: 1..1 instant.
    recorded = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    # activity: CodeableConcept for what happened (CREATE/UPDATE/DELETE).
    # Spec: 0..1 CodeableConcept.
    activity = Column(JSONB, nullable=True)

    # agent: 1..* — who did the action.
    # Format: [{"who": {"reference": "User/uuid"}, "type": {coding: [...]}}]
    agent = Column(JSONB, nullable=False)

    # entity: 0..* — inputs used (e.g. the source document for an extracted biomarker).
    # Format: [{"role": "source", "what": {"reference": "DocumentReference/uuid"}}]
    entity = Column(JSONB, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id) if self.id else None,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "target": self.target,
            "recorded": self.recorded.isoformat() if self.recorded else None,
            "activity": self.activity,
            "agent": self.agent,
            "entity": self.entity,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_fhir_dict(self) -> dict:
        """Serialize to a FHIR R4B Provenance resource via fhir.resources (validated)."""
        return build_fhir_resource(
            "Provenance",
            {
                "resourceType": "Provenance",
                "id": str(self.id) if self.id else None,
                "target": self.target,
                "recorded": fhir_isoformat(self.recorded)
                or fhir_isoformat(self.created_at),
                "activity": self.activity,
                "agent": self.agent,
                "entity": self.entity,
                "meta": build_meta(str(self.id) if self.id else None),
            },
        )
