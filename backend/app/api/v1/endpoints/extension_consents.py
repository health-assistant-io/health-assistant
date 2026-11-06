"""Consent — the user-facing surface of the source lane (plan 7 W3).

Reads serve the 8C change-summary (the capability delta between the running
digest and an incoming one); writes record one consent row per (user,
version, capability) after the host-derived consent echo verifies. Grants
never silently move across digests: an upgrade re-prompts for what is new
and carries unchanged grants forward explicitly.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.extensions import lifecycle
from app.extensions.errors import ExtensionError
from app.models.extension_source_model import ExtensionSourceVersion
from app.schemas.user import TokenData
from app.services.integration import extension_consent_service as consents

router = APIRouter()


def _typed_409(exc: ExtensionError) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": exc.code, "message": exc.detail})


async def _load_version(db: AsyncSession, version_id: str) -> ExtensionSourceVersion:
    version = await db.get(ExtensionSourceVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    return version


@router.get("/pending")
async def list_pending_consents(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Approved versions this user has not decided on — the consent inbox."""
    return await consents.pending_consents(
        db, user_id=current_user.user_id, tenant_id=current_user.tenant_id
    )


@router.get("/versions/{version_id}/delta")
async def consent_delta(
    version_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The 8C change-summary for one incoming version: new capabilities
    force the re-prompt, unchanged ones keep their grants."""
    incoming = await _load_version(db, version_id)
    from app.models.extension_source_model import ExtensionSource

    source = await db.get(ExtensionSource, incoming.source_id)
    running = None
    if source is not None:
        running = await lifecycle.active_version(db, source)
        if running is not None and running.id == incoming.id:
            running = None
    return await consents.consent_delta(
        db,
        user_id=current_user.user_id,
        tenant_id=current_user.tenant_id,
        incoming=incoming,
        running=running,
    )


@router.post("/versions/{version_id}")
async def decide_consent(
    version_id: str,
    decisions: dict[str, str] = Body(..., embed=True),
    echo: dict[str, Any] | None = Body(None, embed=True),
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Record one consent decision per capability, bound to THIS digest."""
    version = await _load_version(db, version_id)
    try:
        rows = await consents.record_decisions(
            db,
            user_id=current_user.user_id,
            tenant_id=current_user.tenant_id,
            version=version,
            decisions=decisions,
            decided_by=current_user.user_id,
            echo=echo,
        )
    except ExtensionError as exc:
        raise _typed_409(exc) from exc
    await db.commit()
    return {
        "versionId": str(version.id),
        "digest": version.digest,
        "decisions": {row.capability: row.decision.value for row in rows},
    }


@router.get("/versions/{version_id}/secrets")
async def version_secret_slots(
    version_id: str,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The version's declared x-secret slots (names + destinations only —
    never values) for the per-version sealing surface."""
    version = await _load_version(db, version_id)
    return {
        "versionId": str(version.id),
        "digest": version.digest,
        "slots": consents.declared_secret_slots(version),
    }
