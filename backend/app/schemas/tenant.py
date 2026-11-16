"""Pydantic v2 schemas for the system-admin tenant management surface.

Two response shapes:
  * ``TenantResponse`` — the lean shape used in lists and cards.
  * ``TenantDetailResponse`` — extends ``TenantResponse`` with usage stats
    and the tenant owner; used by the detail page.

Switching carries its own response (``SwitchTenantResponse``) that hands the
frontend a fresh scoped JWT plus the original-tenant pointer so it can later
restore the admin's real session.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

_ROLE_VALUES = {"USER", "MANAGER", "ADMIN"}


class TenantBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None)
    settings: dict[str, Any] = Field(default_factory=dict)


class TenantCreate(TenantBase):
    owner_id: UUID | None = None


class TenantUpdate(BaseModel):
    """Partial update; every field optional."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=80)
    description: str | None = None
    settings: dict[str, Any] | None = None


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
    owner_id: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
    owner: UserSummary | None = None


class TenantListResponse(BaseModel):
    items: list[TenantResponse]
    total: int


class TenantUserResponse(BaseModel):
    id: UUID
    email: str
    role: str
    is_active: bool
    mfa_enabled: bool = False
    mfa_enforced: bool = False
    passkey_count: int = 0
    tenant_id: UUID
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TenantUserListResponse(BaseModel):
    items: list[TenantUserResponse]
    total: int


class UpdateTenantUser(BaseModel):
    """Role + active toggle for ``PATCH /admin/tenants/{id}/users/{user_id}``.

    SYSTEM_ADMIN cannot be granted here — it is bootstrap-only by design.
    """

    role: Literal["USER", "MANAGER", "ADMIN"] | None = None
    is_active: bool | None = None


class SetTenantUserMFA(BaseModel):
    """Admin-force MFA for ``PATCH /admin/tenants/{id}/users/{uid}/mfa``.

    ``enforced=true`` (plan 16 H5, "promoted for institute use"): the
    user's next password login requires TOTP enrollment before the
    challenge passes. Clearing it never disables an enrolled secret.
    """

    enforced: bool


class CreateInvitePayload(BaseModel):
    email: EmailStr | None = None
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
    """One ``audit_events`` row (identity-auth §17 shape + the product
    diff columns). ``outcome`` is ``ok``/``denied``/``error``. The chain
    fields (``chain_key``/``seq``/``prev_hash``/``entry_hash`` — ADR 0054
    D3) carry the row's tamper-evidence position."""

    id: UUID
    tenant_id: UUID | None = None
    user_id: UUID | None = None
    action: str
    resource_type: str
    resource_id: UUID | None = None
    outcome: str = "ok"
    old_value: dict[str, Any] | None = None  # gate-allow: *, audit diff
    new_value: dict[str, Any] | None = None
    created_at: str | None = None
    chain_key: str | None = None
    seq: int | None = None
    prev_hash: str | None = None
    entry_hash: str | None = None

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
    items: list[AuditEntryResponse]
    total: int


class AuditRetentionPolicyResponse(BaseModel):
    """One ``retention_policies`` row that governs audit data (Phase 8B
    retention view)."""

    scope: str
    scope_value: str
    action: str
    days: int
    timestamp_column: str
    enabled: bool

    model_config = ConfigDict(from_attributes=True)


class AuditRetentionResponse(BaseModel):
    """The retention view for ``audit_events`` (``GET /admin/audit/retention``).

    ``legal_holds`` is deliberately ``null``: there is no legal-hold
    registry yet (ADR 0054 D3 / plan D Phase 4 — documented, not deferred
    silently). Audit rows are never deleted by the app; the sanctioned
    lifecycle rewrites only the mutable payload columns (actor + diffs)
    outside the hash chain."""

    policies: list[AuditRetentionPolicyResponse]
    legal_holds: None = None
    notes: str = ""


class AuditChainStatus(BaseModel):
    """One audit chain's verification status (ADR 0054 D3, Phase 4A).

    ``reason`` is a closed vocabulary: ``ok``/``empty``/``gap``/
    ``prev_hash_mismatch``/``entry_hash_mismatch``/``missing_head``/
    ``head_seq_mismatch``/``head_hash_mismatch``. ``first_bad_seq`` names
    the first offending row (``None`` when the chain is intact or empty).
    """

    chain_key: str
    ok: bool
    entries: int
    head_seq: int | None = None
    first_bad_seq: int | None = None
    reason: str


