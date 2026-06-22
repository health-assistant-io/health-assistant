"""User schemas"""

from typing import Optional, Dict, Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ConfigDict


class UserBase(BaseModel):
    """Base user schema"""

    email: str
    role: str = Field(default="user", description="User role: admin, manager, or user")


class UserCreate(UserBase):
    """User creation schema"""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=100)
    tenant_id: Optional[UUID] = None


class UserUpdate(BaseModel):
    """User update schema"""

    email: Optional[EmailStr] = None
    role: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class UserResponse(UserBase):
    """User response schema"""

    id: UUID
    tenant_id: UUID
    settings: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class TokenData(BaseModel):
    """Schema for token payload data.

    Standard claims: ``user_id``, ``tenant_id``, ``role``, ``sub`` (email).

    Switched-session claims (only present when a SYSTEM_ADMIN has used the
    tenant-switch surface to operate inside another tenant):
      * ``original_tenant_id`` — the admin's real tenant.
      * ``original_user_id``   — the admin's real user id.
      * ``switched``           — flag distinguishing a switched session.

    All switch claims are optional so normal tokens still validate.
    """

    model_config = ConfigDict(extra="ignore")

    user_id: UUID
    tenant_id: UUID
    role: str
    sub: Optional[str] = None
    original_tenant_id: Optional[UUID] = None
    original_user_id: Optional[UUID] = None
    switched: bool = False

    @property
    def email(self) -> Optional[str]:
        return self.sub


class UserInDB(UserResponse):
    """User in database schema"""

    hashed_password: str
