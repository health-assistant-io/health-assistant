from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from typing import Optional
from app.core.security import get_current_user
from app.core.config import settings
from app.services.import_service import import_service
from app.schemas.import_data import (
    ImportStatus,
    CSVImportConfig,
    FHIRImportConfig,
    OCRImportConfig,
    DataImportRequest,
    DataImportResponse,
)
import tempfile
from pathlib import Path

from app.schemas.user import TokenData

router = APIRouter(prefix="/import", tags=["data-import"])


@router.post("", response_model=DataImportResponse)
async def import_data(
    request: DataImportRequest,
    current_user: TokenData = Depends(get_current_user),
):
    """
    Import data from various formats

    Supports:
    - CSV: Import lab results, vitals, etc.
    - JSON: Import FHIR resources
    - PDF/Image: OCR extraction using OpenAI-compatible APIs
    """
    try:
        job_id = await import_service.create_import_job(
            user_id=str(current_user.user_id),
            tenant_id=str(current_user.tenant_id),
            format=request.format,
            options=request.options,
        )

        return DataImportResponse(
            job_id=job_id,
            status=ImportStatus.PENDING,
            message=f"Import job created for {request.format.value} format",
            estimated_time=30,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/csv")
async def import_csv(
    file: UploadFile = File(...),
    patient_id: Optional[str] = Form(None),
    delimiter: str = Form(","),
    has_header: bool = Form(True),
    current_user: TokenData = Depends(get_current_user),
):
    """Import data from CSV file"""
    filename = file.filename or ""
    if not filename.endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a CSV",
        )

    tmp_path = None
    try:
        # Get tenant from user payload
        tenant_id = str(current_user.tenant_id)

        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        # Create import config
        config = CSVImportConfig(
            delimiter=delimiter,
            has_header=has_header,
        )

        # Process import
        result = await import_service.import_from_csv(
            file_path=tmp_path,
            tenant_id=tenant_id,
            patient_id=patient_id,
            config=config,
        )

        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    finally:
        # Clean up temp file
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


@router.post("/fhir")
async def import_fhir(
    file: UploadFile = File(...),
    patient_id: Optional[str] = Form(None),
    validate_data: bool = Form(True, alias="validate"),
    current_user: TokenData = Depends(get_current_user),
):
    """Import FHIR resources from JSON file"""
    filename = file.filename or ""
    if not filename.endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a JSON",
        )

    tmp_path = None
    try:
        # Get tenant from user payload
        tenant_id = str(current_user.tenant_id)

        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        # Create import config
        config = FHIRImportConfig(
            validate_profiles=validate_data,
        )

        # Process import
        result = await import_service.import_from_fhir(
            file_path=tmp_path,
            tenant_id=tenant_id,
            patient_id=patient_id,
            config=config,
        )

        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    finally:
        # Clean up temp file
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


@router.post("/ocr")
async def import_ocr(
    file: UploadFile = File(...),
    patient_id: Optional[str] = Form(None),
    model_name: Optional[str] = Form(None),
    api_base: Optional[str] = Form(None),
    extract_tables: bool = Form(True),
    current_user: TokenData = Depends(get_current_user),
):
    """
    Import data using OCR from PDF or images

    Supports OpenAI-compatible APIs:
    - OpenAI Vision API
    - Azure OpenAI
    - Local LLM (Ollama, etc.)
    - Any OpenAI-compatible endpoint
    """
    valid_extensions = [".pdf", ".jpg", ".jpeg", ".png", ".webp"]
    filename = file.filename or "unknown"
    if not any(filename.lower().endswith(ext) for ext in valid_extensions):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File must be one of: {', '.join(valid_extensions)}",
        )

    tmp_path = None
    try:
        # Save uploaded file temporarily
        suffix = Path(filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        # Create OCR config
        config = OCRImportConfig(
            provider=settings.OCR_PROVIDER,
            model_name=model_name or settings.OPENAI_MODEL,
            extract_tables=extract_tables,
        )

        # Use custom API base if provided
        api_base_url = api_base or settings.OPENAI_API_BASE
        api_key = settings.OPENAI_API_KEY
        model = model_name or settings.OPENAI_MODEL

        # Get tenant from user payload
        tenant_id = str(current_user.tenant_id)

        # Process import
        result = await import_service.import_from_ocr(
            file_path=tmp_path,
            tenant_id=tenant_id,
            patient_id=patient_id,
            config=config,
            api_key=api_key,
            api_base=api_base_url,
            model=model,
        )

        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    finally:
        # Clean up temp file
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


@router.get("/status/{job_id}")
async def get_import_status(
    job_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Get import job status"""
    result = await import_service.get_import_status(job_id)

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import job not found",
        )

    return result


@router.delete("/status/{job_id}")
async def cancel_import_job(
    job_id: str,
    current_user: TokenData = Depends(get_current_user),
):
    """Cancel import job"""
    success = await import_service.cancel_import_job(job_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot cancel job (already completed or not found)",
        )

    return {"message": "Import job cancelled"}
