"""Request/response shapes for the patient access-grant surface (ADR 0054 Phase 2)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.patient_access_grant import GrantPermission, GrantSource, GrantStatus


class GrantCreate(BaseModel):
    """Create one grant (the source follows the caller: staff → care_team/admin,
    a patient owner → patient share; ``guardian`` is admin-only)."""

    model_config = ConfigDict(extra="forbid")

    grantee_user_id: UUID | None = None
    grantee_doctor_id: UUID | None = None
    permission: GrantPermission = GrantPermission.READ
    source: GrantSource | None = None
    expires_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=500)


class GrantUpdate(BaseModel):
    """Grantor-side update of permission / expiry / reason (no status move)."""

    model_config = ConfigDict(extra="forbid")

    permission: GrantPermission | None = None
    expires_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=500)


class GrantDecline(BaseModel):
    """Optional reason when declining a share request."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class GrantResponse(BaseModel):
    """One grant row (id-shaped; the patient label is resolved for lists)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    grantee_user_id: UUID | None = None
    grantee_doctor_id: UUID | None = None
    permission: GrantPermission
    source: GrantSource
    status: GrantStatus
    granted_by: UUID | None = None
    granted_at: datetime | None = None
    accepted_at: datetime | None = None
    expires_at: datetime | None = None
    reason: str | None = None
    grantee_label: str | None = None


class GrantListResponse(BaseModel):
    """The patient's grant ledger (the Access tab)."""

    items: list[GrantResponse]


class MeGrantItem(BaseModel):
    """Minimal grant view for ``/me/access`` and ``/me/access/grants``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    patient_name: str | None = None
    permission: GrantPermission
    source: GrantSource
    status: GrantStatus
    granted_at: datetime | None = None
    accepted_at: datetime | None = None
    expires_at: datetime | None = None


class MeGrantsResponse(BaseModel):
    """The caller's incoming grants (accepted) and pending requests."""

    active: list[MeGrantItem]
    pending: list[MeGrantItem]


def grant_to_response(grant: Any, grantee_label: str | None = None) -> GrantResponse:
    """Project a ``PatientAccessGrant`` row onto the wire shape."""
    return GrantResponse(
        id=grant.id,
        patient_id=grant.patient_id,
        grantee_user_id=grant.grantee_user_id,
        grantee_doctor_id=grant.grantee_doctor_id,
        permission=grant.permission,
        source=grant.source,
        status=grant.status,
        granted_by=grant.granted_by,
        granted_at=grant.granted_at,
        accepted_at=grant.accepted_at,
        expires_at=grant.expires_at,
        reason=grant.reason,
        grantee_label=grantee_label,
    )
