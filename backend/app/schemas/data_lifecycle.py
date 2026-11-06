"""Wire schemas for data lifecycle: erasure requests + retention policies."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import (
    ErasureStatus,
    ErasureSubjectType,
    RetentionAction,
    RetentionScope,
)


class ErasureRequestCreate(BaseModel):
    """One erasure request.

    ``dry_run`` defaults to ``true`` — the impact report is always available
    before anything is deleted. Re-submit with ``dry_run=false`` to execute.
    """

    subject_type: ErasureSubjectType
    subject_id: UUID
    dry_run: bool = True
    export_before_erase: bool = False


class ErasureReportResponse(BaseModel):
    schema_version: int = 1
    subject: dict[str, str] = Field(default_factory=dict)
    dry_run: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    anonymized: dict[str, int] = Field(default_factory=dict)
    secrets: dict[str, int] = Field(default_factory=dict)
    files: dict[str, int] = Field(default_factory=dict)
    cascade: list[str] = Field(default_factory=list)
    export_job_id: str | None = None
    export_offer: dict[str, Any] = Field(default_factory=dict)


class ErasureResponse(BaseModel):
    id: UUID
    tenant_id: UUID | None = None
    subject_type: ErasureSubjectType
    subject_id: UUID
    requested_by: UUID | None = None
    status: ErasureStatus
    dry_run: bool
    report: ErasureReportResponse
    report_schema_version: int = 1
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None


class ErasureListResponse(BaseModel):
    requests: list[ErasureResponse]
    total: int


class RetentionPolicyResponse(BaseModel):
    id: UUID
    scope: RetentionScope
    scope_value: str
    action: RetentionAction
    days: int
    timestamp_column: str
    enabled: bool
    updated_at: datetime | None = None


class RetentionPolicyUpdate(BaseModel):
    """Operator edit: only the knobs the jobs read are writable."""

    days: int | None = Field(default=None, ge=0)
    action: Literal["delete", "anonymize"] | None = None
    enabled: bool | None = None


class RetentionRunResponse(BaseModel):
    tables: list[dict[str, Any]]
    total_rows: int
