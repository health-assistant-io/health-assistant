"""Request/response shapes — per-tenant SSO + SCIM tokens (Phase 12B)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import ValidationError as AppValidationError
from app.models.enums import Role
from app.services.auth.sso import normalize_allowed_domains, validate_group_role_map


def _field_value_error(exc: Exception) -> ValueError:
    """Re-wrap a service rule as a pydantic field error (FastAPI → 422)."""
    if isinstance(exc, AppValidationError):
        return ValueError(str(exc))
    return exc


class SSOConfigCreate(BaseModel):
    """Create one tenant SSO binding (the secret travels as a **slot name**)."""

    model_config = ConfigDict(extra="forbid")

    issuer: str = Field(min_length=8, max_length=500, description="IdP issuer URL")
    client_id: str = Field(min_length=1, max_length=255)
    client_secret_ref: str = Field(min_length=1, max_length=256)
    allowed_domains: list[str] = Field(default_factory=list, max_length=64)
    default_role: Role = Role.USER
    group_role_map: dict[str, str] = Field(default_factory=dict)
    jit_enabled: bool = True
    enabled: bool = False
    scopes: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("client_secret_ref")
    @classmethod
    def _slot_not_plaintext(cls, value: str) -> str:
        """The slot holds a name, never a secret value (S: secrets only keyring)."""
        lowered = value.strip().lower()
        if lowered.startswith(("-----begin", "eyJb")) or " " in value.strip():
            raise ValueError(
                "client_secret_ref must name a keyring/env slot, not carry a secret value"
            )
        return value.strip()

    @field_validator("allowed_domains")
    @classmethod
    def _domains(cls, value: list[str]) -> list[str]:
        normalized = normalize_allowed_domains(value)
        if not normalized:
            raise ValueError("at least one allowed domain is required")
        return normalized

    @field_validator("default_role")
    @classmethod
    def _role(cls, value: Role) -> Role:
        if value == Role.SYSTEM_ADMIN:
            raise ValueError("SYSTEM_ADMIN is never assignable via SSO")
        return value

    @field_validator("group_role_map")
    @classmethod
    def _map(cls, value: dict[str, str]) -> dict[str, str]:
        try:
            return validate_group_role_map(value)
        except AppValidationError as exc:
            raise _field_value_error(exc) from exc

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, value: list[str]) -> list[str]:
        cleaned = [str(s).strip() for s in value if str(s).strip()]
        if not cleaned:
            return []
        if any(" " in s for s in cleaned):
            raise ValueError("scopes must be individual scope strings (no spaces)")
        return cleaned[:16]


class SSOConfigUpdate(BaseModel):
    """Partial update (``enabled`` is the safe on/off switch)."""

    model_config = ConfigDict(extra="forbid")

    issuer: str | None = Field(default=None, min_length=8, max_length=500)
    client_id: str | None = Field(default=None, min_length=1, max_length=255)
    client_secret_ref: str | None = Field(default=None, min_length=1, max_length=256)
    allowed_domains: list[str] | None = Field(default=None, max_length=64)
    default_role: Role | None = None
    group_role_map: dict[str, str] | None = None
    jit_enabled: bool | None = None
    enabled: bool | None = None
    scopes: list[str] | None = Field(default=None, max_length=16)

    @field_validator("allowed_domains")
    @classmethod
    def _domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = normalize_allowed_domains(value)
        if not normalized:
            raise ValueError("at least one allowed domain is required")
        return normalized

    @field_validator("default_role")
    @classmethod
    def _role(cls, value: Role | None) -> Role | None:
        if value == Role.SYSTEM_ADMIN:
            raise ValueError("SYSTEM_ADMIN is never assignable via SSO")
        return value

    @field_validator("group_role_map")
    @classmethod
    def _map(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        try:
            return validate_group_role_map(value)
        except AppValidationError as exc:
            raise _field_value_error(exc) from exc


class SSOConfigResponse(BaseModel):
    """One binding (the secret appears only as its slot reference)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    issuer: str
    client_id: str
    client_secret_ref: str
    allowed_domains: list[str]
    default_role: Role
    group_role_map: dict[str, str]
    enabled: bool
    jit_enabled: bool
    scopes: list[str]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SSOConfigListResponse(BaseModel):
    items: list[SSOConfigResponse]
    total: int


class SSOAuthorizeRequest(BaseModel):
    """The login-page entry: which address (or tenant) wants to sign in."""

    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, max_length=255)
    tenant_slug: str | None = Field(default=None, max_length=80)


class SSOAuthorizeResponse(BaseModel):
    authorization_url: str
    issuer: str


class SSOLinkConfirm(BaseModel):
    """The authenticated link flow: the local user confirms the intent."""

    model_config = ConfigDict(extra="forbid")

    link_token: str = Field(min_length=16, max_length=256)


class SSOTestConnectionResponse(BaseModel):
    secret_resolved: bool
    discovery_ok: bool
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    error: str | None = None


class SCIMTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    expires_at: datetime | None = None


class SCIMTokenResponse(BaseModel):
    """Hash-free projection (the plaintext appears only on creation)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    scopes: list[str]
    active: bool
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None


class SCIMTokenListResponse(BaseModel):
    items: list[SCIMTokenResponse]
    total: int


class SCIMTokenCreatedResponse(SCIMTokenResponse):
    """The one-time creation response — ``token`` is the only plaintext."""

    token: str


class SSOConfigsByDomainResponse(BaseModel):
    """The login-page SSO availability answer (no tenant enumeration)."""

    sso_available: bool
