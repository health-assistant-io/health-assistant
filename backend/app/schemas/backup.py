from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
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
    patient_ids: Optional[List[str]] = None
    include_documents: bool = True
    include_telemetry: bool = True
    include_integrations: bool = True
    include_ai_config: bool = False

    model_config = ConfigDict(from_attributes=True)


class ExportJobResponse(BaseModel):
    id: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    scope: ExportScope
    export_type: ExportType
    status: JobStatus
    progress: int = 0
    patient_ids: Optional[List[str]] = None
    file_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    resource_counts: Optional[Dict[str, int]] = None
    smart_scope: Optional[str] = None
    error_message: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ExportJobListResponse(BaseModel):
    items: List[ExportJobResponse]
    total: int


class ImportJobResponse(BaseModel):
    id: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    source_filename: Optional[str] = None
    status: JobStatus
    progress: int = 0
    total_records: Optional[int] = None
    processed_records: Optional[int] = None
    failed_records: Optional[int] = None
    restore_result: Optional[Dict[str, Any]] = None
    errors: Optional[List[str]] = None
    warnings: Optional[List[str]] = None
    error_message: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ImportJobListResponse(BaseModel):
    items: List[ImportJobResponse]
    total: int


class ManifestFile(BaseModel):
    path: str
    sha256: str
    size: int


class BackupManifest(BaseModel):
    schema_version: str = BACKUP_SCHEMA_VERSION
    exported_at: datetime
    tenant_id: Optional[str] = None
    fhir_version: str = FHIR_VERSION
    scope: ExportScope
    export_type: ExportType
    smart_scope: str
    source: str = "health-assistant"
    counts: Dict[str, int] = Field(default_factory=dict)
    files: List[ManifestFile] = Field(default_factory=list)
    options: Dict[str, bool] = Field(default_factory=dict)
    notes: Optional[List[str]] = None


class RestoreResult(BaseModel):
    job_id: str
    status: JobStatus
    total_records: int = 0
    processed_records: int = 0
    failed_records: int = 0
    created_resources: Dict[str, int] = Field(default_factory=dict)
    updated_resources: Dict[str, int] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    manifest_verified: bool = False
    fhir_validated: bool = False
