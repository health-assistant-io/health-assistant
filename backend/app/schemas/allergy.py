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


class AllergyCatalogBase(BaseModel):
    name: str
    category: AllergyCategory = AllergyCategory.OTHER
    description: str | None = None
    typical_reactions: list[str] = Field(default_factory=list)

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
    """Partial update for an allergy catalog entry (all fields optional)."""

    name: str | None = None
    category: AllergyCategory | None = None
    description: str | None = None
    typical_reactions: list[str] | None = None


class AllergyCatalogResponse(AllergyCatalogBase):
    id: UUID
    is_custom: bool
    scope: str | None = None
    class_concept_id: UUID | None = None
    class_concept_slug: str | None = None
    class_concept_name: str | None = None
    tenant_id: UUID | None = None
    created_by: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


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
