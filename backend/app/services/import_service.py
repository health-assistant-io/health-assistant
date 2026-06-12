from typing import Optional, Dict
from pathlib import Path
import uuid
from app.schemas.import_data import (
    ImportFormat,
    ImportOptions,
    ImportResult,
    ImportStatus,
    CSVImportConfig,
    FHIRImportConfig,
    OCRImportConfig,
)
from app.processors.importers.csv_importer import CSVImporter
from app.processors.importers.fhir_importer import FHIRImporter
from app.processors.ocr import get_ocr_processor


class ImportService:
    """Service for importing data from various sources"""
    
    def __init__(self):
        self.results: Dict[str, ImportResult] = {}
    
    async def create_import_job(
        self,
        user_id: str,
        tenant_id: str,
        format: ImportFormat,
        options: Optional[ImportOptions] = None,
    ) -> str:
        """Create a new import job"""
        job_id = str(uuid.uuid4())
        
        # Store job metadata (would be in database in production)
        self.results[job_id] = ImportResult(
            job_id=job_id,
            status=ImportStatus.PENDING,
            total_records=0,
            processed_records=0,
            failed_records=0,
            errors=[],
            warnings=[],
        )
        
        return job_id
    
    async def import_from_csv(
        self,
        file_path: Path,
        tenant_id: str,
        patient_id: Optional[str] = None,
        config: Optional[CSVImportConfig] = None,
    ) -> ImportResult:
        """Import data from CSV file"""
        importer = CSVImporter(config)
        result = await importer.import_from_file(file_path, tenant_id, patient_id)
        
        # Process imported data into FHIR resources
        # This would create actual database records
        return result
    
    async def import_from_fhir(
        self,
        file_path: Path,
        tenant_id: str,
        patient_id: Optional[str] = None,
        config: Optional[FHIRImportConfig] = None,
    ) -> ImportResult:
        """Import FHIR resources from JSON file"""
        importer = FHIRImporter(config)
        result = await importer.import_from_file(file_path, tenant_id, patient_id)
        
        # Process imported FHIR resources
        # This would create actual database records
        return result
    
    async def import_from_ocr(
        self,
        file_path: Path,
        tenant_id: str,
        patient_id: Optional[str] = None,
        config: Optional[OCRImportConfig] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
    ) -> ImportResult:
        """Import data using OCR"""
        try:
            config = config or OCRImportConfig()
            
            # Get OCR processor
            ocr_processor = get_ocr_processor(
                provider=config.provider,
                api_key=api_key,
                api_base=api_base,
                model=model,
            )
            
            # Extract text from document
            _text = await ocr_processor.extract_text(file_path)
            
            # Process extracted text with NLP
            # This would use the NLP extractor to identify biomarkers, etc.
            
            result = ImportResult(
                job_id="",
                status=ImportStatus.COMPLETED,
                total_records=1,
                processed_records=1,
                failed_records=0,
                created_resources={"documents": 1},
                summary=f"Extracted text from {file_path.name}",
            )
            
            return result
        except Exception as e:
            return ImportResult(
                job_id="",
                status=ImportStatus.FAILED,
                total_records=0,
                processed_records=0,
                failed_records=0,
                errors=[str(e)],
            )
    
    async def get_import_status(self, job_id: str) -> Optional[ImportResult]:
        """Get import job status"""
        return self.results.get(job_id)
    
    async def cancel_import_job(self, job_id: str) -> bool:
        """Cancel import job"""
        if job_id in self.results:
            result = self.results[job_id]
            if result.status == ImportStatus.PENDING:
                result.status = ImportStatus.FAILED
                result.errors.append("Job cancelled by user")
                return True
        return False


# Singleton instance
import_service = ImportService()
