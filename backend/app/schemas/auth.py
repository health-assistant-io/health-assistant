"""Authentication schemas"""

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from typing import Optional


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
    """User registration schema"""

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(
        ..., min_length=8, max_length=100, description="Password (min 8 characters)"
    )
    tenant_id: Optional[str] = Field(None, description="Tenant/Organization ID (optional)")

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
