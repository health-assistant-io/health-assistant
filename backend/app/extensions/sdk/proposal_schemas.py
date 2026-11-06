"""Pydantic payload contracts for HITL proposal kinds (plan 5 W3).

Every kind's ``final_payload`` is validated against the model its contract
entry names (``schema_ref``) **before any write** — the AI/user output is
untrusted input (the family security standard AI trust boundary). The four patient-record
kinds are strict (``extra="forbid"``: an unexpected field is a bug, never a
silent skip); catalog kinds stay open because upstream metadata legitimately
carries vendor-specific keys, and their identity fields are validated by the
existing catalog services.

The contract file (``app/extensions/hitl-proposal-kinds.json``) names these
models; :data:`PAYLOAD_SCHEMAS` is the static resolution table (no dynamic
imports — the extension structure probe bans them under ``app/extensions``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import (
    ClinicalEventStatus,
    CodingSystem,
)
from app.schemas.medication import MedicationRecordCreate


class _StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _OpenPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


class CreateEventPayload(_StrictPayload):
    """`final_payload` for ``create_event`` (patient clinical event)."""

    patient_id: UUID | None = None
    type_id: UUID | None = None
    status: ClinicalEventStatus = ClinicalEventStatus.ACTIVE
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    onset_date: datetime | None = None
    resolved_date: datetime | None = None
    occurrences: list[dict[str, Any]] = Field(default_factory=list)
    event_metadata: dict[str, Any] = Field(default_factory=dict)
    coding_system: CodingSystem | None = None
    code: str | None = None
    examinations: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)


class CreateExaminationPayload(_StrictPayload):
    """`final_payload` for ``create_examination`` (patient examination)."""

    patient_id: UUID | None = None
    examination_date: date | None = None
    notes: str | None = None
    patient_notes: str | None = None
    category: str | None = None
    category_concept_id: UUID | None = None
    organization_id: UUID | None = None
    doctor_ids: list[UUID] = Field(default_factory=list)
    diagnoses: list[str] = Field(default_factory=list)
    impressions: str | None = None
    auto_extract_metadata: bool = False
    external_id: str | None = None


class AddMedicationPayload(MedicationRecordCreate):
    """`final_payload` for ``add_medication`` (patient prescription).

    Inherits the canonical ``MedicationRecordCreate`` shape (the same schema
    the REST endpoint uses), tightened to reject unknown fields. ``patient_id``
    is optional on the wire — the proposal row is the tenant/patient scope and
    a mismatching explicit value is rejected by the resolver.

    The HITL forms carry the hyphen/lowercase status spelling
    (``active`` / ``entered-in-error`` / ``on-hold``); the canonical enum is
    uppercase, so this boundary normalizes before validation instead of
    rejecting a card the UI itself rendered.
    """

    model_config = ConfigDict(extra="forbid")

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper().replace("-", "_")
        return value


class AddBiomarkerPayload(_StrictPayload):
    """`final_payload` for ``add_biomarker_to_examination`` (observation)."""

    patient_id: UUID | None = None
    examination_id: UUID | None = None
    biomarker_id: UUID
    status: str = "final"
    category: Any = None
    code: dict[str, Any] = Field(default_factory=dict)
    value_quantity: dict[str, Any] | None = None
    value_codeable_concept: dict[str, Any] | None = None
    value_string: str | None = None
    component: list[dict[str, Any]] | None = None
    note: Any = None
    comment: str | None = None
    method: str | None = None
    effective_datetime: datetime | None = None
    interpretation: Any = None
    reference_range: Any = None
    raw_value: float | None = None
    document_id: UUID | None = None
    subject: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _requires_a_value_shape(self) -> AddBiomarkerPayload:
        if not (
            self.value_quantity
            or self.value_codeable_concept
            or self.value_string
            or self.component
        ):
            raise ValueError(
                "add_biomarker_to_examination requires value_quantity, "
                "value_codeable_concept, value_string, or component"
            )
        return self


class CatalogBiomarkerPayload(_OpenPayload):
    """`final_payload` for ``create_biomarker_definition``."""

    name: str = Field(min_length=1, max_length=255)
    slug: str | None = None
    coding_system: str | None = None
    code: str | None = None
    category: str | None = None
    aliases: list[Any] = Field(default_factory=list)
    info: str | None = None


class CatalogMedicationPayload(_OpenPayload):
    """`final_payload` for ``create_medication_definition``."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    indications: str | None = None
    dosage_info: str | None = None
    contraindications: str | None = None
    side_effects: list[Any] = Field(default_factory=list)


class CatalogConceptPayload(_OpenPayload):
    """`final_payload` for ``create_concept``."""

    slug: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(min_length=1, max_length=64)


class CatalogEdgePayload(_OpenPayload):
    """`final_payload` for ``create_edge``."""

    src_type: str = Field(min_length=1, max_length=64)
    src_id: str = Field(min_length=1, max_length=64)
    dst_type: str = Field(min_length=1, max_length=64)
    dst_id: str = Field(min_length=1, max_length=64)
    relation: str = Field(min_length=1, max_length=64)


class ClientCommittedPayload(_OpenPayload):
    """Payloads for kinds the canonical REST endpoint already wrote.

    ``add_allergy`` / ``create_allergy_definition`` / ``generate_anatomy_graph``
    are committed by the UI through the same REST/FHIR endpoints a manual
    create uses; the resolve call only records the user-approved payload for
    audit and agent feedback. The shape is deliberately open, but bounded to a
    JSON object (never a bare scalar) so the audit row stays queryable.
    """


class AskUserPayload(_StrictPayload):
    """`final_payload` for ``ask_user`` (structured answers, no write)."""

    answers: dict[str, Any] = Field(default_factory=dict)


#: ``schema_ref`` (``module:Class``) → model. Static by design: the structure
#: probe forbids dynamic importing under ``app/extensions``.
PAYLOAD_SCHEMAS: dict[str, type[BaseModel]] = {
    "app.extensions.sdk.proposal_schemas:CreateEventPayload": CreateEventPayload,
    "app.extensions.sdk.proposal_schemas:CreateExaminationPayload": CreateExaminationPayload,
    "app.extensions.sdk.proposal_schemas:AddMedicationPayload": AddMedicationPayload,
    "app.extensions.sdk.proposal_schemas:AddBiomarkerPayload": AddBiomarkerPayload,
    "app.extensions.sdk.proposal_schemas:CatalogBiomarkerPayload": CatalogBiomarkerPayload,
    "app.extensions.sdk.proposal_schemas:CatalogMedicationPayload": CatalogMedicationPayload,
    "app.extensions.sdk.proposal_schemas:CatalogConceptPayload": CatalogConceptPayload,
    "app.extensions.sdk.proposal_schemas:CatalogEdgePayload": CatalogEdgePayload,
    "app.extensions.sdk.proposal_schemas:ClientCommittedPayload": ClientCommittedPayload,
    "app.extensions.sdk.proposal_schemas:AskUserPayload": AskUserPayload,
}


def schema_for(schema_ref: str) -> type[BaseModel] | None:
    """The model named by a contract entry, or ``None`` when unregistered."""
    return PAYLOAD_SCHEMAS.get(schema_ref)


__all__ = [
    "PAYLOAD_SCHEMAS",
    "AddBiomarkerPayload",
    "AddMedicationPayload",
    "AskUserPayload",
    "CatalogBiomarkerPayload",
    "CatalogConceptPayload",
    "CatalogEdgePayload",
    "CatalogMedicationPayload",
    "ClientCommittedPayload",
    "CreateEventPayload",
    "CreateExaminationPayload",
    "schema_for",
]
