"""Request/response shapes for the break-glass elevation surface (ADR 0054 D1)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.access_elevation import ElevationScope, ElevationStatus
from app.services.auth.elevations import MAX_DURATION_MINUTES


class ElevationRequest(BaseModel):
    """Open a break-glass request (reason + held-permission subset + duration)."""

    model_config = ConfigDict(extra="forbid")

    scope: ElevationScope = ElevationScope.TENANT
    permissions: list[str] = Field(min_length=1, max_length=64)
    duration_minutes: int = Field(ge=1, le=MAX_DURATION_MINUTES)
    reason: str = Field(min_length=1, max_length=1000)
    metadata: dict[str, Any] | None = None


class ElevationDecisionBody(BaseModel):
    """Optional note when denying (the elevation reason itself is immutable)."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class ElevationResponse(BaseModel):
    """One elevation row (id-shaped; the reason is returned to its viewers)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None = None
    requested_by: UUID
    approved_by: UUID | None = None
    scope: ElevationScope
    status: ElevationStatus
    permissions: list[str]
    reason: str
    duration_minutes: int
    requested_at: datetime
    approved_at: datetime | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class ElevationListResponse(BaseModel):
    """The admin elevation ledger (tenant rows; system rows for SYSTEM_ADMIN)."""

    items: list[ElevationResponse]
    total: int


def elevation_to_response(elevation) -> ElevationResponse:
    """Project one ORM row to the wire shape (``metadata`` rides the
    ``elevation_metadata`` attribute — the declarative ``metadata`` name is
    SQLAlchemy's own)."""
    return ElevationResponse(
        id=elevation.id,
        tenant_id=elevation.tenant_id,
        requested_by=elevation.requested_by,
        approved_by=elevation.approved_by,
        scope=ElevationScope(elevation.scope),
        status=ElevationStatus(elevation.status),
        permissions=list(elevation.permissions or []),
        reason=elevation.reason,
        duration_minutes=elevation.duration_minutes,
        requested_at=elevation.requested_at,
        approved_at=elevation.approved_at,
        starts_at=elevation.starts_at,
        expires_at=elevation.expires_at,
        revoked_at=elevation.revoked_at,
        metadata=elevation.elevation_metadata,
    )
