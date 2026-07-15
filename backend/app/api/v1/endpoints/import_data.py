import logging
import tempfile
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.backup import ImportJobResponse, ImportJobListResponse
from app.schemas.import_data import (
    CSVImportConfig,
    FHIRImportConfig,
    OCRImportConfig,
)
from app.schemas.user import TokenData
from app.services.import_service import ImportService

router = APIRouter(prefix="/import", tags=["data-import"])

logger = logging.getLogger(__name__)


def _parse_uuid(v: str) -> UUID:
    try:
        return UUID(v)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid id")


@router.post("/backup", response_model=ImportJobResponse)
async def import_backup(
    file: UploadFile = File(...),
    auto_map_biomarkers: bool = Form(True),
    use_ai_normalization: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Import a backup archive (ZIP) or a bare FHIR Bundle / catalog JSON.

    Accepts:
    - A ZIP produced by POST /export (full_backup or fhir_only wrapped) — verified via manifest.
    - A bare `bundle.json` (FHIR R4B Bundle) — restored as a transaction.
    - A bare `catalog.json` (biomarker/unit definitions) — upserted into the ontology catalog.
    """
    tenant_id = _parse_uuid(str(current_user.tenant_id))
    user_id = _parse_uuid(str(current_user.user_id))
    svc = ImportService(db)

    suffix = Path(file.filename or "").suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        job = await svc.create_import_job(user_id, tenant_id, file.filename)
        from app.workers.tasks import import_backup as import_backup_task

        import json

        config_dict = {
            "auto_map_biomarkers": auto_map_biomarkers,
            "use_ai_normalization": use_ai_normalization,
        }

        import_backup_task.delay(
            str(job.id), str(tmp_path), str(user_id), json.dumps(config_dict)
        )
        return ImportJobResponse(**job.to_dict())
    except Exception:
        tmp_path.unlink(missing_ok=True)
        # Re-raise so the global handler returns a generic 500 + correlation
        # id. Backup import errors can include DB/FS internals — never echo.
        logger.exception("Backup import job creation failed")
        raise


@router.post("/fhir")
async def import_fhir(
    file: UploadFile = File(...),
    patient_id: Optional[str] = Form(None),
    validate_data: bool = Form(True, alias="validate"),
    auto_map_biomarkers: bool = Form(True),
    use_ai_normalization: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Import FHIR resources from a JSON file (synchronous, smaller bundles)."""
    filename = file.filename or ""
    if not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be a JSON")

    tmp_path = None
    try:
        tenant_id = str(current_user.tenant_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)
        svc = ImportService(db)
        config = FHIRImportConfig(
            validate_profiles=validate_data,
            auto_map_biomarkers=auto_map_biomarkers,
            use_ai_normalization=use_ai_normalization,
        )
        result = await svc.import_from_fhir(
            tmp_path, tenant_id, patient_id=patient_id, config=config
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@router.post("/csv")
async def import_csv(
    file: UploadFile = File(...),
    patient_id: Optional[str] = Form(None),
    delimiter: str = Form(","),
    has_header: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Import data from a CSV file."""
    filename = file.filename or ""
    if not filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    tmp_path = None
    try:
        tenant_id = str(current_user.tenant_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)
        config = CSVImportConfig(delimiter=delimiter, has_header=has_header)
        svc = ImportService(db)
        result = await svc.import_from_csv(
            file_path=tmp_path,
            tenant_id=tenant_id,
            patient_id=patient_id,
            config=config,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@router.post("/ocr")
async def import_ocr(
    file: UploadFile = File(...),
    patient_id: Optional[str] = Form(None),
    model_name: Optional[str] = Form(None),
    api_base: Optional[str] = Form(None),
    extract_tables: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Import data using OCR from PDF or images (OpenAI-compatible APIs)."""
    valid_extensions = [".pdf", ".jpg", ".jpeg", ".png", ".webp"]
    filename = file.filename or "unknown"
    if not any(filename.lower().endswith(ext) for ext in valid_extensions):
        raise HTTPException(
            status_code=400,
            detail=f"File must be one of: {', '.join(valid_extensions)}",
        )

    tmp_path = None
    try:
        suffix = Path(filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)
        config = OCRImportConfig(
            provider=settings.OCR_PROVIDER,
            model_name=model_name or settings.OPENAI_MODEL,
            extract_tables=extract_tables,
        )
        api_base_url = api_base or settings.OPENAI_API_BASE
        api_key = settings.OPENAI_API_KEY
        model = model_name or settings.OPENAI_MODEL
        tenant_id = str(current_user.tenant_id)
        svc = ImportService(db)
        result = await svc.import_from_ocr(
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
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@router.get("/jobs", response_model=ImportJobListResponse)
async def list_import_jobs(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """List import jobs for the current tenant."""
    tenant_id = _parse_uuid(str(current_user.tenant_id))

    from sqlalchemy import select
    from app.models.export_import_job import ImportJobModel

    res = await db.execute(
        select(ImportJobModel)
        .where(ImportJobModel.tenant_id == tenant_id)
        .order_by(ImportJobModel.created_at.desc())
        .limit(limit)
    )
    jobs = res.scalars().all()

    return ImportJobListResponse(
        items=[ImportJobResponse(**j.to_dict()) for j in jobs], total=len(jobs)
    )


@router.get("/jobs/{job_id}", response_model=ImportJobResponse)
async def get_import_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get the status of a backup import job."""
    jid = _parse_uuid(job_id)
    tenant_id = _parse_uuid(str(current_user.tenant_id))
    svc = ImportService(db)
    job = await svc.get_job(jid, tenant_id)
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    return ImportJobResponse(**job.to_dict())
