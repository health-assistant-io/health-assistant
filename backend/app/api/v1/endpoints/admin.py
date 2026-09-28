"""Admin endpoints for system-wide operations.

Currently exposes ontology-catalog import (URL + file upload), the
system-wide notification broadcast, and the cross-tenant audit-stream
viewer. Catalog imports and broadcasts are SYSTEM_ADMIN-only (broadcast
allows ADMIN for tenant scope); every admin action here writes an
``audit_events`` row via ``audit_service.log_audit_action``
(identity-auth §17 — plan 16 H2).

Imports run in the background via FastAPI's ``BackgroundTasks``.
Progress is recorded to the ``task_logs`` table via ``TaskLogger`` so
admins can follow along in the Task Monitor UI.

The previous version of this module was entirely broken (wrong
``async_session_maker`` import, wrong ``TaskLogger`` / ``TaskProgressTracker``
arity, calls to nonexistent methods). Rewritten against the real signatures
in ``app.workers.task_logger`` and ``app.core.database``.
"""

import json
import logging
from typing import Dict, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, get_db
from app.core.security import RoleChecker, TokenData
from app.models.enums import Role
from app.schemas.biomarker import CatalogImportPayload
from app.schemas.tenant import AuditEntryResponse, AuditListResponse
from app.services.audit_service import (
    OUTCOME_DENIED,
    OUTCOME_ERROR,
    OUTCOME_OK,
    log_audit_action,
)
from app.services.catalog_import_service import CatalogImportService
from app.services.seed_export_service import SeedExportService
from app.services.tenant_admin_service import TenantAdminService
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
        await log_audit_action(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="admin.catalog_import",
            resource_type="catalog",
            outcome=OUTCOME_ERROR,
            new_value={"source_url": url, "error": str(e)[:200]},
        )
        raise HTTPException(status_code=400, detail=str(e))

    background_tasks.add_task(
        _run_catalog_import,
        payload=payload,
        user_id=str(current_user.user_id),
        tenant_id=str(current_user.tenant_id),
        source_url=url,
    )
    await log_audit_action(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="admin.catalog_import",
        resource_type="catalog",
        outcome=OUTCOME_OK,
        new_value={
            "source_url": url,
            "units": len(payload.units),
            "biomarkers": len(payload.biomarkers),
        },
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
        await log_audit_action(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="admin.catalog_import",
            resource_type="catalog",
            outcome=OUTCOME_ERROR,
            new_value={"source_file": file.filename, "error": f"invalid JSON: {e}"},
        )
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}")
    except Exception:
        logger.exception("Catalog payload validation failed")
        await log_audit_action(
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="admin.catalog_import",
            resource_type="catalog",
            outcome=OUTCOME_ERROR,
            new_value={"source_file": file.filename, "error": "invalid payload"},
        )
        raise HTTPException(
            status_code=400, detail="Invalid catalog payload (see server log)."
        )

    background_tasks.add_task(
        _run_catalog_import,
        payload=payload,
        user_id=str(current_user.user_id),
        tenant_id=str(current_user.tenant_id),
        source_url=f"upload:{file.filename}",
    )
    await log_audit_action(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        action="admin.catalog_import",
        resource_type="catalog",
        outcome=OUTCOME_OK,
        new_value={
            "source_file": file.filename,
            "units": len(payload.units),
            "biomarkers": len(payload.biomarkers),
        },
    )
    return {
        "message": "Catalog import started in the background. Check task logs for progress."
    }


