from pydantic import BaseModel, ConfigDict, Field, field_validator
from uuid import UUID
from typing import Optional, List, Dict, Any
from datetime import datetime


from app.models.enums import (
    AllergyCategory,
    AllergyCriticality,
    AllergyClinicalStatus,
    ReactionSeverity,
)


# --- Allergy Catalog ---


class AllergyCatalogBase(BaseModel):
    name: str
    category: AllergyCategory = AllergyCategory.OTHER
    description: Optional[str] = None
    typical_reactions: List[str] = Field(default_factory=list)

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

    name: Optional[str] = None
    category: Optional[AllergyCategory] = None
    description: Optional[str] = None
    typical_reactions: Optional[List[str]] = None


class AllergyCatalogResponse(AllergyCatalogBase):
    id: UUID
    is_custom: bool
    scope: Optional[str] = None
    class_concept_id: Optional[UUID] = None
    class_concept_slug: Optional[str] = None
    class_concept_name: Optional[str] = None
    tenant_id: Optional[UUID] = None
    created_by: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


# --- Allergy Intolerance (Patient Record) ---


class AllergyReaction(BaseModel):
    manifestation: str
    severity: ReactionSeverity = ReactionSeverity.MILD
    date: Optional[datetime] = None


class AllergenCode(BaseModel):
    text: str
    catalog_id: Optional[UUID] = None


class AllergyIntoleranceBase(BaseModel):
    clinical_status: AllergyClinicalStatus = AllergyClinicalStatus.ACTIVE
    verification_status: str = "confirmed"
    category: Optional[AllergyCategory] = None
    criticality: Optional[AllergyCriticality] = None
    code: Dict[str, Any]  # {"text": "Peanuts", "catalog_id": "..."}
    onset_date: Optional[datetime] = None
    resolved_date: Optional[datetime] = None
    last_occurrence: Optional[datetime] = None
    note: Optional[str] = None
    reactions: List[Dict[str, Any]] = Field(default_factory=list)

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
    pass


class AllergyIntoleranceUpdate(BaseModel):
    clinical_status: Optional[AllergyClinicalStatus] = None
    verification_status: Optional[str] = None
    category: Optional[AllergyCategory] = None
    criticality: Optional[AllergyCriticality] = None
    code: Optional[Dict[str, Any]] = None
    onset_date: Optional[datetime] = None
    resolved_date: Optional[datetime] = None
    last_occurrence: Optional[datetime] = None
    note: Optional[str] = None
    reactions: Optional[List[Dict[str, Any]]] = None


class AllergyIntoleranceResponse(AllergyIntoleranceBase):
    id: UUID
    patient_id: UUID
    tenant_id: UUID
    patient_name_display: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
