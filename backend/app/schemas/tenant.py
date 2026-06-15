"""Tenant schemas"""

from typing import Optional, Dict, Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class TenantBase(BaseModel):
    """Base tenant schema"""

    name: str = Field(..., min_length=1, max_length=200, description="Tenant name")


class TenantCreate(TenantBase):
    """Tenant creation schema"""

    settings: Optional[Dict[str, Any]] = Field(default_factory=dict)


class TenantUpdate(BaseModel):
    """Tenant update schema"""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    settings: Optional[Dict[str, Any]] = None


class TenantResponse(TenantBase):
    """Tenant response schema"""

    id: UUID
    settings: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)
