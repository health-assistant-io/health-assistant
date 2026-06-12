from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID
from typing import Optional, List, Any
from datetime import datetime
from enum import Enum


from app.models.enums import AllergyCategory, AllergyCriticality, AllergyClinicalStatus, ReactionSeverity


# --- Allergy Catalog ---


class AllergyCatalogBase(BaseModel):
    name: str
    category: AllergyCategory
    description: Optional[str] = None
    typical_reactions: Optional[List[str]] = None


class AllergyCatalogCreate(AllergyCatalogBase):
    pass


class AllergyCatalogResponse(AllergyCatalogBase):
    id: UUID
    is_custom: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# --- Allergy Intolerance (Patient Record) ---


class AllergyReaction(BaseModel):
    manifestation: str
    severity: ReactionSeverity
    date: Optional[datetime] = None


class AllergenCode(BaseModel):
    text: str
    catalog_id: Optional[UUID] = None


class AllergyIntoleranceBase(BaseModel):
    clinical_status: AllergyClinicalStatus = AllergyClinicalStatus.ACTIVE
    verification_status: str = "confirmed"
    category: Optional[AllergyCategory] = None
    criticality: Optional[AllergyCriticality] = None
    code: AllergenCode
    onset_date: Optional[datetime] = None
    resolved_date: Optional[datetime] = None
    last_occurrence: Optional[datetime] = None
    note: Optional[str] = None
    reactions: List[AllergyReaction] = Field(default_factory=list)


class AllergyIntoleranceCreate(AllergyIntoleranceBase):
    pass


class AllergyIntoleranceUpdate(BaseModel):
    clinical_status: Optional[AllergyClinicalStatus] = None
    verification_status: Optional[str] = None
    category: Optional[AllergyCategory] = None
    criticality: Optional[AllergyCriticality] = None
    onset_date: Optional[datetime] = None
    resolved_date: Optional[datetime] = None
    last_occurrence: Optional[datetime] = None
    note: Optional[str] = None
    reactions: Optional[List[AllergyReaction]] = None


class AllergyIntoleranceResponse(AllergyIntoleranceBase):
    id: UUID
    patient_id: UUID
    patient_name_display: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
