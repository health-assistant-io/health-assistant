from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    AllergyCategory,
    AllergyClinicalStatus,
    AllergyCriticality,
    ReactionSeverity,
)

# --- Allergy Catalog ---
# Write contract (plan A Phase 2 / ADR 0055 D5): extra="forbid" — unknown or
# system-owned fields (scope, tenant_id, seed_key, created_by, id, timestamps)
# are rejected with 422. ``coding_system``/``code`` (SNOMED) are writable;
# ``seed_key`` is response-only.


class AllergyCatalogBase(BaseModel):
    name: str
    coding_system: str | None = Field(default=None, max_length=50)
    code: str | None = Field(default=None, max_length=100)
    class_concept_id: UUID | None = None
    class_concept_slug: str | None = None
    category: AllergyCategory = AllergyCategory.OTHER
    description: str | None = None
    typical_reactions: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("typical_reactions", mode="before")
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v

    @field_validator("description", mode="before")
    @classmethod
    def empty_string_to_none(cls, v):
        if v == "":
            return None
        return v


class AllergyCatalogCreate(AllergyCatalogBase):
    pass


class AllergyCatalogUpdate(BaseModel):
    """Partial update for an allergy catalog entry (all fields optional).

    Class-concept precedence matches ``MedicationCatalogUpdate``: a sent
    ``class_concept_id`` wins (an explicit ``null`` clears the link, any slug
    sent alongside is ignored); a bare slug re-resolves tenant-explicitly.
    """

    coding_system: str | None = Field(default=None, max_length=50)
    code: str | None = Field(default=None, max_length=100)
    class_concept_id: UUID | None = None
    class_concept_slug: str | None = None
    name: str | None = None
    category: AllergyCategory | None = None
    description: str | None = None
    typical_reactions: list[str] | None = None

    model_config = ConfigDict(extra="forbid")


class AllergyCatalogResponse(AllergyCatalogBase):
    id: UUID
    class_concept_name: str | None = None
    seed_key: str | None = None
    scope: str | None = None
    tenant_id: UUID | None = None
    created_by: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
        arbitrary_types_allowed=True,
        extra="ignore",
    )


# --- Allergy Intolerance (Patient Record) ---


class AllergyReaction(BaseModel):
    manifestation: str
    severity: ReactionSeverity = ReactionSeverity.MILD
    date: datetime | None = None


class AllergenCode(BaseModel):
    text: str
    catalog_id: UUID | None = None


class AllergyIntoleranceBase(BaseModel):
    patient_id: UUID | None = None
    clinical_status: AllergyClinicalStatus = AllergyClinicalStatus.ACTIVE
    verification_status: str = "confirmed"
    category: AllergyCategory | None = None
    criticality: AllergyCriticality | None = None
    code: dict[str, Any]  # {"text": "Peanuts", "catalog_id": "..."}
    onset_date: datetime | None = None
    resolved_date: datetime | None = None
    last_occurrence: datetime | None = None
    note: str | None = None
    reactions: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("reactions", mode="before")
    @classmethod
    def ensure_reactions_list(cls, v):
        if v is None:
            return []
        return v

    @field_validator("note", "verification_status", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "":
            return None
        return v


class AllergyIntoleranceCreate(AllergyIntoleranceBase):
    # Integration dedup key (Phase 4 of the fhir-server multi-resource sync
    # plan). Set by integration providers on the objects they return from
    # ``pull_allergies``; the engine reads it and forwards it to the
    # service. ``source_integration_id`` is NOT on the schema — the engine
    # always supplies it (= the integration's own id).
    external_id: str | None = None


class AllergyIntoleranceUpdate(BaseModel):
    clinical_status: AllergyClinicalStatus | None = None
    verification_status: str | None = None
    category: AllergyCategory | None = None
    criticality: AllergyCriticality | None = None
    code: dict[str, Any] | None = None
    onset_date: datetime | None = None
    resolved_date: datetime | None = None
    last_occurrence: datetime | None = None
    note: str | None = None
    reactions: list[dict[str, Any]] | None = None


class AllergyIntoleranceResponse(AllergyIntoleranceBase):
    id: UUID
    patient_id: UUID
    tenant_id: UUID
    patient_name_display: str | None = None
    source_integration_id: UUID | None = None
    external_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