@router.post("/notifications/broadcast")
async def broadcast_notification(
    title: str,
    body: str | None = None,
    severity: str = "info",
    scope: str = "tenant",
    tenant_id: str | None = None,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),
) -> Dict[str, str]:
    """Broadcast a system notification.

    ADMIN/MANAGER may broadcast to their own tenant (``scope=tenant``).
    SYSTEM_ADMIN may additionally broadcast system-wide (``scope=system``)
    or target another tenant via ``tenant_id``.
    """
    from app.models.enums import (
        NotificationCategory,
        NotificationSeverity,
        NotificationSource,
        NotificationType,
        RecipientKind,
    )
    from app.services.notification_service import emit

    is_system_admin = current_user.role == Role.SYSTEM_ADMIN.value
    if severity not in {s.value for s in NotificationSeverity}:
        raise HTTPException(status_code=400, detail=f"Invalid severity: {severity}")

    if scope == "system":
        if not is_system_admin:
            await log_audit_action(
                tenant_id=current_user.tenant_id,
                user_id=current_user.user_id,
                action="admin.broadcast",
                resource_type="notification",
                outcome=OUTCOME_DENIED,
                new_value={"scope": scope, "reason": "system scope requires SYSTEM_ADMIN"},
            )
            raise HTTPException(
                status_code=403, detail="Only SYSTEM_ADMIN can broadcast system-wide."
            )
        targets = [{"kind": RecipientKind.SYSTEM.value}]
        tenant_scope = None
    elif scope == "tenant":
        target_tenant = (
            tenant_id if (is_system_admin and tenant_id) else current_user.tenant_id
        )
        targets = [{"kind": RecipientKind.TENANT.value, "id": str(target_tenant)}]
        tenant_scope = target_tenant
    else:
        raise HTTPException(
            status_code=400, detail="scope must be 'tenant' or 'system'."
        )

    notification = await emit(
        source=NotificationSource.SYSTEM,
        type=NotificationType.SYSTEM_BROADCAST,
        category=NotificationCategory.SYSTEM,
        severity=NotificationSeverity(severity),
        title=title,
        body=body,
        tenant_id=tenant_scope,
        targets=targets,
        payload={"broadcast": True, "scope": scope},
        source_ref={"broadcast_by": str(current_user.user_id)},
        sender_user_id=current_user.user_id,
    )
    if notification is None:
        await log_audit_action(
            tenant_id=tenant_scope,
            user_id=current_user.user_id,
            action="admin.broadcast",
            resource_type="notification",
            outcome=OUTCOME_ERROR,
            new_value={"scope": scope, "title": title},
        )
        raise HTTPException(status_code=500, detail="Failed to emit notification.")
    await log_audit_action(
        tenant_id=tenant_scope,
        user_id=current_user.user_id,
        action="admin.broadcast",
        resource_type="notification",
        resource_id=notification.id,
        outcome=OUTCOME_OK,
        new_value={"scope": scope, "severity": severity, "title": title},
    )
    return {"status": "success", "notification_id": str(notification.id)}


@router.get("/audit", response_model=AuditListResponse)
async def list_audit_events(
    tenant_id: Optional[UUID] = Query(
        default=None,
        description="Filter to one tenant; omit for the full cross-tenant stream.",
    ),
    action: Optional[str] = Query(default=None),
    outcome: Optional[str] = Query(default=None),
    user_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
) -> AuditListResponse:
    """Cross-tenant audit-stream viewer (``audit_events`` — §17, plan 16 H2).

    SYSTEM_ADMIN-only. Without ``tenant_id`` this returns every tenant's
    stream **plus** system-level rows (NULL tenant — e.g. tenant hard
    deletes, login denials for unknown principals). Tenant-scoped reads
    stay on ``GET /admin/tenants/{tenant_id}/audit``.
    """
    items, total = await TenantAdminService(db).list_audit_entries_all(
        tenant_id=tenant_id,
        action=action,
        outcome=outcome,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )
    return AuditListResponse(
        items=[AuditEntryResponse.model_validate(a) for a in items],
        total=total,
    )


@router.get("/seeds/export.zip")
async def export_seeds_zip(
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Download the running instance's global taxonomy/anatomy/catalog data as
    a ZIP of seed-format JSON files (flat layout, one file per seed).

    The download is read-only — it never touches the server's ``data/seeds/``.
    A maintainer transfers the ZIP to their dev machine and unpacks it into
    ``backend/data/seeds/`` (via ``scripts/unpack_seeds_zip.py``, which backs
    up existing files first), then reviews with ``git diff data/seeds/``.

    SYSTEM_ADMIN-only: seeds are the global canonical taxonomy, not tenant data.
    """
    zip_bytes = await SeedExportService(db, tenant_id=None).build_zip_bytes()
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="health-assistant-seeds.zip"',
        },
    )
