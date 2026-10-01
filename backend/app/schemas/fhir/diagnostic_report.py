"""Diagnostic Report FHIR schemas"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DiagnosticReportBase(BaseModel):
    """Base diagnostic report schema"""

    status: str = Field(default="final", description="Report status")
    code: dict[str, Any] = Field(..., description="Report code object")
    subject: dict[str, Any] = Field(..., description="Patient reference")


class DiagnosticReportCreate(DiagnosticReportBase):
    """Diagnostic report creation schema"""

    tenant_id: UUID
    conclusion: str | None = None
    effective_datetime: datetime | None = None
    issued: datetime | None = None
    performer: list[dict[str, Any]] | None = None
    category: dict[str, Any] | None = None
    conclusion_code: dict[str, Any] | None = None
    presented_form: dict[str, Any] | None = None


class DiagnosticReportUpdate(BaseModel):
    """Diagnostic report update schema"""

    status: str | None = None
    code: dict[str, Any] | None = None
    subject: dict[str, Any] | None = None
    conclusion: str | None = None
    effective_datetime: datetime | None = None
    issued: datetime | None = None
    performer: list[dict[str, Any]] | None = None
    category: dict[str, Any] | None = None
    conclusion_code: dict[str, Any] | None = None
    presented_form: dict[str, Any] | None = None


class DiagnosticReportResponse(DiagnosticReportBase):
    """Diagnostic report response schema"""

    id: UUID
    conclusion: str | None = None
    effective_datetime: datetime | None = None
    issued: datetime | None = None
    performer: list[dict[str, Any]] | None = None
    category: dict[str, Any] | None = None
    conclusion_code: dict[str, Any] | None = None
    presented_form: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
