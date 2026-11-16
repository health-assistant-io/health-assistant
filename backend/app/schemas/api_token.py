"""Scoped API-token wire shapes (ADR 0054 Phase 6, program 12C2B).

The plaintext appears exactly once — in :class:`ApiTokenCreated`, the
201 response of ``POST /me/api-tokens`` (and the rotate response). Every
other shape is the hash-free :meth:`~app.models.api_token.ApiToken.to_dict`
projection. Scope values are the closed ``all|granted|none`` vocabulary;
``patient_ids`` is meaningful only with ``granted``.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiTokenPatientScope(enum.StrEnum):
    """The closed patient-scope vocabulary of a token."""

    ALL = "all"
    GRANTED = "granted"
    NONE = "none"


def _validate_patient_scope(scope: str, patient_ids: list[UUID]) -> None:
    if scope == ApiTokenPatientScope.GRANTED.value and not patient_ids:
        raise ValueError("patient_scope='granted' requires at least one patient id")
    if scope != ApiTokenPatientScope.GRANTED.value and patient_ids:
        raise ValueError("patient_ids is only allowed with patient_scope='granted'")


class ApiTokenCreate(BaseModel):
    """Create one token (the permission list is validated against the owner)."""

    name: str = Field(min_length=1, max_length=200)
    permissions: list[str] = Field(min_length=1)
    patient_scope: ApiTokenPatientScope = ApiTokenPatientScope.ALL
    patient_ids: list[UUID] = Field(default_factory=list)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _scope_pairing(self):
        _validate_patient_scope(self.patient_scope.value, self.patient_ids)
        return self


class ApiTokenUpdate(BaseModel):
    """Update the mutable facts; absent fields are left untouched."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    permissions: list[str] | None = Field(default=None, min_length=1)
    patient_scope: ApiTokenPatientScope | None = None
    patient_ids: list[UUID] | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _scope_pairing(self):
        if self.patient_scope is not None:
            _validate_patient_scope(self.patient_scope.value, list(self.patient_ids or []))
        elif self.patient_ids:
            raise ValueError("patient_ids requires patient_scope='granted'")
        return self


class ApiTokenResponse(BaseModel):
    """Hash-free token projection (never includes the value or its hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    owner_user_id: UUID
    name: str
    token_prefix: str
    permissions: list[str]
    patient_scope: ApiTokenPatientScope
    patient_ids: list[UUID]
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    rotated_from: UUID | None = None
    created_by: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> ApiTokenResponse:
        """Project a model row through its hash-free ``to_dict``."""
        return cls.model_validate(row.to_dict())


class ApiTokenCreated(BaseModel):
    """The one-time creation/rotation response: plaintext + projection."""

    token: str
    api_token: ApiTokenResponse


class ApiTokenPermissionOption(BaseModel):
    """One assignable permission for the create/edit picker."""

    permission: str
    scope: str


class ApiTokenPermissionCatalog(BaseModel):
    """The owner's assignable permission catalog (the frontend contract)."""

    permissions: list[ApiTokenPermissionOption]
    patient_scopes: list[str]
