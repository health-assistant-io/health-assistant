"""Admin endpoints for system-wide operations.

Currently exposes ontology-catalog import (URL + file upload). Both endpoints
are SYSTEM_ADMIN-only and run the import in the background via FastAPI's
``BackgroundTasks``. Progress is recorded to the ``task_logs`` table via
``TaskLogger`` so admins can follow along in the Task Monitor UI.

The previous version of this module was entirely broken (wrong
``async_session_maker`` import, wrong ``TaskLogger`` / ``TaskProgressTracker``
arity, calls to nonexistent methods). Rewritten against the real signatures
in ``app.workers.task_logger`` and ``app.core.database``.
"""
import json
import logging
from typing import Dict
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, get_db
from app.core.security import RoleChecker, TokenData
from app.models.enums import Role
from app.schemas.biomarker import CatalogImportPayload
from app.services.catalog_import_service import CatalogImportService
from app.workers.task_logger import TaskLogger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


async def _run_catalog_import(
    payload: CatalogImportPayload,
    user_id: str,
    tenant_id: str,
    *,
    source_url: str | None = None,
) -> None:
    task_id = str(uuid4())
    task_name = "System Catalog Import"
    async with AsyncSessionLocal() as session:
        task_logger = TaskLogger(
            task_name=task_name,
            task_id=task_id,
            tenant_id=tenant_id,
            db=session,
        )
        await task_logger.log_start(
            source_url=source_url,
            user_id=str(user_id),
            units=len(payload.units),
            biomarkers=len(payload.biomarkers),
        )
        try:
            total_items = len(payload.units) + len(payload.biomarkers)
            await task_logger.log_progress(
                stage="importing",
                progress=10,
                total_items=total_items,
            )

            import_service = CatalogImportService(session)
            stats = await import_service.import_catalog(payload)

            await task_logger.log_success(
                "Catalog import completed",
                **{k: str(v) for k, v in stats.items()},
            )
        except Exception as e:
            logger.exception("Catalog import failed")
            await task_logger.log_error(e, stage="import")


@router.post("/catalogs/import/url")
async def import_catalog_from_url(
    url: str,
    background_tasks: BackgroundTasks,
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, str]:
    """Import a clinical ontology catalog from an external JSON URL.

    Requires SYSTEM_ADMIN privileges. Runs in the background; check the Task
    Monitor UI for progress.
    """
    fetch_service = CatalogImportService(db)
    try:
        payload = await fetch_service.fetch_catalog_from_url(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    background_tasks.add_task(
        _run_catalog_import,
        payload=payload,
        user_id=str(current_user.user_id),
        tenant_id=str(current_user.tenant_id),
        source_url=url,
    )
    return {
        "message": "Catalog import started in the background. Check task logs for progress."
    }


@router.post("/catalogs/import/file")
async def import_catalog_from_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
) -> Dict[str, str]:
    """Import a clinical ontology catalog from an uploaded JSON file.

    Requires SYSTEM_ADMIN privileges. Runs in the background.
    """
    try:
        content = await file.read()
        data = json.loads(content)
        payload = CatalogImportPayload.model_validate(data)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid catalog payload: {e}")

    background_tasks.add_task(
        _run_catalog_import,
        payload=payload,
        user_id=str(current_user.user_id),
        tenant_id=str(current_user.tenant_id),
        source_url=f"upload:{file.filename}",
    )
    return {
        "message": "Catalog import started in the background. Check task logs for progress."
    }
