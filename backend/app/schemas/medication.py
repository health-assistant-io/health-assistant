from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import MedicationIntent, MedicationStatus

# --- Medication Catalog ---


class MedicationCatalogBase(BaseModel):
    name: str
    description: str | None = None
    indications: str | None = None
    side_effects: list[str] = Field(default_factory=list)
    contraindications: str | None = None
    dosage_info: str | None = None

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
    name: str | None = None
    description: str | None = None
    indications: str | None = None
    side_effects: list[str] | None = None
    contraindications: str | None = None
    dosage_info: str | None = None


class MedicationCatalogResponse(MedicationCatalogBase):
    id: UUID
    is_custom: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


# --- Medication Record (Patient) ---


class MedicationTiming(BaseModel):
    type: str = "daily"  # daily, weekly, specific_days, interval
    frequency: int | None = 1
    period: int | None = 1
    period_unit: str | None = "day"  # day, week, month
    days_of_week: list[str] = Field(default_factory=list)  # ["mon", "tue", ...]
    time_of_day: list[str] = Field(default_factory=list)  # ["08:00", "20:00"]
    as_needed: bool = False
    display: str | None = None

    @field_validator("days_of_week", "time_of_day", mode="before")
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v


class MedicationRecordBase(BaseModel):
    status: MedicationStatus = MedicationStatus.ACTIVE
    code: dict[str, Any]  # {"text": "Aspirin", "catalog_id": "..."}
    patient_id: UUID | None = None
    examination_id: UUID | None = None
    # Discriminator: MedicationStatement (default) vs MedicationRequest.
    # Exposed so integration pulls can import a remote MedicationRequest as
    # intent=order; UI callers leave it unset to keep the legacy statement
    # default. ``None`` here means "let the ORM column default apply".
    intent: MedicationIntent | None = None
    start_date: date | None = None
    end_date: date | None = None
    dosage: str | None = None
    frequency: MedicationTiming | None = None
    reason: str | None = None
    note: str | None = None

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
    timing: dict[str, Any] | None = None  # Direct FHIR timing object support
    # Integration dedup key (Phase 4 of the fhir-server multi-resource sync
    # plan). Set by integration providers on the objects they return from
    # ``pull_medications``; the engine reads it and forwards it to the
    # service. ``source_integration_id`` is NOT on the schema — the engine
    # always supplies it (= the integration's own id).
    external_id: str | None = None


class MedicationRecordUpdate(BaseModel):
    status: MedicationStatus | None = None
    code: dict[str, Any] | None = None
    examination_id: UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    dosage: str | None = None
    frequency: MedicationTiming | None = None
    reason: str | None = None
    note: str | None = None


class MedicationRecordResponse(MedicationRecordBase):
    id: UUID
    patient_id: UUID
    tenant_id: UUID
    source_integration_id: UUID | None = None
    external_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
