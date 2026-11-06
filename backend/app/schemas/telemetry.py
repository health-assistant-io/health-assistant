from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class TelemetryDataPoint(BaseModel):
    """A single long-format telemetry measurement.

    One point = one ``(timestamp, slug, value)`` triple. A mobile client
    syncing N metrics at one timestamp sends N points (not one point with N
    fields). The integration SDK already produces one Observation per metric,
    so the mapping is 1:1.
    """

    timestamp: datetime = Field(..., description="ISO 8601 timestamp of the measurement")
    slug: str = Field(
        ...,
        description="Biomarker slug (e.g. 'heart-rate', 'steps', 'spo2')",
    )
    value: float = Field(..., description="Numeric measurement value")
    unit: str | None = Field(None, description="Optional unit symbol (e.g. 'bpm', 'count', '%')")
    patient_id: UUID = Field(
        ...,
        description=(
            "Patient the measurement belongs to. Required: telemetry rows are "
            "always patient-attributed (the hypertable stores ``patient_id`` "
            "NOT NULL; there is no device-only attribution path)."
        ),
    )


class TelemetrySyncPayload(BaseModel):
    device_id: str = Field(..., description="Opaque device reference (for example a watch serial)")
    source: str = Field(
        "bridge",
        description=(
            "Device source family; with ``device_id`` it identifies the telemetry device row"
        ),
    )
    device_label: str | None = Field(
        None, description="Human-readable device label (defaults to the device reference)"
    )
    points: list[TelemetryDataPoint] = Field(
        ..., description="Array of long-format telemetry points to sync"
    )
