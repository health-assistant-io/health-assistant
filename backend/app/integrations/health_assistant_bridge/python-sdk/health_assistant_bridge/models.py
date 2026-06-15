from typing import Optional, List, Dict, Literal
from pydantic import BaseModel, Field

class BridgeStatus(BaseModel):
    status: str
    integration_id: str
    last_synced_at: Optional[str] = None
    cursor: Optional[str] = None

class MetricMappingRequest(BaseModel):
    name: str
    code: Optional[str] = None

class MapRequestPayload(BaseModel):
    unmapped_metrics: List[MetricMappingRequest]

class MappedMetric(BaseModel):
    original_name: str
    action: Literal["map_to_existing", "create_new"]
    existing_biomarker_id: Optional[str] = None
    new_biomarker_name: Optional[str] = None
    new_biomarker_code: Optional[str] = None
    new_biomarker_coding_system: Optional[str] = None

class MapResponsePayload(BaseModel):
    mappings: List[MappedMetric]

class ClientRecord(BaseModel):
    type: Literal["quantitative", "categorical"]
    code: Optional[str] = None
    coding_system: str = "custom"
    name: str
    value: Optional[float] = None
    value_string: Optional[str] = None
    unit: Optional[str] = None
    timestamp: Optional[str] = None
    reference_range: Optional[Dict[str, float]] = None
    interpretation: Optional[str] = None
    performer: Optional[str] = None

class SyncPayload(BaseModel):
    client_version: str
    source_system: str
    cursor: Optional[str] = None
    records: List[ClientRecord]

class SyncResponse(BaseModel):
    success: bool
    metrics_synced: Optional[int] = None
    message: Optional[str] = None
    error: Optional[str] = None
