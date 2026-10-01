"""Medication FHIR schemas"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MedicationBase(BaseModel):
    """Base medication schema"""

    code: dict[str, Any] = Field(..., description="Medication code (RxNorm)")
    status: str = Field(default="ACTIVE", description="Medication status")
    subject: dict[str, Any] = Field(..., description="Patient reference")


class MedicationCreate(MedicationBase):
    """Medication creation schema"""

    tenant_id: UUID
    batch: dict[str, Any] | None = None
    dose_rate: dict[str, Any] | None = None
    quantity: dict[str, Any] | None = None
    day_supply: dict[str, Any] | None = None
    start_date: date | None = None
    end_date: date | None = None
    reason: str | None = None


class MedicationUpdate(BaseModel):
    """Medication update schema"""

    code: dict[str, Any] | None = None
    status: str | None = None
    subject: dict[str, Any] | None = None
    batch: dict[str, Any] | None = None
    dose_rate: dict[str, Any] | None = None
    quantity: dict[str, Any] | None = None
    day_supply: dict[str, Any] | None = None
    start_date: date | None = None
    end_date: date | None = None
    reason: str | None = None


class MedicationResponse(MedicationBase):
    """Medication response schema"""

    id: UUID
    batch: dict[str, Any] | None = None
    dose_rate: dict[str, Any] | None = None
    quantity: dict[str, Any] | None = None
    day_supply: dict[str, Any] | None = None
    start_date: date | None = None
    end_date: date | None = None
    reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class MedicationList(BaseModel):
    """Medication list response schema"""

    items: list[MedicationResponse]
    total: int = Field(..., description="Total number of medications")
