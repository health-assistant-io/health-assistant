# ruff: noqa: B008,B904 -- long immutable strings; reflow when touched
"""Admin endpoints for system-wide operations.

Currently exposes ontology-catalog import (URL + file upload), the
system-wide notification broadcast, and the audit surface: the
admin audit-stream viewer (tenant-scoped for tenant admins,
system-wide for SYSTEM_ADMIN), the CSV/JSONL export (audited), the
retention view and the hash-chain verifier. Catalog imports and
broadcasts are SYSTEM_ADMIN-only (broadcast allows ADMIN for tenant
scope); every admin action here writes an ``audit_events`` row via
``audit_service.log_audit_action`` (identity-auth §17 — plan 16 H2).

Imports run in the background via FastAPI's ``BackgroundTasks``.
Progress is recorded to the ``task_logs`` table via ``TaskLogger`` so
admins can follow along in the Task Monitor UI.

The previous version of this module was entirely broken (wrong
``async_session_maker`` import, wrong ``TaskLogger`` / ``TaskProgressTracker``
arity, calls to nonexistent methods). Rewritten against the real signatures
in ``app.workers.task_logger`` and ``app.core.database``.
"""

import io
import json
import logging
from csv import writer as csv_writer
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz.roles import is_unscoped_system_admin
from app.core.database import AsyncSessionLocal, get_db
from app.core.security import RoleChecker, TokenData
from app.models.data_lifecycle_model import RetentionPolicy
from app.models.enums import RetentionScope, Role
from app.schemas.biomarker import CatalogImportPayload
from app.schemas.tenant import (
    AuditChainStatus,
    AuditChainVerifyAllResponse,
    AuditChainVerifyResponse,
    AuditEntryResponse,
    AuditListResponse,
    AuditRetentionPolicyResponse,
    AuditRetentionResponse,
)
from app.services.audit.audit_service import (
    OUTCOME_DENIED,
    OUTCOME_ERROR,
    OUTCOME_OK,
    log_audit_action,
)
from app.services.catalog.catalog_import_service import CatalogImportService
from app.services.seed.seed_export_service import SeedExportService
from app.services.tenancy.tenant_admin_service import TenantAdminService
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
            result = await import_service.import_catalog(payload)
            if result.failures:
                await session.rollback()
                names = ", ".join(f"{o.kind}:{o.reference}" for o in result.failures[:5])
                raise RuntimeError(
                    f"catalog import had {len(result.failures)} failed item(s): {names}"
                )
            await session.commit()

            await task_logger.log_success(
                "Catalog import completed",
                **{k: str(v) for k, v in result.stats.items()},
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
) -> dict[str, str]:
    """Import a clinical ontology catalog from an external JSON URL.

    Requires SYSTEM_ADMIN privileges. Runs in the background; check the Task
    Monitor UI for progress.
    """
    fetch_service = CatalogImportService(db)
    try:
        payload = await fetch_service.fetch_catalog_from_url(url)
    except ValueError as e:
        await log_audit_action(
            required=True,
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
        required=True,
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
    return {"message": "Catalog import started in the background. Check task logs for progress."}


@router.post("/catalogs/import/file")
async def import_catalog_from_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
) -> dict[str, str]:
    """Import a clinical ontology catalog from an uploaded JSON file.

    Requires SYSTEM_ADMIN privileges. Runs in the background.
    """
    try:
        content = await file.read()
        data = json.loads(content)
        payload = CatalogImportPayload.model_validate(data)
    except json.JSONDecodeError as e:
        await log_audit_action(
            required=True,
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
            required=True,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action="admin.catalog_import",
            resource_type="catalog",
            outcome=OUTCOME_ERROR,
            new_value={"source_file": file.filename, "error": "invalid payload"},
        )
        raise HTTPException(status_code=400, detail="Invalid catalog payload (see server log).")

    background_tasks.add_task(
        _run_catalog_import,
        payload=payload,
        user_id=str(current_user.user_id),
        tenant_id=str(current_user.tenant_id),
        source_url=f"upload:{file.filename}",
    )
    await log_audit_action(
        required=True,
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
    return {"message": "Catalog import started in the background. Check task logs for progress."}


@router.post("/notifications/broadcast")
async def broadcast_notification(
    title: str,
    body: str | None = None,
    severity: str = "info",
    scope: str = "tenant",
    tenant_id: str | None = None,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),
) -> dict[str, str]:
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
    from app.services.notifications.notification_service import emit

    # A scoped SYSTEM_ADMIN credential never broadcasts system-wide: its role
    # name is the owner's, its reach is capped at tenant scope (ADR 0054 D8).
    is_system_admin = is_unscoped_system_admin(current_user)
    if severity not in {s.value for s in NotificationSeverity}:
        raise HTTPException(status_code=400, detail=f"Invalid severity: {severity}")

    if scope == "system":
        if not is_system_admin:
            await log_audit_action(
                required=True,
                tenant_id=current_user.tenant_id,
                user_id=current_user.user_id,
                action="admin.broadcast",
                resource_type="notification",
                outcome=OUTCOME_DENIED,
                new_value={"scope": scope, "detail": "system scope requires SYSTEM_ADMIN"},
            )
            raise HTTPException(
                status_code=403, detail="Only SYSTEM_ADMIN can broadcast system-wide."
            )
        targets = [{"kind": RecipientKind.SYSTEM.value}]
        tenant_scope = None
    elif scope == "tenant":
        target_tenant = tenant_id if (is_system_admin and tenant_id) else current_user.tenant_id
        targets = [{"kind": RecipientKind.TENANT.value, "id": str(target_tenant)}]
        tenant_scope = target_tenant
    else:
        raise HTTPException(status_code=400, detail="scope must be 'tenant' or 'system'.")

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
            required=True,
            tenant_id=tenant_scope,
            user_id=current_user.user_id,
            action="admin.broadcast",
            resource_type="notification",
            outcome=OUTCOME_ERROR,
            new_value={"scope": scope, "title": title},
        )
        raise HTTPException(status_code=500, detail="Failed to emit notification.")
    await log_audit_action(
        required=True,
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
    tenant_id: UUID | None = Query(
        default=None,
        description="Filter to one tenant. ADMIN callers are always scoped to "
        "their own tenant; a foreign id answers 404 (hidden).",
    ),
    user_id: UUID | None = Query(
        default=None,
        description="Filter by actor (``audit_events.user_id``).",
    ),
    resource_type: str | None = Query(
        default=None,
        description="Filter by resource type (e.g. ``patient``, ``tenant``).",
    ),
    resource_id: UUID | None = Query(
        default=None,
        description="Filter by one resource's id.",
    ),
    action: str | None = Query(default=None),
    outcome: str | None = Query(
        default=None,
        description="Filter by outcome: ``ok`` / ``denied`` / ``error``.",
    ),
    date_from: datetime | None = Query(
        default=None,
        description="Inclusive lower bound on ``created_at`` (ISO-8601).",
    ),
    date_to: datetime | None = Query(
        default=None,
        description="Exclusive upper bound on ``created_at`` (ISO-8601).",
    ),
    all_events: bool = Query(
        default=False,
        alias="all",
        description="Explicit system-wide stream (SYSTEM_ADMIN only; their "
        "default anyway). Refused 403 for tenant admins.",
    ),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN])),
    db: AsyncSession = Depends(get_db),
) -> AuditListResponse:
    """The admin audit-stream viewer (``audit_events`` — §17, ADR 0054).

    Tenant-scoped for tenant admins (``AUDIT_READ`` @ tenant — their own
    tenant only, newest first), system-wide for ``SYSTEM_ADMIN`` (the full
    stream **plus** the system-level NULL-tenant rows; ``?tenant_id=``
    narrows, ``?all=true`` is the explicit form). Filters: actor, resource
    type/id, action, outcome, date range. Rows carry the chain metadata
    (``chain_key``/``seq``/``prev_hash``/``entry_hash``) so the viewer can
    show each entry's tamper-evidence position.
    """
    tenant_filter = await _resolve_audit_tenant(
        current_user,
        tenant_id,
        all_events,
        action="admin.audit_view",
    )
    items, total = await TenantAdminService(db).list_audit_entries_all(
        tenant_id=tenant_filter,
        action=action,
        outcome=outcome,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return AuditListResponse(
        items=[AuditEntryResponse.model_validate(a) for a in items],
        total=total,
    )


#: Export caps (documented in docs/API.md): the export streams newest-first
#: in bounded pages, never the whole table into memory. When the filter set
#: matches more rows than the cap, the stream is truncated and the response
#: says so via ``X-Audit-Export-Truncated`` / ``X-Audit-Export-Total``.
AUDIT_EXPORT_MAX_ROWS = 10000
_AUDIT_EXPORT_PAGE_SIZE = 500

_EXPORT_COLUMNS = (
    "id",
    "tenant_id",
    "chain_key",
    "seq",
    "prev_hash",
    "entry_hash",
    "created_at",
    "user_id",
    "action",
    "resource_type",
    "resource_id",
    "outcome",
    "old_value",  # gate-allow: *, audit diff
    "new_value",  # gate-allow: *, audit diff
)


def _export_row(row) -> dict:
    """One streamed audit row as its export shape (diffs are already
    redacted at write time; chain metadata included)."""
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id) if row.tenant_id else None,
        "chain_key": row.chain_key,
        "seq": row.seq,
        "prev_hash": row.prev_hash,
        "entry_hash": row.entry_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "user_id": str(row.user_id) if row.user_id else None,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": str(row.resource_id) if row.resource_id else None,
        "outcome": row.outcome,
        "old_value": row.old_value,  # gate-allow: *, audit diff
        "new_value": row.new_value,  # gate-allow: *, audit diff
    }


