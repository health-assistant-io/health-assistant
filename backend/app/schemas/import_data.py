from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict
from datetime import datetime
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
    model_name: Optional[str] = None
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
    filename: Optional[str] = None
    file_path: Optional[str] = None
    progress: int = 0
    total_records: int = 0
    processed_records: int = 0
    failed_records: int = 0
    errors: List[str] = []
    warnings: List[str] = []
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


class ImportResult(BaseModel):
    """Result of import operation"""
    job_id: str
    status: ImportStatus
    total_records: int
    processed_records: int
    failed_records: int
    created_resources: Dict[str, int] = {}
    updated_resources: Dict[str, int] = {}
    errors: List[str] = []
    warnings: List[str] = []
    summary: Optional[str] = None


class CSVImportConfig(BaseModel):
    """Configuration for CSV import"""
    delimiter: str = ","
    encoding: str = "utf-8"
    has_header: bool = True
    date_format: Optional[str] = None
    column_mappings: Dict[str, str] = {}
    
    model_config = ConfigDict(from_attributes=True)


class FHIRImportConfig(BaseModel):
    """Configuration for FHIR import"""
    resource_type: Optional[str] = None
    bundle_type: str = "collection"
    validate_profiles: bool = True
    
    model_config = ConfigDict(from_attributes=True)


class OCRImportConfig(BaseModel):
    """Configuration for OCR import"""
    provider: str = "openai"
    model_name: Optional[str] = None
    language: str = "en"
    extract_tables: bool = True
    extract_images: bool = False
    confidence_threshold: float = 0.8
    
    model_config = ConfigDict(from_attributes=True)


class DataImportRequest(BaseModel):
    """Request to import data"""
    format: ImportFormat
    options: Optional[ImportOptions] = None
    csv_config: Optional[CSVImportConfig] = None
    fhir_config: Optional[FHIRImportConfig] = None
    ocr_config: Optional[OCRImportConfig] = None


class DataImportResponse(BaseModel):
    """Response for import request"""
    job_id: str
    status: ImportStatus
    message: str
    estimated_time: Optional[int] = None  # seconds
