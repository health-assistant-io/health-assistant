from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional
import json

from app.core.database import get_db
from app.core.security import RoleChecker, TokenData
from app.models.enums import Role
from app.services.catalog_import_service import CatalogImportService
from app.schemas.biomarker import CatalogImportPayload
from app.workers.task_logger import TaskLogger, TaskProgressTracker

router = APIRouter(prefix="/admin", tags=["admin"])

@router.post("/catalogs/import/url")
async def import_catalog_from_url(
    url: str,
    background_tasks: BackgroundTasks,
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db)
):
    """
    Import a clinical ontology catalog from an external JSON URL (e.g. GitHub raw URL).
    Requires System Admin privileges.
    """
    
    async def process_import(target_url: str, user_id: str, tenant_id: str):
        # We need a fresh DB session for the background task
        from app.core.database import async_session_maker
        async with async_session_maker() as session:
            logger = TaskLogger(session)
            task_id = f"catalog_import_{user_id}"
            tracker = TaskProgressTracker(logger, task_id, "System Catalog Import", tenant_id, user_id)
            
            try:
                await tracker.update_progress(0, "Fetching catalog from URL...", {"url": target_url})
                import_service = CatalogImportService(session)
                payload = await import_service.fetch_catalog_from_url(target_url)
                
                total_items = len(payload.units) + len(payload.biomarkers)
                await tracker.update_progress(10, f"Fetched payload. Found {total_items} items to process.", {"total_items": total_items})
                
                stats = await import_service.import_catalog(payload)
                
                await tracker.complete(f"Import complete. Details: {stats}", result_data=stats)
                
            except Exception as e:
                await tracker.fail(f"Import failed: {str(e)}")

    background_tasks.add_task(process_import, url, current_user.user_id, current_user.tenant_id)
    return {"message": "Catalog import started in the background. Check task logs for progress."}


@router.post("/catalogs/import/file")
async def import_catalog_from_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN]))
):
    """
    Import a clinical ontology catalog from an uploaded JSON file.
    Requires System Admin privileges.
    """
    try:
        content = await file.read()
        data = json.loads(content)
        payload = CatalogImportPayload.model_validate(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {str(e)}")
        
    async def process_file_import(import_payload: CatalogImportPayload, user_id: str, tenant_id: str):
        from app.core.database import async_session_maker
        async with async_session_maker() as session:
            logger = TaskLogger(session)
            task_id = f"catalog_import_file_{user_id}"
            tracker = TaskProgressTracker(logger, task_id, "System Catalog Import (File)", tenant_id, user_id)
            
            try:
                total_items = len(import_payload.units) + len(import_payload.biomarkers)
                await tracker.update_progress(10, f"Processing uploaded file with {total_items} items.", {"total_items": total_items})
                
                import_service = CatalogImportService(session)
                stats = await import_service.import_catalog(import_payload)
                
                await tracker.complete(f"Import complete. Details: {stats}", result_data=stats)
                
            except Exception as e:
                await tracker.fail(f"Import failed: {str(e)}")

    background_tasks.add_task(process_file_import, payload, current_user.user_id, current_user.tenant_id)
    return {"message": "Catalog import started in the background. Check task logs for progress."}