"""Pydantic v2 schemas for the system-admin tenant management surface.

Two response shapes:
  * ``TenantResponse`` — the lean shape used in lists and cards.
  * ``TenantDetailResponse`` — extends ``TenantResponse`` with usage stats
    and the tenant owner; used by the detail page.

Switching carries its own response (``SwitchTenantResponse``) that hands the
frontend a fresh scoped JWT plus the original-tenant pointer so it can later
restore the admin's real session.
"""

from typing import Any, Dict, List, Literal, Optional
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


_ROLE_VALUES = {"USER", "MANAGER", "ADMIN"}


class TenantBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=255)
    slug: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None)
    settings: Dict[str, Any] = Field(default_factory=dict)


class TenantCreate(TenantBase):
    owner_id: Optional[UUID] = None


class TenantUpdate(BaseModel):
    """Partial update; every field optional."""

    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    slug: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class HardDeleteConfirm(BaseModel):
    """Body for ``DELETE /admin/tenants/{id}``.

    ``permanent`` must be ``true`` and ``confirm_name`` must equal the
    tenant's current name. The endpoint refuses otherwise — this is the
    same pattern GitHub/Vercel use to prevent catastrophic misclicks.
    """

    permanent: Literal[True]
    confirm_name: str = Field(..., min_length=1)


class TenantResponse(TenantBase):
    id: UUID
    is_active: bool
    owner_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TenantStats(BaseModel):
    users_count: int = 0
    active_users_count: int = 0
    patients_count: int = 0
    organizations_count: int = 0
    examinations_count: int = 0
    observations_count: int = 0
    documents_count: int = 0
    storage_bytes: int = 0


class UserSummary(BaseModel):
    id: UUID
    email: str
    role: str

    model_config = ConfigDict(from_attributes=True)


class TenantDetailResponse(TenantResponse):
    stats: TenantStats
    owner: Optional[UserSummary] = None


class TenantListResponse(BaseModel):
    items: List[TenantResponse]
    total: int


class TenantUserResponse(BaseModel):
    id: UUID
    email: str
    role: str
    is_active: bool
    tenant_id: UUID
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TenantUserListResponse(BaseModel):
    items: List[TenantUserResponse]
    total: int


class UpdateTenantUser(BaseModel):
    """Role + active toggle for ``PATCH /admin/tenants/{id}/users/{user_id}``.

    SYSTEM_ADMIN cannot be granted here — it is bootstrap-only by design.
    """

    role: Optional[Literal["USER", "MANAGER", "ADMIN"]] = None
    is_active: Optional[bool] = None


class CreateInvitePayload(BaseModel):
    email: Optional[EmailStr] = None
    role: Literal["USER", "MANAGER", "ADMIN"] = "USER"
    expires_days: int = Field(default=7, ge=1, le=30)


class InviteResponse(BaseModel):
    invite_token: str
    tenant_id: UUID
    role: str
    expires_in_days: int


class SwitchTenantResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    scoped_tenant_id: UUID
    original_tenant_id: UUID
    tenant: TenantResponse


class AuditEntryResponse(BaseModel):
    id: UUID
    user_id: Optional[UUID] = None
    action: str
    resource_type: str
    resource_id: Optional[UUID] = None
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("created_at", mode="before")
    @classmethod
    def _isoformat(cls, v):
        if v is None:
            return None
        try:
            return v.isoformat()
        except AttributeError:
            return str(v)


class AuditListResponse(BaseModel):
    items: List[AuditEntryResponse]
    total: int


__all__ = [
    "AuditEntryResponse",
    "AuditListResponse",
    "CreateInvitePayload",
    "HardDeleteConfirm",
    "InviteResponse",
    "SwitchTenantResponse",
    "TenantBase",
    "TenantCreate",
    "TenantDetailResponse",
    "TenantListResponse",
    "TenantResponse",
    "TenantStats",
    "TenantUpdate",
    "TenantUserListResponse",
    "TenantUserResponse",
    "UpdateTenantUser",
    "UserSummary",
    "_ROLE_VALUES",
]