async def _jsonl_stream(rows) -> StreamingResponse:
    async def generate():
        async for row in rows:
            yield json.dumps(_export_row(row), ensure_ascii=False) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="audit-export.jsonl"'},
    )


async def _csv_stream(rows) -> StreamingResponse:
    async def generate():
        buffer = io.StringIO()
        writer = csv_writer(buffer, lineterminator="\n")
        writer.writerow(_EXPORT_COLUMNS)
        yield buffer.getvalue()
        async for row in rows:
            record = _export_row(row)
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow(
                [
                    json.dumps(record[column], ensure_ascii=False)
                    if isinstance(record[column], (dict, list))
                    else ("" if record[column] is None else str(record[column]))
                    for column in _EXPORT_COLUMNS
                ]
            )
            yield buffer.getvalue()

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-export.csv"'},
    )


@router.get("/audit/export")
async def export_audit_events(
    format: str = Query(default="json", pattern="^(csv|json)$"),
    tenant_id: UUID | None = Query(default=None),
    user_id: UUID | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    all_events: bool = Query(default=False, alias="all"),
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Export the filtered audit stream as CSV or newline-delimited JSON.

    Same filters and scoping as ``GET /admin/audit`` (tenant admins export
    their own tenant only), same newest-first order, up to 10000 rows per
    request. Every line/row carries the chain metadata
    (``chain_key``/``seq``/``prev_hash``/``entry_hash``) and the redacted
    diff payloads. The export itself is audited (``audit.export``,
    required/fail-closed), as is every denied attempt.

    When the filter set matches more rows than the cap, the stream is
    truncated and says so: the ``X-Audit-Export-Truncated: true`` and
    ``X-Audit-Export-Total: <matched>`` response headers let the viewer
    warn the operator to narrow the filters (the full stream stays
    available through smaller filtered requests).
    """
    tenant_filter = await _resolve_audit_tenant(
        current_user,
        tenant_id,
        all_events,
        action="audit.export",
    )
    service = TenantAdminService(db)
    total_matched = await service.count_audit_entries_all(
        tenant_id=tenant_filter,
        action=action,
        outcome=outcome,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        date_from=date_from,
        date_to=date_to,
    )
    truncated = total_matched > AUDIT_EXPORT_MAX_ROWS
    await log_audit_action(
        required=True,
        tenant_id=tenant_filter,
        user_id=current_user.user_id,
        action="audit.export",
        resource_type="audit",
        outcome=OUTCOME_OK,
        new_value={
            "format": format,
            "truncated": truncated,
            "total_matched": total_matched,
            "filters": {
                "tenant_id": str(tenant_filter) if tenant_filter else None,
                "user_id": str(user_id) if user_id else None,
                "resource_type": resource_type,
                "resource_id": str(resource_id) if resource_id else None,
                "action": action,
                "outcome": outcome,
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
            },
        },
    )
    rows = service.iter_audit_entries_all(
        tenant_id=tenant_filter,
        action=action,
        outcome=outcome,
        user_id=user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        date_from=date_from,
        date_to=date_to,
        page_size=_AUDIT_EXPORT_PAGE_SIZE,
        max_rows=AUDIT_EXPORT_MAX_ROWS,
    )
    headers = {
        "X-Audit-Export-Truncated": "true" if truncated else "false",
        "X-Audit-Export-Total": str(total_matched),
    }
    if format == "csv":
        response = await _csv_stream(rows)
    else:
        response = await _jsonl_stream(rows)
    response.headers.update(headers)
    return response


@router.get("/audit/retention", response_model=AuditRetentionResponse)
async def audit_retention(
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
) -> AuditRetentionResponse:
    """The retention view for ``audit_events`` (SYSTEM_ADMIN-only).

    Reads the ``retention_policies`` rows that govern audit data (the
    3650-day anonymize of actor/diff payloads — the app never deletes
    audit rows, and the sanctioned rewrites stay outside the hash chain).
    ``legal_holds`` is deliberately ``null``: there is no hold registry
    yet (ADR 0054 D3 / plan D Phase 4) — retention applies uniformly, and
    there is deliberately **no deletion endpoint** on this surface.
    """
    rows = (
        (
            await db.execute(
                select(RetentionPolicy).where(
                    RetentionPolicy.scope == RetentionScope.TABLE,
                    RetentionPolicy.scope_value.in_(["audit_events", "audit_chain_heads"]),
                )
            )
        )
        .scalars()
        .all()
    )
    return AuditRetentionResponse(
        policies=[AuditRetentionPolicyResponse.model_validate(row) for row in rows],
        legal_holds=None,
        notes=(
            "Audit rows are never deleted by the application. The sanctioned "
            "lifecycle anonymizes the actor and diff payloads after their "
            "retention window (retention_policies), leaving the per-tenant "
            "hash chain verifiable. There is no legal-hold registry yet — "
            "retention applies uniformly (ADR 0054 D3); this surface "
            "deliberately exposes no deletion endpoint."
        ),
    )


async def _resolve_audit_tenant(
    current_user: TokenData,
    tenant_id: UUID | None,
    all_requested: bool,
    *,
    action: str,
) -> UUID | None:
    """The tenant filter for the admin audit surface (ADR 0054 Phase 3).

    ``SYSTEM_ADMIN``: ``tenant_id`` narrows to one tenant, otherwise the
    full cross-tenant stream plus the system chain (``all=true`` is the
    explicit form of the default). ``ADMIN``: always their own tenant — a
    foreign ``tenant_id`` is a hidden 404 (§7) and ``all=true`` is refused
    403; both denials are audited (fail-closed).
    """
    # Only an unscoped human SYSTEM_ADMIN gets the cross-tenant stream; a
    # scoped credential carrying the same role name stays on its own tenant.
    is_system = is_unscoped_system_admin(current_user)
    if is_system:
        return tenant_id
    if all_requested:
        await log_audit_action(
            required=True,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action=action,
            resource_type="audit",
            outcome=OUTCOME_DENIED,
            new_value={"detail": "system_wide_requires_system_admin"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The system-wide audit view requires SYSTEM_ADMIN.",
        )
    own = UUID(str(current_user.tenant_id)) if current_user.tenant_id else None
    if tenant_id is not None and tenant_id != own:
        await log_audit_action(
            required=True,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            action=action,
            resource_type="audit",
            outcome=OUTCOME_DENIED,
            new_value={"detail": "cross_tenant"},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )
    return own


@router.get("/audit/verify", response_model=None)
async def verify_audit_chain_endpoint(
    tenant_id: UUID | None = Query(
        default=None,
        description="Verify this tenant's chain; omit for the system chain.",
    ),
    all_chains: bool = Query(
        default=False,
        alias="all",
        description="Verify every chain (all tenants + the system chain).",
    ),
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
) -> AuditChainVerifyResponse | AuditChainVerifyAllResponse:
    """Audit-chain verification (ADR 0054 D3 — program Phase 4A).

    SYSTEM_ADMIN-only. Walks the chain(s): sequence contiguity from 1,
    ``prev_hash`` linkage, canonical ``entry_hash`` recomputation, and the
    head row — a gap or any mismatch is a tamper alarm (there is no resync
    marker). Returns the closed-vocabulary status per chain; a broken chain
    is also alarmed server-side (``AUDIT_CHAIN_ALARM`` critical log).

    Chain selection: ``?all=true`` walks every chain; otherwise
    ``?tenant_id=<uuid>`` walks that tenant's chain; with neither, the
    system chain (NULL-tenant rows).
    """
    from app.services.audit.audit_chain import (
        SYSTEM_CHAIN_KEY,
        chain_key_for,
        verify_all_chains,
        verify_audit_chain,
    )

    if all_chains:
        statuses = await verify_all_chains(db)
        return AuditChainVerifyAllResponse(
            ok=all(status["ok"] for status in statuses),
            chains=[AuditChainStatus.model_validate(status) for status in statuses],
        )
    key = chain_key_for(tenant_id) if tenant_id is not None else SYSTEM_CHAIN_KEY
    status = await verify_audit_chain(db, key)
    return AuditChainVerifyResponse(chain=AuditChainStatus.model_validate(status))


@router.get("/seeds/export.zip")
async def export_seeds_zip(
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Download the running instance's global taxonomy/anatomy/catalog data as
    the **canonical seed pack** (``manifest.json`` + ``items/*.json``, ADR
    0056): every registered item file, deterministic (no timestamps, sorted,
    hashed) and contract-valid, so it can be registered back through the seed
    source lane and round-trips with zero writes.

    The download is read-only — it never touches the server's ``data/seeds/``.
    A maintainer transfers the ZIP to their dev machine and unpacks it into
    ``backend/data/seeds/`` (via ``scripts/unpack_seeds_zip.py``, which
    verifies every manifest sha256 pin and backs up existing files first),
    then reviews with ``git diff data/seeds/``.

    SYSTEM_ADMIN-only: seeds are the global canonical taxonomy, not tenant data.
    """
    zip_bytes = await SeedExportService(db, tenant_id=None).build_zip_bytes()
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="health-assistant-seed-pack.zip"',
        },
    )
