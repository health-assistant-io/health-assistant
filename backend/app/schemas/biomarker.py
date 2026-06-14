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
    category: Optional[str] = None
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
    aliases: Optional[List[str]] = None
    info: Optional[str] = None
    reference_range_min: Optional[float] = None
    reference_range_max: Optional[float] = None
    is_telemetry: Optional[bool] = None
    preferred_unit_id: Optional[UUID] = None

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
