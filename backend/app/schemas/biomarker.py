from pydantic import BaseModel, ConfigDict
from uuid import UUID
from typing import Optional, List
from app.models.enums import CodingSystem


class UnitResponse(BaseModel):
    id: UUID
    symbol: str
    name: str
    quantity_type: str

    model_config = ConfigDict(from_attributes=True)


class UnitCreate(BaseModel):
    symbol: str
    name: str
    quantity_type: Optional[str] = "other"


class BiomarkerBase(BaseModel):
    slug: str
    coding_system: Optional[CodingSystem] = CodingSystem.LOINC
    code: Optional[str] = None
    name: str
    # Backward-compat: ``category`` is the readable string (the linked
    # ``biomarker_class`` concept's name). For writes prefer
    # ``class_concept_id``; ``category`` is best-effort resolved to a concept
    # in the biomarker endpoint / catalog import.
    category: Optional[str] = None
    class_concept_id: Optional[UUID] = None
    # The class concept *slug* — the canonical key used by the backup
    # export/import path. ``category`` is the concept *name* and does not
    # round-trip through ``biomarker_category_to_concept_slug`` (which only
    # swaps ``_``→``-``), so without this slug the class link is silently
    # dropped on restore. CatalogImportService resolves this ahead of the
    # legacy ``category`` string when both are present.
    class_concept_slug: Optional[str] = None
    aliases: List[str] = []
    info: Optional[str] = None
    reference_range_min: Optional[float] = None
    reference_range_max: Optional[float] = None
    is_telemetry: Optional[bool] = False


class BiomarkerCreate(BiomarkerBase):
    preferred_unit_symbol: Optional[str] = None
    preferred_unit_id: Optional[UUID] = None


class BiomarkerUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    class_concept_id: Optional[UUID] = None
    aliases: Optional[List[str]] = None
    info: Optional[str] = None
    reference_range_min: Optional[float] = None
    reference_range_max: Optional[float] = None
    is_telemetry: Optional[bool] = None
    preferred_unit_id: Optional[UUID] = None


class BiomarkerRemapRequest(BaseModel):
    """Relink unmapped observations to a biomarker definition.

    Observations are matched by their stored code.text against ``source_name``
    (case-insensitive). Scope to a patient when ``patient_id`` is provided.
    """

    source_name: str
    patient_id: Optional[UUID] = None


class BiomarkerResponse(BiomarkerBase):
    id: UUID
    preferred_unit_id: Optional[UUID]
    preferred_unit_symbol: Optional[str] = None
    meta_data: Optional[dict] = None

    model_config = ConfigDict(from_attributes=True)


class BiomarkerEventCorrelationBase(BaseModel):
    biomarker_id: UUID
    event_type_id: UUID
    correlation_type: Optional[str] = "monitoring"
    description: Optional[str] = None


class BiomarkerEventCorrelationResponse(BiomarkerEventCorrelationBase):
    id: UUID
    biomarker: Optional[BiomarkerResponse] = None

    model_config = ConfigDict(from_attributes=True)


class CatalogMetadata(BaseModel):
    version: str
    source: str
    last_updated: str


class CatalogImportPayload(BaseModel):
    metadata: Optional[CatalogMetadata] = None
    units: List[UnitCreate] = []
    biomarkers: List[BiomarkerCreate] = []
