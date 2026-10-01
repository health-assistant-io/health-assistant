"""Authentication schemas"""

from pydantic import BaseModel, ConfigDict, Field

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
    password: str = Field(..., min_length=6, max_length=100, description="User password")


class TokenResponse(BaseModel):
    """Token response schema"""

    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Token expiration time in seconds")

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class TokenRefresh(BaseModel):
    """Refresh request schema.

    ``refresh_token`` is optional for **browser** clients (§10): the
    rotating refresh JWT lives in the HttpOnly ``nx_refresh`` cookie
    (Path=/api/v1/auth), so ``POST /auth/refresh`` and ``POST
    /auth/logout`` read the cookie when the body omits the token. §9 user
    clients keep sending it in the body — both paths share the same
    rotation / revocation machinery.
    """

    refresh_token: str | None = Field(
        None, description="Refresh JWT — omitted by browsers (nx_refresh cookie)."
    )


class UserRegister(BaseModel):
    """User registration schema (invite-only).

    Joins an existing tenant as USER (or another role encoded in the
    invite token). Requires a valid invite token minted by that tenant's
    admin via ``POST /auth/invite``. First-run bootstrap lives in
    ``SetupRequest`` / ``POST /auth/setup`` instead. The route is
    additionally gated by ``HA_REGISTRATION_ENABLED`` (§12/§16).
    """

    email: str = Field(..., pattern=_LENIENT_EMAIL_PATTERN, description="User email address")
    password: str = Field(
        ..., min_length=10, max_length=100, description="Password (min 10 characters)"
    )
    full_name: str = Field(default="", max_length=200)
    tenant_id: str | None = Field(
        None, description="Tenant/Organization ID. If omitted, a new tenant is created."
    )
    invite_token: str | None = Field(
        None,
        description=(
            "Required when tenant_id is provided. Minted by POST /auth/invite "
            "by an admin of that tenant."
        ),
    )

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class SetupStatus(BaseModel):
    """First-run status — tells the frontend whether to show the setup
    wizard or the login screen.

    ``token_mode`` lets the wizard tailor the token hint per deployment
    (log-grep for ``log``, one-click launcher URL for ``env``, a time
    window hint for ``time``, hidden field for ``disabled``).

    ``setup_url_hint`` is non-null only in ``env`` mode when the wizard
    can autofill the token from the launcher URL — it contains the
    ``/setup?token=<value>`` ready to be handed to the user. The launcher
    uses this when it doesn't already compose the URL itself.

    ``demo_mode`` reflects the **DB instance fact**
    (``instance_settings.demo_mode`` — identity-auth §4/§13, init-only):
    the frontend then skips login entirely and auto-calls
    ``POST /auth/demo-login``.
    """

    initialized: bool = Field(..., description="True once at least one user exists in the system.")
    setup_token_required: bool = Field(
        ...,
        description=(
            "True when the setup wizard must present the one-time setup "
            "token (non-localhost, non-dev). False for local Docker/dev."
        ),
    )
    token_mode: str = Field(
        ...,
        description="Resolved SETUP_TOKEN_MODE: 'log' | 'env' | 'time' | 'disabled'.",
    )
    setup_url_hint: str | None = Field(
        None,
        description=(
            "Deprecated: always null. The endpoint never returns the setup "
            "token value — in 'env' mode the launcher (which holds the "
            "token) composes the one-click URL itself."
        ),
    )
    demo_mode: bool = Field(
        False,
        description=(
            "True when the instance's DB demo_mode fact is set (init-only "
            "HA_DEMO_MODE). The frontend auto-logs in via POST "
            "/auth/demo-login and skips the login form."
        ),
    )


class SetupRequest(BaseModel):
    """First-run setup payload — creates the initial SYSTEM_ADMIN + tenant."""

    email: str = Field(..., pattern=_LENIENT_EMAIL_PATTERN, description="Admin email address")
    password: str = Field(
        ..., min_length=10, max_length=100, description="Password (min 10 characters)"
    )
    full_name: str = Field(default="", max_length=200)
    tenant_name: str = Field(
        ..., min_length=1, max_length=120, description="Name for the initial tenant"
    )
    setup_token: str | None = Field(
        None,
        description=(
            "One-time setup token printed to the backend logs on first boot. "
            "Required when setup_token_required is true."
        ),
    )

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


# ---------------------------------------------------------------------------
# TOTP MFA (plan 16 H5)
# ---------------------------------------------------------------------------


class MFAVerifyRequest(BaseModel):
    """``POST /auth/mfa/verify`` — answer a login MFA challenge.

    ``mfa_token`` is the short-lived challenge JWT from the login 401;
    ``code`` is the current 6-digit TOTP code or one of the single-use
    recovery codes.
    """

    mfa_token: str = Field(..., min_length=1, description="Login MFA challenge token.")
    code: str = Field(
        ...,
        min_length=4,
        max_length=16,
        description="TOTP code or a single-use recovery code.",
    )


class MFAChallengeEnrollRequest(BaseModel):
    """``POST /auth/mfa/enroll`` — provisioning for forced enrollment.

    Used only when a login challenge carried ``enrollment_needed: true``
    (admin-forced MFA): returns the secret + otpauth URI + recovery
    codes so the user can enroll mid-login, then confirms via
    ``POST /auth/mfa/verify``.
    """

    mfa_token: str = Field(..., min_length=1, description="Login MFA challenge token.")


class MFAConfirmRequest(BaseModel):
    """``POST /me/mfa/confirm`` — activate a pending enrollment."""

    code: str = Field(
        ...,
        min_length=4,
        max_length=16,
        description="Current 6-digit TOTP code from the authenticator.",
    )


class MFADisableRequest(BaseModel):
    """``DELETE /me/mfa`` — password-confirmed MFA removal."""

    password: str = Field(..., min_length=1, description="Account password.")


class MFAEnrollResponse(BaseModel):
    """One-time enrollment payload — the plaintext secret and recovery
    codes exist only in this response (the server stores the
    Fernet-encrypted secret + bcrypt hashes)."""

    secret: str = Field(..., description="Base32 TOTP secret (20 random bytes).")
    uri: str = Field(..., description="otpauth:// provisioning URI.")
    recovery_codes: list[str] = Field(..., description="Single-use recovery codes — shown once.")


class MFAStatusResponse(BaseModel):
    """Self-service MFA status (``GET /me/mfa``)."""

    enabled: bool = Field(..., description="A confirmed TOTP secret is active.")
    enforced: bool = Field(..., description="An admin requires MFA for this account.")
    pending: bool = Field(..., description="An enrollment awaits confirmation.")
