"""Authentication schemas"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional

# Lenient email pattern: one ``@`` with non-blank, whitespace-free text on
# both sides. We deliberately do NOT use ``EmailStr``/email-validator here
# because it rejects the ``user@localhost`` / ``user@host.local`` addresses
# that are normal on a self-hosted install (e.g. the run-dev default
# ``admin@healthassistant.local``). The email is a login identifier, not
# used for deliverability, so strict RFC/DSL validation is the wrong call.
_LENIENT_EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+$"


class LoginRequest(BaseModel):
    """Login request schema"""

    username: str = Field(..., description="User email address")
    password: str = Field(
        ..., min_length=6, max_length=100, description="User password"
    )


class TokenResponse(BaseModel):
    """Token response schema"""

    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Token expiration time in seconds")

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class TokenRefresh(BaseModel):
    """Token refresh request schema"""

    refresh_token: str = Field(..., description="JWT refresh token")


class UserRegister(BaseModel):
    """User registration schema (invite-only).

    Joins an existing tenant as USER (or another role encoded in the
    invite token). Requires a valid invite token minted by that tenant's
    admin via ``POST /auth/invite``. First-run bootstrap lives in
    ``SetupRequest`` / ``POST /auth/setup`` instead.
    """

    email: str = Field(
        ..., pattern=_LENIENT_EMAIL_PATTERN, description="User email address"
    )
    password: str = Field(
        ..., min_length=8, max_length=100, description="Password (min 8 characters)"
    )
    tenant_id: Optional[str] = Field(
        None, description="Tenant/Organization ID. If omitted, a new tenant is created."
    )
    invite_token: Optional[str] = Field(
        None,
        description=(
            "Required when tenant_id is provided. Minted by POST /auth/invite "
            "by an admin of that tenant."
        ),
    )

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class SetupStatus(BaseModel):
    """First-run status — tells the frontend whether to show the setup
    wizard or the login screen."""

    initialized: bool = Field(
        ..., description="True once at least one user exists in the system."
    )
    setup_token_required: bool = Field(
        ...,
        description=(
            "True when the setup wizard must present the one-time setup "
            "token (non-localhost, non-dev). False for local Docker/dev."
        ),
    )


class SetupRequest(BaseModel):
    """First-run setup payload — creates the initial SYSTEM_ADMIN + tenant."""

    email: str = Field(
        ..., pattern=_LENIENT_EMAIL_PATTERN, description="Admin email address"
    )
    password: str = Field(
        ..., min_length=8, max_length=100, description="Password (min 8 characters)"
    )
    tenant_name: str = Field(
        ..., min_length=1, max_length=120, description="Name for the initial tenant"
    )
    setup_token: Optional[str] = Field(
        None,
        description=(
            "One-time setup token printed to the backend logs on first boot. "
            "Required when setup_token_required is true."
        ),
    )

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
