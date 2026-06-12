"""Medication FHIR schemas"""

from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime, date
from pydantic import BaseModel, Field


class MedicationBase(BaseModel):
    """Base medication schema"""

    code: Dict[str, Any] = Field(..., description="Medication code (RxNorm)")
    status: str = Field(default="ACTIVE", description="Medication status")
    subject: Dict[str, Any] = Field(..., description="Patient reference")


class MedicationCreate(MedicationBase):
    """Medication creation schema"""

    tenant_id: UUID
    batch: Optional[Dict[str, Any]] = None
    dose_rate: Optional[Dict[str, Any]] = None
    quantity: Optional[Dict[str, Any]] = None
    day_supply: Optional[Dict[str, Any]] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    reason: Optional[str] = None


class MedicationUpdate(BaseModel):
    """Medication update schema"""

    code: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    subject: Optional[Dict[str, Any]] = None
    batch: Optional[Dict[str, Any]] = None
    dose_rate: Optional[Dict[str, Any]] = None
    quantity: Optional[Dict[str, Any]] = None
    day_supply: Optional[Dict[str, Any]] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    reason: Optional[str] = None


class MedicationResponse(MedicationBase):
    """Medication response schema"""

    id: UUID
    batch: Optional[Dict[str, Any]] = None
    dose_rate: Optional[Dict[str, Any]] = None
    quantity: Optional[Dict[str, Any]] = None
    day_supply: Optional[Dict[str, Any]] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    reason: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MedicationList(BaseModel):
    """Medication list response schema"""

    items: List[MedicationResponse]
    total: int = Field(..., description="Total number of medications")
