"""Scoped integration-principal wire shapes (ADR 0054 D8, Phase 13).

The secret appears exactly once — in :class:`IntegrationPrincipalCreated`, the
201 response of ``POST /integration-principals`` (and the rotate response).
Every other shape is the secret-free ``to_dict`` projection. Scope values are
permission catalog values; ``patient_ids`` is meaningful only with
``patient_scope='granted'``.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PrincipalPatientScope(enum.StrEnum):
    """The closed patient-scope vocabulary of a principal."""

    ALL = "all"
    GRANTED = "granted"
    NONE = "none"


class PrincipalKind(enum.StrEnum):
    """The closed principal-kind vocabulary."""

    INTEGRATION = "integration"
    EXTENSION = "extension"
    SERVICE = "service"


def _validate_patient_scope(scope: str, patient_ids: list[UUID]) -> None:
    if scope == PrincipalPatientScope.GRANTED.value and not patient_ids:
        raise ValueError("patient_scope='granted' requires at least one patient id")
    if scope != PrincipalPatientScope.GRANTED.value and patient_ids:
        raise ValueError("patient_ids is only allowed with patient_scope='granted'")


class IntegrationPrincipalCreate(BaseModel):
    """Create one principal (scopes are validated against the owner/ceiling)."""

    name: str = Field(min_length=1, max_length=200)
    kind: PrincipalKind = PrincipalKind.SERVICE
    integration_id: UUID | None = None
    owner_user_id: UUID | None = None
    scopes: list[str] = Field(min_length=1)
    patient_scope: PrincipalPatientScope = PrincipalPatientScope.ALL
    patient_ids: list[UUID] = Field(default_factory=list)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _coherence(self):
        _validate_patient_scope(self.patient_scope.value, self.patient_ids)
        if self.kind is PrincipalKind.INTEGRATION and self.integration_id is None:
            raise ValueError("kind='integration' requires integration_id")
        return self


class IntegrationPrincipalResponse(BaseModel):
    """Secret-free principal projection (never the value or its hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    owner_user_id: UUID
    name: str
    kind: PrincipalKind
    integration_id: UUID | None = None
    scopes: list[str]
    patient_scope: PrincipalPatientScope
    patient_ids: list[UUID]
    status: str
    secret_prefix: str | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    rotated_from: UUID | None = None
    created_by: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> IntegrationPrincipalResponse:
        """Project a model row through its secret-free ``to_dict``."""
        return cls.model_validate(row.to_dict())


class IntegrationPrincipalCreated(BaseModel):
    """The one-time creation/rotation response: secret + projection."""

    secret: str
    integration_principal: IntegrationPrincipalResponse


class IntegrationPrincipalPermissionOption(BaseModel):
    """One assignable permission for the create picker."""

    permission: str
    scope: str


class IntegrationPrincipalPermissionCatalog(BaseModel):
    """The assignable permission catalog for an integration (or the owner)."""

    permissions: list[IntegrationPrincipalPermissionOption]
    patient_scopes: list[str]
