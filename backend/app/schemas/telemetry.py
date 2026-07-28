from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID


class TelemetryDataPoint(BaseModel):
    """A single long-format telemetry measurement.

    One point = one ``(timestamp, slug, value)`` triple. A mobile client
    syncing N metrics at one timestamp sends N points (not one point with N
    fields). The integration SDK already produces one Observation per metric,
    so the mapping is 1:1.
    """

    timestamp: datetime = Field(
        ..., description="ISO 8601 timestamp of the measurement"
    )
    slug: str = Field(
        ...,
        description="Biomarker slug (e.g. 'heart-rate', 'steps', 'spo2')",
    )
    value: float = Field(..., description="Numeric measurement value")
    unit: Optional[str] = Field(
        None, description="Optional unit symbol (e.g. 'bpm', 'count', '%')"
    )
    patient_id: Optional[UUID] = Field(
        None,
        description=(
            "Optional patient attribution. When omitted, the row is "
            "attributed later via the device_id → UserIntegration chain."
        ),
    )


class TelemetrySyncPayload(BaseModel):
    device_id: str = Field(
        ..., description="Unique identifier for the mobile device or watch"
    )
    points: List[TelemetryDataPoint] = Field(
        ..., description="Array of long-format telemetry points to sync"
    )
