# ruff: noqa: B904 -- long immutable strings; reflow when touched
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enums import ExportScope, Role
from app.schemas.backup import BackupRequest, ExportJobListResponse, ExportJobResponse
from app.schemas.user import TokenData
from app.services.auth import step_up
from app.services.export.export_service import ExportService

router = APIRouter(prefix="/export", tags=["data-export"])


def _parse_uuid(v: str) -> UUID:
    try:
        return UUID(v)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid id")


def _authorize_scope(scope: ExportScope, current_user: TokenData, *, scoped: bool = False) -> None:
    role = current_user.role
    if scope == ExportScope.PATIENT:
        return
    if scope == ExportScope.GROUP:
        if role not in (Role.MANAGER.value, Role.ADMIN.value, Role.SYSTEM_ADMIN.value):
            raise HTTPException(
                status_code=403,
                detail="Group export requires MANAGER, ADMIN or SYSTEM_ADMIN role.",
            )
        return
    if scope == ExportScope.SYSTEM:
        # A whole-tenant archive is a bulk egress of every patient in the
        # tenant: a scoped credential (PAT / integration principal) never
        # creates one, whatever its owner's role name says — its reach is
        # narrowed at ``_validate_patient_scoping``, and there is no
        # patient-narrowed system scope.
        if scoped:
            raise HTTPException(
                status_code=403,
                detail="System export is not available to a scoped credential.",
            )
        if role not in (Role.ADMIN.value, Role.SYSTEM_ADMIN.value):
            raise HTTPException(
                status_code=403,
                detail="System export requires ADMIN or SYSTEM_ADMIN role.",
            )


async def _scoped_export_reach(
    current_user: TokenData, db: AsyncSession
) -> tuple[bool, set[UUID] | None]:
    """``(scoped, reach)`` — what a scoped credential may bulk-export.

    ``scoped`` is ``False`` for a session principal (no extra narrowing). A
    PAT/integration principal answers :func:`scoped_patient_reach` **without**
    grants (D5: a care-team read/write grant never widens export), so
    ``reach`` is the explicit allowlist intersected with the owner's reach,
    the empty set for ``patient_scope=none``, or ``None`` (tenant-wide) for
    ``all`` on a tenant-wide owner.
    """
    from app.core.authz.roles import is_scoped_principal
    from app.services.auth.access import scoped_patient_reach

    if not is_scoped_principal(current_user):
        return False, None
    return True, await scoped_patient_reach(current_user, db, include_grants=False)


def _job_within_reach(job, reach: set[UUID] | None) -> bool:
    """Whether a scoped credential's reach covers every patient in ``job``.

    A ``system`` job is the whole tenant and is never inside a credential's
    reach; a reach of ``None`` is the owner's tenant-wide reach (``all``
    scope), so only a job that really is tenant-wide would qualify — and that
    is the ``system`` scope we just excluded.
    """
    if job.scope == ExportScope.SYSTEM:
        return False
    if reach is None:
        return True
    try:
        job_patients = {UUID(str(pid)) for pid in (job.patient_ids or [])}
    except (TypeError, ValueError):
        return False
    return bool(job_patients) and job_patients <= reach


async def _authorize_job_access(job, current_user: TokenData, db: AsyncSession) -> None:
    """Object-level gate for export job status/download.

    Creating an export is role-gated, but the generated archive always
    contains every patient in its scope. A USER may therefore only see and
    download their own patient-scoped jobs; MANAGER/ADMIN may inspect the
    tenant's jobs, SYSTEM_ADMIN anything. A scoped credential answers its own
    patient reach instead: the role name it carries is its owner's, so a
    ``system`` job (or a group job naming a patient outside the reach) is
    hidden exactly like a cross-tenant job.
    """
    scoped, reach = await _scoped_export_reach(current_user, db)
    if scoped and not _job_within_reach(job, reach):
        raise HTTPException(status_code=404, detail="Export job not found")
    if current_user.role == Role.SYSTEM_ADMIN.value:
        return

    if current_user.role == Role.USER.value:
        if str(job.user_id) != str(current_user.user_id) or job.scope != ExportScope.PATIENT:
            raise HTTPException(status_code=404, detail="Export job not found")
        return
    # A system-scope backup contains every patient plus raw documents and
    # integration/AI sidecars: MANAGER (content) may not download it.
    if job.scope == ExportScope.SYSTEM and current_user.role not in (
        Role.ADMIN.value,
        Role.SYSTEM_ADMIN.value,
    ):
        raise HTTPException(status_code=404, detail="Export job not found")


async def _validate_patient_scoping(
    scope: ExportScope,
    patient_ids: list[str] | None,
    current_user: TokenData,
    db: AsyncSession,
    *,
    scoped: bool = False,
    reach: set[UUID] | None = None,
) -> list[str] | None:
    from app.services.auth.access import check_patient_export_access

    if scope == ExportScope.PATIENT:
        if not patient_ids:
            raise HTTPException(
                status_code=400,
                detail="patient_ids is required for patient-scoped export.",
            )
        if current_user.role == Role.USER.value and len(patient_ids) > 1:
            raise HTTPException(
                status_code=403,
                detail="USER role can only export a single patient (their own).",
            )
        # API-H2 (audit 2026-08): every requested patient is access-checked.
        # D5 (ADR 0054): export is NOT implied by a read grant — the check
        # is ownership/scope based, so a care-team read grant cannot bulk-
        # export someone else's record.
        for pid in patient_ids:
            await check_patient_export_access(pid, current_user, db)
    elif scope == ExportScope.GROUP:
        if not patient_ids:
            # A GROUP export without an explicit patient list would make
            # ``gather_patients`` return the whole tenant (the filter is
            # skipped for an empty list), i.e. an unfiltered archive a
            # MANAGER can download. Require the list, matching the
            # documented contract.
            raise HTTPException(
                status_code=400,
                detail="patient_ids is required for group-scoped export.",
            )
        # A scoped credential's group list is bounded by its patient reach
        # (ADR 0054 D8): an owner's role name never widens the archive past
        # the `patient_scope` the credential was issued with.
        if scoped and reach is not None:
            outside = [pid for pid in patient_ids if _parse_uuid(pid) not in reach]
            if outside:
                raise HTTPException(
                    status_code=403,
                    detail="You can only export patients in this credential's reach.",
                )
    return patient_ids


