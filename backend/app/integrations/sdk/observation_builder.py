from datetime import datetime, timezone, timezone
from typing import Any, Dict, List, Optional, Union
from uuid import UUID
import random

from app.schemas.fhir.observation import ObservationCreate
from app.models.enums import CodingSystem

class ObservationBuilder:
    """Helper to build Pydantic ObservationCreate objects for integrations."""

    def __init__(self, tenant_id: UUID, patient_id: UUID):
        self.tenant_id = tenant_id
        self.patient_id = patient_id
        self._data: Dict[str, Any] = {
            "status": "final",
            "effective_datetime": datetime.now(timezone.utc),
        }
        self._code: Optional[str] = None
        self._coding_system: CodingSystem = CodingSystem.LOINC
        self._display_name: Optional[str] = None
        self._value: Optional[float] = None
        self._unit: Optional[str] = None
        self._unit_code: Optional[str] = None
        self._biomarker_id: Optional[UUID] = None
        self._reference_range: Optional[Dict[str, float]] = None
        self._interpretation: Optional[str] = None

    def set_status(self, status: str):
        self._data["status"] = status
        return self

    def set_effective_date(self, dt: datetime):
        self._data["effective_datetime"] = dt
        return self

    def set_biomarker(self, code: str, display_name: str, coding_system: CodingSystem = CodingSystem.LOINC, biomarker_id: Optional[UUID] = None):
        self._code = code
        self._display_name = display_name
        self._coding_system = coding_system
        self._biomarker_id = biomarker_id
        return self

    def set_value(self, value: float, unit: str, unit_code: Optional[str] = None):
        self._value = value
        self._unit = unit
        self._unit_code = unit_code
        return self

    def set_reference_range(self, low: Optional[float] = None, high: Optional[float] = None):
        self._reference_range = {}
        if low is not None:
            self._reference_range["low"] = low
        if high is not None:
            self._reference_range["high"] = high
        return self

    def set_interpretation(self, interpretation: str):
        self._interpretation = interpretation
        return self

    def build(self) -> ObservationCreate:
        if not self._code or not self._display_name:
            raise ValueError("Biomarker code and display name are required")

        # Map enum to proper FHIR system URL
        system_url = "http://loinc.org"
        if self._coding_system == CodingSystem.SNOMED:
            system_url = "http://snomed.info/sct"
        elif self._coding_system == CodingSystem.CUSTOM:
            system_url = "http://healthassistant.local/custom"

        coding = [
            {
                "system": system_url,
                "code": self._code,
                "display": self._display_name
            }
        ]

        value_quantity = {
            "value": self._value,
            "unit": self._unit,
            "system": "http://unitsofmeasure.org",
            "code": self._unit_code or self._unit
        }

        # Calculate a mock relative score if reference range is present
        relative_score = None
        if self._reference_range and self._value is not None:
            low = self._reference_range.get("low")
            high = self._reference_range.get("high")
            if low is not None and high is not None and high > low:
                relative_score = (self._value - low) / (high - low)
                relative_score = max(0.0, min(1.0, relative_score))
            else:
                relative_score = 0.5 # Default middle score if range is incomplete

        # Ensure timezone-naive datetime for SQLAlchemy/asyncpg compatibility
        eff_dt = self._data["effective_datetime"]
        if eff_dt and eff_dt.tzinfo is not None:
            # Convert to UTC then remove tzinfo
            eff_dt = eff_dt.astimezone(timezone.utc).replace(tzinfo=None)

        return ObservationCreate(
            tenant_id=self.tenant_id,
            subject={"reference": f"Patient/{self.patient_id}"},
            status=self._data["status"],
            code={
                "coding": coding,
                "text": self._display_name
            },
            effective_datetime=eff_dt,
            value_quantity=value_quantity,
            raw_value=self._value,
            normalized_value=self._value, # Default to raw, should be improved with unit conversion
            biomarker_id=self._biomarker_id,
            lab_reference_range=self._reference_range,
            relative_score=relative_score,
            interpretation=self._interpretation
        )
