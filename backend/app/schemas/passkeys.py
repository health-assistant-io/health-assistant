"""WebAuthn/passkey request and response schemas (program 12C2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PasskeyOptionsResponse(BaseModel):
    """PublicKeyCredential creation/request options for the browser."""

    options: dict[str, Any] = Field(
        ...,
        description=(
            "The WebAuthn options object exactly as the browser expects it: "
            "pass it to navigator.credentials.create({publicKey: options}) or "
            "navigator.credentials.get({publicKey: options})."
        ),
    )
    expires_in: int = Field(..., description="Challenge lifetime in seconds.")


class PasskeyRegistrationVerifyRequest(BaseModel):
    """``POST /me/mfa/passkeys/verify`` — attestation response."""

    credential: dict[str, Any] = Field(
        ...,
        description=(
            "The PublicKeyCredential JSON from navigator.credentials.create: "
            "{id, rawId, type, response: {clientDataJSON, attestationObject, "
            "transports?}, authenticatorAttachment?}."
        ),
    )
    name: str = Field(
        default="Passkey",
        min_length=1,
        max_length=200,
        description="A user label for the credential (e.g. 'MacBook Touch ID').",
    )


class PasskeyResponse(BaseModel):
    """One registered credential (never carries key material)."""

    id: str
    name: str
    transports: list[str] = Field(default_factory=list)
    aaguid: str | None = None
    backup_eligible: bool = False
    backup_state: bool = False
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    disabled: bool = False


class PasskeyListResponse(BaseModel):
    """``GET /me/mfa/passkeys``."""

    items: list[PasskeyResponse] = Field(default_factory=list)


class PasskeyAuthenticationOptionsRequest(BaseModel):
    """``POST /auth/passkeys/options``.

    ``email`` selects the account whose credentials are offered
    (``allowCredentials``). Omit it for a usernameless/discoverable
    ceremony; the assertion's credential then identifies the account.
    """

    email: str | None = Field(default=None, max_length=320)


class PasskeyAuthenticationVerifyRequest(BaseModel):
    """``POST /auth/passkeys/verify`` — assertion response."""

    credential: dict[str, Any] = Field(
        ...,
        description=(
            "The PublicKeyCredential JSON from navigator.credentials.get: "
            "{id, rawId, type, response: {clientDataJSON, authenticatorData, "
            "signature, userHandle?}}."
        ),
    )


class PasskeyStepUpOptionsRequest(BaseModel):
    """``POST /auth/step-up/passkeys/options``."""

    action: str = Field(
        ...,
        description=(
            "The step-up action to authorize: role_change | export | "
            "break_glass | grant_admin | mfa_change."
        ),
    )


class PasskeyStepUpVerifyRequest(BaseModel):
    """``POST /auth/step-up/passkeys/verify``."""

    action: str = Field(..., description="The same action the options call named.")
    credential: dict[str, Any] = Field(
        ..., description="The PublicKeyCredential JSON assertion response."
    )


class PasskeyStepUpOptionsResponse(PasskeyOptionsResponse):
    """Step-up options — echoes the action the proof will authorize."""

    action: str
