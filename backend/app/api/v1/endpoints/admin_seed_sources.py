"""The seed source admin API — system + tenant sources surface (ADR 0056
Phase 4, program Phase 10A).

Routes (``/api/v1/admin/seed-sources``): list/create sources, sync (fetch +
register a pending digest), upload (file sources), version lifecycle
(approve/reject/revoke), the plan preview (read-only ``plan_only`` run),
the apply door (plan-only default, ``{"apply": true}`` executes; also the
explicit re-apply door for a PARTIAL bundled apply), adopt ("update
available" made explicit), disable/enable and retire (its items retire;
rows are never deleted).

Permissions: SYSTEM_ADMIN for the system lane, tenant ADMIN for their own
tenant overlays (the central gate requires ``seed_source:read|manage|approve``
at tenant scope; the row-level check here turns a cross-tenant id into a
hidden 404). Every mutation is audited. Errors are typed detail objects
(``{"code": ..., "message": ...}``) so the Sources UI can pattern-match.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import RoleChecker
from app.models.user_model import Role
from app.schemas.user import TokenData
from app.services.seed import sources_service
from app.services.seed.planner import PackPlan
from app.services.seed.sources_service import SeedSourceError
from app.services.seed.transport import SeedTransportError

logger = logging.getLogger(__name__)

router = APIRouter()


_STATUS_FOR_CODE = {
    "SOURCE_NOT_FOUND": 404,
    "FORBIDDEN_SCOPE": 403,
    "BUNDLED_IMMUTABLE": 409,
    "VERSION_NOT_PENDING": 409,
    "VERSION_NOT_APPROVED": 409,
    "ALREADY_RUNNING": 409,
    "PACK_INVALID": 422,
    "PACK_NOT_FOUND": 409,
    "SCOPE_MISMATCH": 409,
    "PRIORITY_MISMATCH": 409,
    "DIGEST_DRIFT": 409,
    "SECRET_MISSING": 409,
    "ADOPT_APPLY_FAILED": 409,
    "UPLOAD_TOO_LARGE": 413,
    "INVALID_KIND": 400,
    "INVALID_SCOPE": 400,
    "INVALID_PRIORITY": 400,
    "INVALID_DIGEST": 400,
    "URI_INVALID": 400,
    "REF_INVALID": 400,
    "SUBPATH_INVALID": 400,
}


def _typed_error(exc: Exception) -> HTTPException:
    """A typed detail object (``{"code", "message"}``) — never a bare string."""
    if isinstance(exc, SeedSourceError | SeedTransportError):
        code = exc.code
        message = str(exc).split(": ", 1)[-1]
    else:
        code = "SYNC_FAILED"
        message = str(exc)[:400]
    return HTTPException(
        status_code=_STATUS_FOR_CODE.get(code, 409), detail={"code": code, "message": message}
    )


class SeedSourceCreate(BaseModel):
    """One source registration (a tenant admin always creates tenant-scope
    sources; SYSTEM_ADMIN may pass ``"system"``)."""

    kind: str = Field(pattern="^(file|git|registry)$")
    priority: int
    scope: str = Field(default="", pattern="^(|system|tenant)$")
    uri: str | None = None
    ref_spec: str | None = None
    subpath: str | None = None
    auth_secret_ref: str | None = None
    tenant_id: str | None = None


class ApplyRequest(BaseModel):
    """The apply door: the default is the read-only plan preview."""

    apply: bool = False


class RejectRequest(BaseModel):
    reason: str | None = None


class AdoptRequest(BaseModel):
    seed_key: str | None = None


async def _version_view(db: AsyncSession, version) -> dict[str, Any]:
    from sqlalchemy import select

    from app.models.seed_source_model import SeedImportRun

    await db.refresh(version)
    latest = (
        await db.execute(
            select(SeedImportRun)
            .where(SeedImportRun.source_version_id == version.id)
            .order_by(SeedImportRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    manifest = version.manifest or {}
    view = version.to_dict()
    view["pack_id"] = manifest.get("id")
    view["pack_version"] = manifest.get("version")
    view["pack_scope"] = manifest.get("scope")
    view["pack_priority"] = manifest.get("priority")
    view["latest_run"] = (
        {
            "run_id": str(latest.id),
            "mode": latest.mode.value if latest.mode else None,
            "status": latest.status.value if latest.status else None,
            "started_at": latest.started_at.isoformat() if latest.started_at else None,
            "stats": latest.stats or {},
        }
        if latest
        else None
    )
    return view


@router.get("")
async def list_sources(
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008 -- framework default idiom
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """The actor's sources (system admins see everything; a tenant admin
    sees own-tenant overlays) with version + latest-run summaries."""
    return await sources_service.list_sources_for_actor(db, current_user)


@router.post("", status_code=201)
async def create_source(
    payload: SeedSourceCreate,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Register a source (the row is validated against the scope's priority
    band and the kind's URI grammar; sync/upload register versions)."""
    try:
        source = await sources_service.create_source(
            db,
            kind=payload.kind,
            priority=payload.priority,
            scope=payload.scope
            or ("tenant" if current_user.role != Role.SYSTEM_ADMIN else "system"),
            actor=current_user,
            uri=payload.uri,
            ref_spec=payload.ref_spec,
            subpath=payload.subpath,
            auth_secret_ref=payload.auth_secret_ref,
            tenant_id=payload.tenant_id,
        )
        view = await sources_service.source_view(db, source)
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return view


