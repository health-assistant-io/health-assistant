from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ExportScope, ExportType, JobStatus

PROVENANCE_SYSTEM = "https://healthassistant.local/fhir/export"
PROVENANCE_CODE = "ha-export"
# Advertise R4 (4.0.1) — the FHIR version the /fhir/R4/* facade targets.
# Note on the validator: fhir.resources 8.x dropped its R4 subpackage and ships
# R4B (4.3.0) as its primary version. R4B is a backward-compatible superset of
# R4 for every field our models emit, so the R4B validator (used inside
# fhir_helpers.build_fhir_resource) accepts exactly the FHIR JSON we produce;
# what we advertise to clients is R4 4.0.1 because the path is /fhir/R4/.
FHIR_VERSION = "4.0.1"
BACKUP_SCHEMA_VERSION = "1.0.0"


class BackupRequest(BaseModel):
    scope: ExportScope = ExportScope.PATIENT
    export_type: ExportType = ExportType.FHIR_ONLY
    patient_ids: list[str] | None = None
    include_documents: bool = True
    include_telemetry: bool = True
    include_integrations: bool = True
    include_ai_config: bool = False

    model_config = ConfigDict(from_attributes=True)


class ExportJobResponse(BaseModel):
    id: str
    tenant_id: str | None = None
    user_id: str | None = None
    scope: ExportScope
    export_type: ExportType
    status: JobStatus
    progress: int = 0
    patient_ids: list[str] | None = None
    file_path: str | None = None
    file_size_bytes: int | None = None
    resource_counts: dict[str, int] | None = None
    smart_scope: str | None = None
    error_message: str | None = None
    completed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ExportJobListResponse(BaseModel):
    items: list[ExportJobResponse]
    total: int


class ImportJobResponse(BaseModel):
    id: str
    tenant_id: str | None = None
    user_id: str | None = None
    source_filename: str | None = None
    status: JobStatus
    progress: int = 0
    total_records: int | None = None
    processed_records: int | None = None
    failed_records: int | None = None
    restore_result: dict[str, Any] | None = None
    errors: list[str] | None = None
    warnings: list[str] | None = None
    error_message: str | None = None
    completed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ImportJobListResponse(BaseModel):
    items: list[ImportJobResponse]
    total: int


class ManifestFile(BaseModel):
    path: str
    sha256: str
    size: int


class BackupManifest(BaseModel):
    schema_version: str = BACKUP_SCHEMA_VERSION
    exported_at: datetime
    tenant_id: str | None = None
    fhir_version: str = FHIR_VERSION
    scope: ExportScope
    export_type: ExportType
    smart_scope: str
    source: str = "health-assistant"
    counts: dict[str, int] = Field(default_factory=dict)
    files: list[ManifestFile] = Field(default_factory=list)
    options: dict[str, bool] = Field(default_factory=dict)
    notes: list[str] | None = None


class RestoreResult(BaseModel):
    job_id: str
    status: JobStatus
    total_records: int = 0
    processed_records: int = 0
    failed_records: int = 0
    created_resources: dict[str, int] = Field(default_factory=dict)
    updated_resources: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    manifest_verified: bool = False
    fhir_validated: bool = False
