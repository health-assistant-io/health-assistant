"""Diagnostic Report FHIR schemas"""

from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field


class DiagnosticReportBase(BaseModel):
    """Base diagnostic report schema"""

    status: str = Field(default="final", description="Report status")
    code: Dict[str, Any] = Field(..., description="Report code object")
    subject: Dict[str, Any] = Field(..., description="Patient reference")


class DiagnosticReportCreate(DiagnosticReportBase):
    """Diagnostic report creation schema"""

    tenant_id: UUID
    conclusion: Optional[str] = None
    effective_datetime: Optional[datetime] = None
    issued: Optional[datetime] = None
    performer: Optional[Dict[str, Any]] = None
    category: Optional[Dict[str, Any]] = None
    conclusion_code: Optional[Dict[str, Any]] = None
    presented_form: Optional[Dict[str, Any]] = None


class DiagnosticReportUpdate(BaseModel):
    """Diagnostic report update schema"""

    status: Optional[str] = None
    code: Optional[Dict[str, Any]] = None
    subject: Optional[Dict[str, Any]] = None
    conclusion: Optional[str] = None
    effective_datetime: Optional[datetime] = None
    issued: Optional[datetime] = None
    performer: Optional[Dict[str, Any]] = None
    category: Optional[Dict[str, Any]] = None
    conclusion_code: Optional[Dict[str, Any]] = None
    presented_form: Optional[Dict[str, Any]] = None


class DiagnosticReportResponse(DiagnosticReportBase):
    """Diagnostic report response schema"""

    id: UUID
    conclusion: Optional[str] = None
    effective_datetime: Optional[datetime] = None
    issued: Optional[datetime] = None
    performer: Optional[Dict[str, Any]] = None
    category: Optional[Dict[str, Any]] = None
    conclusion_code: Optional[Dict[str, Any]] = None
    presented_form: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
