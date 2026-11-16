# ruff: noqa: B904 -- long immutable strings; reflow when touched
import logging
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.roles import is_scoped_principal, is_unscoped_system_admin
from app.core.config import settings
from app.core.database import get_db
from app.core.security import RoleChecker, get_current_user
from app.models.enums import Role
from app.models.patient_access_grant import GrantPermission
from app.schemas.backup import ImportJobListResponse, ImportJobResponse
from app.schemas.import_data import (
    CSVImportConfig,
    FHIRImportConfig,
)
from app.schemas.user import TokenData
from app.services.auth.access import check_patient_access
from app.services.imports.service import ImportService
from app.storage.object_storage import ObjectTooLarge, StorageError

router = APIRouter(prefix="/import", tags=["data-import"])

logger = logging.getLogger(__name__)


def _parse_uuid(v: str) -> UUID:
    try:
        return UUID(v)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid id")


async def _fail_import_job(svc: ImportService, job, error: str) -> None:
    """Mark a just-created import job failed when its archive was rejected.

    A PENDING job with no stored archive is a dead row the UI can never
    finish; the create/upload failure path owns closing it. Best-effort: the
    original HTTP error must still propagate.
    """
    if job is None:
        return
    try:
        await svc._fail_job(job.id, error)
    except Exception:
        logger.warning("Could not mark rejected import job %s failed", job.id)


@router.post("/backup", response_model=ImportJobResponse)
async def import_backup(
    file: UploadFile = File(...),
    auto_map_biomarkers: bool = Form(True),
    use_ai_normalization: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN])),  # noqa: B008 -- framework default idiom (FastAPI/Pydantic)
):
    """Import a backup archive (ZIP) or a bare FHIR Bundle / catalog JSON.

    Accepts:
    - A ZIP produced by POST /export (full_backup or fhir_only wrapped) — verified via manifest.
    - A bare `bundle.json` (FHIR R4B Bundle) — restored as a transaction.
    - A bare `catalog.json` (biomarker/unit definitions) — upserted into the
      caller tenant's catalog overlay; system rows stay the seed lane's
      (the SYSTEM_ADMIN URL import is the system door).
    """
    tenant_id = _parse_uuid(str(current_user.tenant_id))
    user_id = _parse_uuid(str(current_user.user_id))
    svc = ImportService(db)

    from app.services.documents.document_service import _read_capped
    from app.storage.artifacts import guess_content_type, import_key
    from app.storage.documents import DocumentStorage

    content = await _read_capped(file, settings.MAX_UPLOAD_SIZE * 1024 * 1024)
    storage = DocumentStorage()
    key = None
    job = None

    try:
        job = await svc.create_import_job(user_id, tenant_id, file.filename)
        # The worker is a separate process: hand it an object key, never a
        # path on this host's /tmp. It materializes the archive itself and
        # deletes the object when the job finishes.
        key = import_key(tenant_id, job.id, file.filename or "backup.bin")
        await storage.aput_key(
            key,
            content,
            content_type=guess_content_type(file.filename, "application/octet-stream"),
        )
        import json

        from app.workers.tasks import import_backup as import_backup_task

        config_dict = {
            "auto_map_biomarkers": auto_map_biomarkers,
            "use_ai_normalization": use_ai_normalization,
        }

        import_backup_task.delay(str(job.id), key, str(user_id), json.dumps(config_dict))
        return ImportJobResponse(**job.to_dict())
    except ObjectTooLarge as exc:
        if key is not None:
            try:
                await storage.adelete_key(key)
            except Exception:
                logger.warning("Failed to clean up import object %s", key)
        await _fail_import_job(
            svc, job, "Backup archive exceeds the storage limit."
        )
        raise HTTPException(
            status_code=413, detail="Backup archive exceeds the storage limit."
        ) from exc
    except StorageError as exc:
        if key is not None:
            try:
                await storage.adelete_key(key)
            except Exception:
                logger.warning("Failed to clean up import object %s", key)
        await _fail_import_job(svc, job, "Import storage is unavailable.")
        logger.error("Backup import storage failure: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Import storage is unavailable") from exc
    except Exception:
        if key is not None:
            try:
                await storage.adelete_key(key)
            except Exception:
                logger.warning("Failed to clean up import object %s", key)
        await _fail_import_job(svc, job, "Backup import job creation failed.")
        # Re-raise so the global handler returns a generic 500 + correlation
        # id. Backup import errors can include DB/FS internals — never echo.
        logger.exception("Backup import job creation failed")
        raise


