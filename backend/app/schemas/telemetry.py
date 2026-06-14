from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

class TelemetryDataPoint(BaseModel):
    timestamp: datetime = Field(..., description="ISO 8601 Timestamp of the measurement")
    heart_rate: Optional[float] = None
    steps: Optional[float] = None
    calories: Optional[float] = None
    data: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional dynamic metrics (e.g. SpO2, sleep stages)")

class TelemetrySyncPayload(BaseModel):
    device_id: str = Field(..., description="Unique identifier for the mobile device or watch")
    points: List[TelemetryDataPoint] = Field(..., description="Array of time-series data points to sync")