@router.post("", response_model=ExportJobResponse)
async def create_export(
    request: BackupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create an export/backup job.

    Scopes (SMART-compatible claim stored on the job):
    - `patient` — one patient (USER may export only their own; requires `patient_ids`).
    - `group` — multiple patients (`patient_ids` required; MANAGER+).
    - `system` — whole tenant (ADMIN+; `patient_ids` ignored).

    Types:
    - `fhir_only` — a portable FHIR R4B transaction Bundle (`.fhir.json`).
    - `full_backup` — a BagIt-style ZIP with the FHIR Bundle, non-FHIR sidecars,
      raw document files, and a SHA256 manifest.
    - `catalog_only` — biomarker/unit + clinical-event-type definitions (`.catalog.json`).

    A full-record archive is a bulk egress of PHI, so the call is
    **step-up gated** (program 12C1): the caller must first record a fresh
    ``export`` proof at ``POST /auth/step-up``, otherwise the response is
    ``403 {"code": "step_up_required", "action": "export"}``.
    """
    await step_up.enforce(current_user, step_up.StepUpAction.EXPORT)
    scoped, reach = await _scoped_export_reach(current_user, db)
    _authorize_scope(request.scope, current_user, scoped=scoped)
    patient_ids = await _validate_patient_scoping(
        request.scope,
        request.patient_ids,
        current_user,
        db,
        scoped=scoped,
        reach=reach,
    )

    tenant_id = _parse_uuid(str(current_user.tenant_id))
    user_id = _parse_uuid(str(current_user.user_id))
    svc = ExportService(db)

    job = await svc.create_job(
        user_id=user_id,
        tenant_id=tenant_id,
        scope=request.scope,
        export_type=request.export_type,
        patient_ids=patient_ids,
    )

    from app.workers.tasks import export_backup as export_backup_task

    export_backup_task.delay(str(job.id))
    return ExportJobResponse(**job.to_dict())


@router.get("/jobs", response_model=ExportJobListResponse)
async def list_export_jobs(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """List export jobs for the current tenant."""
    tenant_id = _parse_uuid(str(current_user.tenant_id))
    svc = ExportService(db)
    jobs = await svc.list_jobs(tenant_id, limit=limit)
    scoped, reach = await _scoped_export_reach(current_user, db)
    if scoped:
        jobs = [job for job in jobs if _job_within_reach(job, reach)]
    elif current_user.role == Role.USER.value:
        jobs = [
            job
            for job in jobs
            if str(job.user_id) == str(current_user.user_id) and job.scope == ExportScope.PATIENT
        ]
    elif current_user.role == Role.MANAGER.value:
        jobs = [job for job in jobs if job.scope != ExportScope.SYSTEM]
    return ExportJobListResponse(
        items=[ExportJobResponse(**j.to_dict()) for j in jobs], total=len(jobs)
    )


@router.get("/jobs/{job_id}", response_model=ExportJobResponse)
async def get_export_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get the status of an export job."""
    jid = _parse_uuid(job_id)
    tenant_id = _parse_uuid(str(current_user.tenant_id))
    svc = ExportService(db)
    job = await svc.get_job(jid, tenant_id)
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")
    await _authorize_job_access(job, current_user, db)
    return ExportJobResponse(**job.to_dict())


@router.get("/jobs/{job_id}/download")
async def download_export(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Download the generated export file (JWT-gated, tenant-scoped)."""
    jid = _parse_uuid(job_id)
    tenant_id = _parse_uuid(str(current_user.tenant_id))
    svc = ExportService(db)
    job = await svc.get_job(jid, tenant_id)
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")
    await _authorize_job_access(job, current_user, db)
    if not job.file_path:
        raise HTTPException(status_code=404, detail="Export file not available")

    # Dual-read: a pre-contract job may still point at a local file; a new job
    # stores its archive under the tenant's object prefix.
    from fastapi.responses import FileResponse as _FileResponse

    fallback = Path(job.file_path)
    if fallback.is_absolute() and fallback.exists():
        return _FileResponse(
            job.file_path,
            filename=fallback.name,
            media_type="application/octet-stream",
        )

    from fastapi.responses import StreamingResponse

    from app.storage.artifacts import ArtifactStorage, looks_like_key
    from app.storage.object_storage import ObjectNotFound

    if not looks_like_key(job.file_path):
        raise HTTPException(status_code=404, detail="Export file not available")
    storage = ArtifactStorage()
    if await storage.astat(job.file_path) is None:
        raise HTTPException(status_code=404, detail="Export file not available")
    filename = job.file_path.rsplit("/", 1)[-1]
    try:
        return StreamingResponse(
            storage.astream(job.file_path),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except ObjectNotFound:
        raise HTTPException(status_code=404, detail="Export file not available")
