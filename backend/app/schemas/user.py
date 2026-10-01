"""User schemas — identity-auth §5/§8/§12 shapes + Class S extensions."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class PublicUser(BaseModel):
    """The contract's ``PublicUser`` (identity-auth §12).

    Exactly ``{id, email, full_name, is_admin, is_active}`` — never hashes,
    counters, or lockout timestamps. ``is_admin`` is derived from the
    Class S role enum (ADMIN/SYSTEM_ADMIN — §17 keeps the roles).
    """

    id: UUID
    email: str
    full_name: str = ""
    is_admin: bool = False
    is_active: bool = True


class UserBase(BaseModel):
    """Base user schema"""

    email: str
    role: str = Field(default="user", description="User role: admin, manager, or user")


class UserCreate(UserBase):
    """User creation schema"""

    email: EmailStr
    password: str = Field(..., min_length=10, max_length=100)
    full_name: str = Field(default="", max_length=200)
    tenant_id: UUID | None = None


class UserUpdate(BaseModel):
    """User update schema"""

    email: EmailStr | None = None
    role: str | None = None
    settings: dict[str, Any] | None = None


class UserResponse(PublicUser):
    """Contract ``PublicUser`` + the Class S product extensions.

    ``role`` (SYSTEM_ADMIN/ADMIN/MANAGER/USER) and ``tenant_id`` are the
    frozen §17 tenancy extensions; ``settings`` is the product row
    payload. ``mfa_enabled`` / ``mfa_enforced`` (plan 16 H5) expose the
    TOTP state for the settings + admin Users UIs — booleans only, never
    the secret or recovery hashes.
    """

    role: str = Field(default="user", description="User role: admin, manager, or user")
    tenant_id: UUID
    settings: dict[str, Any] = Field(default_factory=dict)
    mfa_enabled: bool = Field(default=False, description="TOTP MFA is active.")
    mfa_enforced: bool = Field(default=False, description="An admin requires MFA.")

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class TokenData(BaseModel):
    """Schema for token payload data (identity-auth §8 claims).

    Standard claims: ``sub`` (user id — Class S also mirrors it in
    ``user_id``), ``email``, ``ver`` (``users.token_version``),
    ``auth_mode`` (local-boot | password | oidc | demo), ``token_kind``
    (session | refresh | api | invite | download), ``fid`` (the
    ``auth_sessions`` family id on session/refresh tokens).

    Switched-session claims (only present when a SYSTEM_ADMIN has used the
    tenant-switch surface to operate inside another tenant):
      * ``original_tenant_id`` — the admin's real tenant.
      * ``original_user_id``   — the admin's real user id.
      * ``switched``           — flag distinguishing a switched session.

    API-token claims (only present on OAuth2 client-credentials tokens issued
    for the FHIR facade — see ``docs/API_LAYERS.md``):
      * ``scope``      — space-separated SMART-on-FHIR scopes.
      * ``client_id``  — the OAuth client id.
      * ``aud`` / ``iss`` — JWT audience / issuer.

    All extra claims are optional so every kind still validates.
    """

    model_config = ConfigDict(extra="ignore")

    user_id: UUID | None = None
    tenant_id: UUID
    role: str = ""
    sub: str | None = None
    email: str | None = None
    ver: int | None = None
    auth_mode: str | None = None
    fid: str | None = None
    client_id: str | None = None
    original_tenant_id: UUID | None = None
    original_user_id: UUID | None = None
    switched: bool = False
    # API-token claims (defaults keep session tokens valid).
    token_kind: str = "session"
    scope: str = ""
    aud: Any | None = None
    iss: str | None = None
    bound_patient_id: UUID | None = None

    @property
    def scope_set(self) -> set[str]:
        """SMART scopes parsed into a set (empty for session tokens)."""
        return {s for s in (self.scope or "").split() if s}


class UserInDB(UserResponse):
    """User in database schema"""

    password_hash: str