@router.post("/fhir")
async def import_fhir(
    file: UploadFile = File(...),
    patient_id: str | None = Form(None),
    validate_data: bool = Form(True, alias="validate"),
    auto_map_biomarkers: bool = Form(True),
    use_ai_normalization: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(RoleChecker([Role.MANAGER, Role.ADMIN])),  # noqa: B008 -- framework default idiom (FastAPI/Pydantic)
):
    """Import FHIR resources from a JSON file (synchronous, smaller bundles).

    A FHIR Bundle carries its own resource ids and subject references, so a
    raw bundle is a tenant-wide write surface: MANAGER or above. USERs import
    through CSV/OCR into a patient they own, or through document extraction.
    """
    filename = file.filename or ""
    if not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be a JSON")

    if patient_id:
        await check_patient_access(patient_id, current_user, db, required=GrantPermission.WRITE)

    tmp_path = None
    try:
        tenant_id = str(current_user.tenant_id)
        from app.services.documents.document_service import _read_capped

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
            content = await _read_capped(file, settings.MAX_UPLOAD_SIZE * 1024 * 1024)
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
    except Exception:
        logger.exception("Import failed")
        raise HTTPException(status_code=500, detail="Import failed")
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@router.post("/csv")
async def import_csv(
    file: UploadFile = File(...),
    patient_id: str | None = Form(None),
    delimiter: str = Form(","),
    has_header: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Import data from a CSV file."""
    filename = file.filename or ""
    if not filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a CSV")

    if patient_id:
        await check_patient_access(patient_id, current_user, db, required=GrantPermission.WRITE)
    elif current_user.role == Role.USER.value:
        raise HTTPException(
            status_code=400,
            detail="patient_id is required when importing as a USER.",
        )

    tmp_path = None
    try:
        tenant_id = str(current_user.tenant_id)
        from app.services.documents.document_service import _read_capped

        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            content = await _read_capped(file, settings.MAX_UPLOAD_SIZE * 1024 * 1024)
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
    except Exception:
        logger.exception("Import failed")
        raise HTTPException(status_code=500, detail="Import failed")
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@router.post("/ocr")
async def import_ocr(
    file: UploadFile = File(...),
    patient_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Import data using OCR from PDF or images.

    Provider, base URL, model and key resolve through the scoped AI gateway
    (the ``ocr`` task assignment, BYOK key at rest) — the request carries no
    model configuration.
    """
    valid_extensions = [".pdf", ".jpg", ".jpeg", ".png", ".webp"]
    filename = file.filename or "unknown"
    if not any(filename.lower().endswith(ext) for ext in valid_extensions):
        raise HTTPException(
            status_code=400,
            detail=f"File must be one of: {', '.join(valid_extensions)}",
        )

    if patient_id:
        await check_patient_access(patient_id, current_user, db, required=GrantPermission.WRITE)
    elif current_user.role == Role.USER.value:
        raise HTTPException(
            status_code=400,
            detail="patient_id is required when importing as a USER.",
        )

    tmp_path = None
    try:
        suffix = Path(filename).suffix
        from app.services.documents.document_service import _read_capped

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await _read_capped(file, settings.MAX_UPLOAD_SIZE * 1024 * 1024)
            tmp.write(content)
            tmp_path = Path(tmp.name)
        tenant_id = str(current_user.tenant_id)
        svc = ImportService(db)
        result = await svc.import_from_ocr(
            file_path=tmp_path,
            tenant_id=tenant_id,
            patient_id=patient_id,
            user_id=str(current_user.user_id),
        )
        return result
    except Exception:
        logger.exception("Import failed")
        raise HTTPException(status_code=500, detail="Import failed")
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _authorize_job_access(job, current_user: TokenData) -> None:
    """Object-level gate for import job status (mirrors the export job gate).

    Import job metadata is owner-sensitive (source filename, record counts,
    restore_result, errors). A USER may only see their own job; MANAGER/ADMIN
    may inspect the tenant's jobs; an unscoped SYSTEM_ADMIN anything. A
    scoped credential (PAT/integration principal) is owner-scoped whatever its
    owner's role name: it never inherits the operator's tenant-wide read of
    other people's restore results. A foreign or nonexistent job answers the
    same 404 — no existence disclosure.
    """
    if is_scoped_principal(current_user):
        if str(job.user_id) != str(current_user.user_id):
            raise HTTPException(status_code=404, detail="Import job not found")
        return
    if is_unscoped_system_admin(current_user) or current_user.role != Role.USER.value:
        return
    if str(job.user_id) != str(current_user.user_id):
        raise HTTPException(status_code=404, detail="Import job not found")


@router.get("/jobs", response_model=ImportJobListResponse)
async def list_import_jobs(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """List import jobs for the current tenant.

    USER accounts — and every scoped credential, whatever its owner's role
    name — see only their own jobs (the same answer as export jobs); staff
    sessions see the tenant-wide list. The owner filter runs in the query so
    own jobs are not crowded out by staff rows before the limit.
    """
    tenant_id = _parse_uuid(str(current_user.tenant_id))

    from sqlalchemy import select

    from app.models.export_import_job import ImportJobModel

    stmt = select(ImportJobModel).where(ImportJobModel.tenant_id == tenant_id)
    if is_scoped_principal(current_user) or current_user.role == Role.USER.value:
        stmt = stmt.where(ImportJobModel.user_id == _parse_uuid(str(current_user.user_id)))
    res = await db.execute(stmt.order_by(ImportJobModel.created_at.desc()).limit(limit))
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
    _authorize_job_access(job, current_user)
    return ImportJobResponse(**job.to_dict())
