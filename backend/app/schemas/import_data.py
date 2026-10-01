from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ImportFormat, ImportSourceType, ImportStatus


class ImportOptions(BaseModel):
    """Options for data import"""

    format: ImportFormat = ImportFormat.CSV
    source_type: ImportSourceType = ImportSourceType.FILE_UPLOAD
    create_patient: bool = False
    update_existing: bool = True
    validate_fhir: bool = True
    ocr_enabled: bool = True
    ocr_provider: str = "openai"
    model_name: str | None = None
    extract_images: bool = False

    model_config = ConfigDict(from_attributes=True)


class ImportJob(BaseModel):
    """Import job record"""

    id: str
    user_id: str
    tenant_id: str
    status: ImportStatus
    format: ImportFormat
    source_type: ImportSourceType
    filename: str | None = None
    file_path: str | None = None
    progress: int = 0
    total_records: int = 0
    processed_records: int = 0
    failed_records: int = 0
    errors: list[str] = []
    warnings: list[str] = []
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ImportResult(BaseModel):
    """Result of import operation"""

    job_id: str
    status: ImportStatus
    total_records: int
    processed_records: int
    failed_records: int
    created_resources: dict[str, int] = {}
    updated_resources: dict[str, int] = {}
    errors: list[str] = []
    warnings: list[str] = []
    summary: str | None = None


class CSVImportConfig(BaseModel):
    """Configuration for CSV import"""

    delimiter: str = ","
    encoding: str = "utf-8"
    has_header: bool = True
    date_format: str | None = None
    column_mappings: dict[str, str] = {}

    model_config = ConfigDict(from_attributes=True)


class FHIRImportConfig(BaseModel):
    """Configuration for FHIR import"""

    resource_type: str | None = None
    bundle_type: str = "collection"
    validate_profiles: bool = True
    auto_map_biomarkers: bool = True
    use_ai_normalization: bool = False

    model_config = ConfigDict(from_attributes=True)


class OCRImportConfig(BaseModel):
    """Configuration for OCR import"""

    provider: str = "openai"
    model_name: str | None = None
    language: str = "en"
    extract_tables: bool = True
    extract_images: bool = False
    confidence_threshold: float = 0.8

    model_config = ConfigDict(from_attributes=True)


class DataImportRequest(BaseModel):
    """Request to import data"""

    format: ImportFormat
    options: ImportOptions | None = None
    csv_config: CSVImportConfig | None = None
    fhir_config: FHIRImportConfig | None = None
    ocr_config: OCRImportConfig | None = None


class DataImportResponse(BaseModel):
    """Response for import request"""

    job_id: str
    status: ImportStatus
    message: str
    estimated_time: int | None = None  # seconds
