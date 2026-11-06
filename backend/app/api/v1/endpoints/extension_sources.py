"""The extension source lane — admin surface (plan 7 W2).

SYSTEM_ADMIN-only: register sources, sync (resolve + register versions),
approve/revoke digests, and read the bundled registry's surfaced
registration failures (plan 37 6.1 — no log-and-skip). The heavy lifting
lives in :mod:`app.extensions.lifecycle`; this router only projects rows.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import RoleChecker
from app.extensions import lifecycle
from app.extensions.errors import ExtensionError
from app.models.extension_source_model import (
    ExtensionIsolationPosture,
    ExtensionSource,
    ExtensionSourceVersion,
)
from app.models.user_model import Role
from app.schemas.user import TokenData

logger = logging.getLogger(__name__)

router = APIRouter()


def _typed_409(exc: ExtensionError) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": exc.code, "message": exc.detail})


def _version_view(version: ExtensionSourceVersion, *, envelope: bool = True) -> dict[str, Any]:
    view = version.to_dict()
    if not envelope:
        view.pop("envelope", None)
    return view


async def _source_view(db: AsyncSession, source: ExtensionSource) -> dict[str, Any]:
    versions = (
        (
            await db.execute(
                select(ExtensionSourceVersion)
                .where(ExtensionSourceVersion.source_id == source.id)
                .order_by(ExtensionSourceVersion.first_seen_at.desc())
            )
        )
        .scalars()
        .all()
    )
    active = await lifecycle.active_version(db, source)
    return {
        **source.to_dict(),
        "ref_spec": source.ref_spec,
        "versions": [_version_view(v, envelope=False) for v in versions],
        "active_version_id": str(active.id) if active else None,
        "active_digest": active.digest if active else None,
    }


@router.get("")
async def list_sources(
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Every registered source with its version rows and the active digest."""
    sources = (
        (await db.execute(select(ExtensionSource).order_by(ExtensionSource.created_at)))
        .scalars()
        .all()
    )
    return [await _source_view(db, source) for source in sources]


@router.post("")
async def add_source(
    document: dict = Body(...),
    trust_tier: str = Body("T2"),
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Register a source (schema-validated descriptor + the lane's tier)."""
    try:
        source = await lifecycle.register_source(
            db, document=document, trust_tier=trust_tier, tenant_id=current_user.tenant_id
        )
        view = source.to_dict()
    except ExtensionError as exc:
        raise _typed_409(exc) from exc
    await db.commit()
    return view


@router.post("/{source_id}/sync")
async def sync_source(
    source_id: str,
    content_path: str | None = Body(None, embed=True),
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Resolve the source's content and register every manifest it carries.

    Local sources resolve ``content_path`` (allowlist-contained); git and
    registry sources answer typed ``not-ready`` until their transport lands.
    """
    source = await db.get(ExtensionSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    try:
        versions = await lifecycle.sync_source(
            db,
            source,
            content_override=Path(content_path) if content_path else None,
        )
        view = {"source_id": str(source.id), "versions": [_version_view(v) for v in versions]}
    except ExtensionError as exc:
        await db.commit()
        raise _typed_409(exc) from exc
    await db.commit()
    return view


@router.post("/versions/{version_id}/approve")
async def approve_version(
    version_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """``pending → approved`` — the explicit act; never an auto-upgrade."""
    version = await db.get(ExtensionSourceVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    try:
        await lifecycle.approve_version(db, version, approved_by=current_user.user_id)
        view = _version_view(version)
    except ExtensionError as exc:
        raise _typed_409(exc) from exc
    await db.commit()
    return view


@router.post("/versions/{version_id}/revoke")
async def revoke_version(
    version_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    version = await db.get(ExtensionSourceVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    try:
        await lifecycle.revoke_version(db, version)
        view = _version_view(version)
    except ExtensionError as exc:
        raise _typed_409(exc) from exc
    await db.commit()
    return view


@router.get("/versions/{version_id}/posture")
async def version_posture(
    version_id: str,
    current_user: TokenData = Depends(RoleChecker([Role.SYSTEM_ADMIN])),  # noqa: B008
    db: AsyncSession = Depends(get_db),
):
    """The recorded isolation posture rows for one version."""
    rows = (
        (
            await db.execute(
                select(ExtensionIsolationPosture).where(
                    ExtensionIsolationPosture.version_id == version_id
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="no posture row for this version")
    return [row.to_dict() for row in rows]
