from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime, date
from pydantic import BaseModel, Field, field_validator
from enum import Enum


from app.models.enums import MedicationStatus


# --- Medication Catalog ---


class MedicationCatalogBase(BaseModel):
    name: str
    description: Optional[str] = None
    indications: Optional[str] = None
    side_effects: List[str] = Field(default_factory=list)
    contraindications: Optional[str] = None
    dosage_info: Optional[str] = None

    @field_validator("side_effects", mode="before")
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v

    @field_validator(
        "description", "indications", "contraindications", "dosage_info", mode="before"
    )
    @classmethod
    def empty_string_to_none(cls, v):
        if v == "":
            return None
        return v


class MedicationCatalogCreate(MedicationCatalogBase):
    pass


class MedicationCatalogUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    indications: Optional[str] = None
    side_effects: Optional[List[str]] = None
    contraindications: Optional[str] = None
    dosage_info: Optional[str] = None


class MedicationCatalogResponse(MedicationCatalogBase):
    id: UUID
    is_custom: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# --- Medication Record (Patient) ---


class MedicationTiming(BaseModel):
    type: str = "daily"  # daily, weekly, specific_days, interval
    frequency: Optional[int] = 1
    period: Optional[int] = 1
    period_unit: Optional[str] = "day"  # day, week, month
    days_of_week: List[str] = Field(default_factory=list)  # ["mon", "tue", ...]
    time_of_day: List[str] = Field(default_factory=list)  # ["08:00", "20:00"]
    as_needed: bool = False
    display: Optional[str] = None

    @field_validator("days_of_week", "time_of_day", mode="before")
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v


class MedicationRecordBase(BaseModel):
    status: MedicationStatus = MedicationStatus.ACTIVE
    code: Dict[str, Any]  # {"text": "Aspirin", "catalog_id": "..."}
    patient_id: Optional[UUID] = None
    examination_id: Optional[UUID] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    dosage: Optional[str] = None
    frequency: Optional[MedicationTiming] = None
    reason: Optional[str] = None
    note: Optional[str] = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def empty_date_to_none(cls, v):
        if v == "":
            return None
        return v

    @field_validator("dosage", "reason", "note", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "":
            return None
        return v


class MedicationRecordCreate(MedicationRecordBase):
    timing: Optional[Dict[str, Any]] = None  # Direct FHIR timing object support


class MedicationRecordUpdate(BaseModel):
    status: Optional[MedicationStatus] = None
    code: Optional[Dict[str, Any]] = None
    examination_id: Optional[UUID] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    dosage: Optional[str] = None
    frequency: Optional[MedicationTiming] = None
    reason: Optional[str] = None
    note: Optional[str] = None


class MedicationRecordResponse(MedicationRecordBase):
    id: UUID
    patient_id: UUID
    tenant_id: UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
