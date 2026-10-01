"""Pydantic schemas for OAuth2 client management.

The token endpoint speaks the OAuth2 client-credentials grant (RFC 6749 §4.4)
with SMART-on-FHIR scopes; the client-management endpoints let tenant
administrators register, rotate, and revoke facade clients.

See ``docs/API_LAYERS.md`` and ``docs/FHIR_R4_FACADE.md``.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OAuthClientBase(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=255)
    scopes: list[str] = Field(default_factory=list)


class OAuthClientCreate(OAuthClientBase):
    tenant_id: UUID | None = Field(
        None,
        description="Target tenant. Defaults to the caller's tenant; SYSTEM_ADMIN may specify any.",
    )
    bound_patient_id: UUID | None = Field(
        None,
        description="Required when granting any `patient/` scope; the client "
        "is then restricted to that single patient.",
    )


class OAuthClientUpdate(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=255)
    scopes: list[str] | None = None
    is_active: bool | None = None
    bound_patient_id: UUID | None = None


class OAuthClientResponse(BaseModel):
    """Public client representation — never carries the secret hash."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    client_id: str
    tenant_id: UUID
    display_name: str
    scopes: list[str]
    bound_patient_id: UUID | None = None
    is_confidential: bool
    is_active: bool
    created_by_user_id: UUID | None = None

    @classmethod
    def from_model(cls, client) -> "OAuthClientResponse":
        return cls(
            id=client.id,
            client_id=client.client_id,
            tenant_id=client.tenant_id,
            display_name=client.display_name,
            scopes=list(client.scopes) if client.scopes else [],
            bound_patient_id=client.bound_patient_id,
            is_confidential=client.is_confidential,
            is_active=client.is_active,
            created_by_user_id=client.created_by_user_id,
        )


class OAuthClientCreateResponse(OAuthClientResponse):
    """Returned once on creation; carries the plaintext secret."""

    client_secret: str = Field(
        ..., description="Plaintext secret — store now; it cannot be retrieved again."
    )


class OAuthTokenRequest(BaseModel):
    """Loose body mirror of the form-encoded token request (RFC 6749 §4.4).

    The endpoint accepts standard ``application/x-www-form-urlencoded`` too;
    this model is for callers that prefer JSON.
    """

    grant_type: str = Field("client_credentials")
    client_id: str | None = None
    client_secret: str | None = None
    scope: str | None = None


class OAuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str
    tenant_id: UUID