class AuditChainVerifyResponse(BaseModel):
    """One chain's status (``GET /admin/audit/verify``)."""

    chain: AuditChainStatus


class AuditChainVerifyAllResponse(BaseModel):
    """Every chain's status (``GET /admin/audit/verify?all=true``)."""

    ok: bool
    chains: list[AuditChainStatus]


_POLICY_MFA_ROLES = ("ADMIN", "MANAGER", "USER")
_POLICY_IP_ALLOWLIST_MAX = 64


class TenantSecurityPolicyPatch(BaseModel):
    """Partial update for ``PATCH /tenants/{id}/security-policy`` (ADR 0054
    Phase 3). Every field optional: an omitted field is untouched; an
    explicit ``null`` **clears** the nullable knobs (the window/limit
    fields fall back to unset = feature off). ``mfa_required_roles``
    accepts only ``ADMIN``/``MANAGER``/``USER`` — ``SYSTEM_ADMIN`` is
    always required and can neither be listed nor waived. ``patient_sharing``
    is the policy field of the stored ``allow_patient_share`` settings key."""

    mfa_required_roles: list[str] | None = None
    session_idle_minutes: int | None = Field(default=None, ge=5, le=10080)
    session_max_hours: int | None = Field(default=None, ge=1, le=8760)
    concurrent_session_limit: int | None = Field(default=None, ge=1, le=100)
    ip_allowlist: list[str] | None = None
    patient_sharing: bool | None = None
    manager_scope: Literal["tenant", "assigned"] | None = None

    @field_validator("mfa_required_roles")
    @classmethod
    def _mfa_roles(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        invalid = sorted({role for role in value if role not in _POLICY_MFA_ROLES})
        if invalid:
            raise ValueError(
                f"mfa_required_roles accepts only {list(_POLICY_MFA_ROLES)}; "
                f"unknown or not policy-eligible: {invalid} (SYSTEM_ADMIN is "
                "always required and cannot be listed)"
            )
        return sorted(set(value), key=_POLICY_MFA_ROLES.index)

    @field_validator("ip_allowlist")
    @classmethod
    def _cidrs(cls, value: list[str] | None) -> list[str] | None:
        import ipaddress

        if value is None:
            return None
        if len(value) > _POLICY_IP_ALLOWLIST_MAX:
            raise ValueError(f"ip_allowlist accepts at most {_POLICY_IP_ALLOWLIST_MAX} CIDR ranges")
        canonical: list[str] = []
        for item in value:
            try:
                cidr = str(ipaddress.ip_network(item, strict=False))
            except (ValueError, TypeError):
                raise ValueError(f"invalid CIDR range: {item!r}") from None
            if cidr not in canonical:
                canonical.append(cidr)
        return canonical


class TenantSecurityPolicyResponse(BaseModel):
    """The normalized tenant security policy (``GET``/``PATCH``
    ``/tenants/{id}/security-policy``) — the 8C admin-page contract.
    ``system_admin_mfa_required`` is a constant ``true`` (informational:
    SYSTEM_ADMIN MFA can never be waived)."""

    tenant_id: UUID
    mfa_required_roles: list[str] = []
    system_admin_mfa_required: bool = True
    session_idle_minutes: int | None = None
    session_max_hours: int | None = None
    concurrent_session_limit: int | None = None
    ip_allowlist: list[str] = []
    patient_sharing: bool = True
    manager_scope: str = "tenant"
    updated_at: datetime | None = None


__all__ = [
    "_ROLE_VALUES",
    "AuditChainStatus",
    "AuditChainVerifyAllResponse",
    "AuditChainVerifyResponse",
    "AuditEntryResponse",
    "AuditListResponse",
    "AuditRetentionPolicyResponse",
    "AuditRetentionResponse",
    "CreateInvitePayload",
    "HardDeleteConfirm",
    "InviteResponse",
    "SwitchTenantResponse",
    "TenantBase",
    "TenantCreate",
    "TenantDetailResponse",
    "TenantListResponse",
    "TenantResponse",
    "TenantSecurityPolicyPatch",
    "TenantSecurityPolicyResponse",
    "TenantStats",
    "TenantUpdate",
    "TenantUserListResponse",
    "TenantUserResponse",
    "UpdateTenantUser",
    "UserSummary",
]