@router.post("/{source_id}/sync")
async def sync_source(
    source_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Fetch now and register a new digest as a pending version (per-source
    advisory lock; failures keep the pins and surface ``last_error``)."""
    try:
        source = await sources_service.get_source_for_actor(db, source_id, current_user)
        result = await sources_service.sync_source(db, source, actor=current_user)
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return result


@router.post("/{source_id}/upload")
async def upload_pack(
    source_id: str,
    file: UploadFile = File(...),
    pack_id: str | None = None,
    version: str | None = None,
    license: str = "CC0-1.0",
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Upload one pack for a ``file`` source (a full pack zip —
    ``manifest.json`` + ``items/*.json`` — or a single item-file JSON such
    as ``medications.json``). The body is read under the upload cap (typed
    ``UPLOAD_TOO_LARGE``/413 before any parsing); the parsed digest lands
    as a pending version.
    """
    from app.core.config import get_settings
    from app.services.documents.document_service import _read_capped

    try:
        content = await _read_capped(file, get_settings().MAX_UPLOAD_SIZE * 1024 * 1024)
    except HTTPException as exc:
        raise HTTPException(
            status_code=413,
            detail={"code": "UPLOAD_TOO_LARGE", "message": str(exc.detail)},
        ) from exc
    try:
        source = await sources_service.get_source_for_actor(db, source_id, current_user)
        version_row = await sources_service.register_upload(
            db,
            source,
            content=content,
            filename=file.filename or "upload.json",
            actor=current_user,
            pack_id=pack_id,
            version_str=version,
            license_id=license,
        )
        view = await _version_view(db, version_row)
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return view


@router.get("/{source_id}/versions")
async def list_versions(
    source_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """One source's versions (newest first) with their latest run."""
    from sqlalchemy import select

    from app.models.seed_source_model import SeedSourceVersion

    try:
        source = await sources_service.get_source_for_actor(db, source_id, current_user)
    except SeedSourceError as exc:
        raise _typed_error(exc) from exc
    versions = (
        (
            await db.execute(
                select(SeedSourceVersion)
                .where(SeedSourceVersion.source_id == source.id)
                .order_by(SeedSourceVersion.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [{**v.to_dict(), "is_current": source.current_version_id == v.id} for v in versions]


@router.post("/versions/{version_id}/approve")
async def approve_version(
    version_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """``pending → approved`` on the exact digest (the pointer advances only
    on a successful apply)."""
    try:
        version = await sources_service.get_version_for_actor(db, version_id, current_user)
        version = await sources_service.approve_version(db, version, actor=current_user)
        view = await _version_view(db, version)
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return view


@router.post("/versions/{version_id}/reject")
async def reject_version(
    version_id: str,
    payload: RejectRequest | None = None,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """``pending → rejected`` with a reason."""
    try:
        version = await sources_service.get_version_for_actor(db, version_id, current_user)
        version = await sources_service.reject_version(
            db, version, actor=current_user, reason=payload.reason if payload else None
        )
        view = await _version_view(db, version)
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return view


@router.post("/versions/{version_id}/revoke")
async def revoke_version(
    version_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """``approved → revoked`` — the previous approved version re-applies
    (restoring what the revoked version retired); with none, the source is
    left pointer-less."""
    try:
        version = await sources_service.get_version_for_actor(db, version_id, current_user)
        result = await sources_service.revoke_version(db, version, actor=current_user)
        result["version"] = await _version_view(db, version)
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return result


@router.get("/versions/{version_id}/plan")
async def plan_version(
    version_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The plan preview — a GET with one recorded side effect: it persists a
    ``plan``-mode run (the durable preview history) and writes nothing else.
    Pending versions plan too — the preview precedes approval."""
    try:
        version = await sources_service.get_version_for_actor(db, version_id, current_user)
        source = await sources_service.get_source_for_actor(
            db, str(version.source_id), current_user
        )
        plan: PackPlan = await sources_service.plan_version(db, source, version)
        doc = plan.to_doc()
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return doc


@router.post("/versions/{version_id}/apply")
async def apply_version(
    version_id: str,
    payload: ApplyRequest | None = None,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The apply door — **plan-only by default**; ``{"apply": true}`` runs
    the approval-checked apply under the advisory locks and returns the run
    ids + final statuses. Also the explicit re-apply door for a PARTIAL
    bundled apply (retry exactly the failed items)."""
    try:
        version = await sources_service.get_version_for_actor(db, version_id, current_user)
        source = await sources_service.get_source_for_actor(
            db, str(version.source_id), current_user
        )
        if payload is None or not payload.apply:
            plan: PackPlan = await sources_service.plan_version(db, source, version)
            await db.commit()
            return {"mode": "plan", "plan": plan.to_doc()}
        outcome = await sources_service.apply_version(db, source, version, actor=current_user)
        body = {
            "mode": "apply",
            "runs": {str(k): str(v) for k, v in outcome.runs.items()},
            "statuses": {str(k): s.value if s else None for k, s in outcome.statuses.items()},
            "stats": outcome.stats,
        }
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return body


@router.get("/{source_id}/runs")
async def list_runs(
    source_id: str,
    limit: int = 25,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """One source's run history (newest first, ≤100) — mode/status/timing,
    stats summary, error and the version digest each run applied. The durable
    history behind the Sources UI's per-source "runs" view."""
    from sqlalchemy import select

    from app.models.seed_source_model import SeedImportRun, SeedSourceVersion

    try:
        source = await sources_service.get_source_for_actor(db, source_id, current_user)
    except SeedSourceError as exc:
        raise _typed_error(exc) from exc
    limit = max(1, min(limit, 100))
    rows = (
        await db.execute(
            select(SeedImportRun, SeedSourceVersion.digest)
            .join(SeedSourceVersion, SeedSourceVersion.id == SeedImportRun.source_version_id)
            .where(SeedSourceVersion.source_id == source.id)
            .order_by(SeedImportRun.started_at.desc())
            .limit(limit)
        )
    ).all()
    out: list[dict[str, Any]] = []
    for run, digest in rows:
        view = run.to_dict()
        view["digest"] = digest
        out.append(view)
    return out


@router.get("/{source_id}/runs/{run_id}")
async def run_detail(
    source_id: str,
    run_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """One run in full: status/stats/error, the recorded plan document and
    the per-item outcomes (action + content hash + conflict payload — the
    durable loser/override record). A run from another source is the same
    hidden 404."""
    import uuid as uuid_mod

    from sqlalchemy import select

    from app.models.seed_source_model import SeedImportRun, SeedImportRunItem, SeedSourceVersion

    try:
        source = await sources_service.get_source_for_actor(db, source_id, current_user)
    except SeedSourceError as exc:
        raise _typed_error(exc) from exc
    try:
        rid = uuid_mod.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "no such run"}
        ) from exc
    row = (
        await db.execute(
            select(SeedImportRun, SeedSourceVersion.digest)
            .join(SeedSourceVersion, SeedSourceVersion.id == SeedImportRun.source_version_id)
            .where(SeedImportRun.id == rid, SeedSourceVersion.source_id == source.id)
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "no such run"}
        )
    run, digest = row
    items = (
        (
            await db.execute(
                select(SeedImportRunItem)
                .where(SeedImportRunItem.run_id == run.id)
                .order_by(SeedImportRunItem.created_at.asc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    view = run.to_dict()
    view["digest"] = digest
    view["items"] = [item.to_dict() for item in items]
    return view


@router.post("/{source_id}/adopt")
async def adopt_updates(
    source_id: str,
    payload: AdoptRequest | None = None,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Adopt "update available": clear ``user_modified`` (optionally only
    ``seed_key``) and re-apply the current version — the pack content wins."""
    try:
        source = await sources_service.get_source_for_actor(db, source_id, current_user)
        result = await sources_service.adopt_updates(
            db, source, actor=current_user, seed_key=payload.seed_key if payload else None
        )
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return result


@router.post("/{source_id}/disable")
async def disable_source(
    source_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Disable a source (it stops providing keys; the bundled lane refuses)."""
    try:
        source = await sources_service.get_source_for_actor(db, source_id, current_user)
        source = await sources_service.set_enabled(db, source, False, actor=current_user)
        view = await sources_service.source_view(db, source)
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return view


@router.post("/{source_id}/enable")
async def enable_source(
    source_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Re-enable a disabled source."""
    try:
        source = await sources_service.get_source_for_actor(db, source_id, current_user)
        source = await sources_service.set_enabled(db, source, True, actor=current_user)
        view = await sources_service.source_view(db, source)
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return view


@router.post("/{source_id}/retire")
async def retire_source(
    source_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.ADMIN, Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retire a source: its items retire (only where nothing else provides
    them; patient-referenced rows are kept), the source disables — **rows
    are never deleted**."""
    try:
        source = await sources_service.get_source_for_actor(db, source_id, current_user)
        result = await sources_service.retire_source(db, source, actor=current_user)
        result["source"] = await sources_service.source_view(db, source)
    except (SeedSourceError, SeedTransportError) as exc:
        await db.commit()
        raise _typed_error(exc) from exc
    await db.commit()
    return result
