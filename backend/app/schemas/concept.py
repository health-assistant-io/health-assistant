"""Pydantic schemas for Concept and ConceptEdge."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ConceptKind


def _validate_kind_values(kinds: list[str]) -> list[str]:
    for k in kinds:
        try:
            ConceptKind(k)
        except ValueError:
            valid = sorted(member.value for member in ConceptKind)
            raise ValueError(f"Invalid kind '{k}'. Valid: {valid}") from None
    return kinds


class ConceptBase(BaseModel):
    slug: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    kinds: list[str] = Field(default_factory=list)
    primary_kind: str | None = None
    # Legacy single-kind field — still accepted on write for backward
    # compatibility (wrapped to ``kinds=[kind]`` by the endpoint). Omitted
    # from responses in favor of ``kinds`` / ``primary_kind``.
    kind: str | None = None
    parent_id: UUID | None = None
    description: str | None = None
    coding_system: str | None = Field(None, max_length=50)
    code: str | None = Field(None, max_length=100)
    aliases: list[str] = Field(default_factory=list)
    icon: dict | None = None
    color: str | None = Field(None, max_length=50)
    display_order: int = 0
    meta_data: dict | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("kinds", mode="before")
    @classmethod
    def _validate_kinds(cls, v):
        if v is None:
            return []
        return _validate_kind_values(v)


class ConceptCreate(ConceptBase):
    tenant_scoped: bool = False

    @field_validator("kind", "primary_kind", mode="before")
    @classmethod
    def _validate_single_kinds(cls, v):
        if v is None:
            return None
        return _validate_kind_values([v])[0]


class ConceptUpdate(BaseModel):
    name: str | None = None
    parent_id: UUID | None = None
    description: str | None = None
    coding_system: str | None = None
    code: str | None = None
    aliases: list[str] | None = None
    icon: dict | None = None
    color: str | None = None
    status: str | None = None
    display_order: int | None = None
    meta_data: dict | None = None
    kinds: list[str] | None = None
    primary_kind: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("kinds", mode="before")
    @classmethod
    def _validate_kinds(cls, v):
        if v is None:
            return None
        return _validate_kind_values(v)

    @field_validator("primary_kind", mode="before")
    @classmethod
    def _validate_primary_kind(cls, v):
        if v is None:
            return None
        return _validate_kind_values([v])[0]

    @field_validator("status", mode="before")
    @classmethod
    def _validate_status(cls, v):
        if v is None:
            return None
        from app.models.enums import ConceptStatus

        try:
            return ConceptStatus(v)
        except ValueError:
            valid = sorted(member.value for member in ConceptStatus)
            raise ValueError(f"Invalid status '{v}'. Valid: {valid}") from None


class ConceptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    seed_key: str | None = None
    kinds: list[str] = Field(default_factory=list)
    primary_kind: str | None = None
    parent_id: UUID | None = None
    # Resolved parent slug (meta-layer reads + the unified write service).
    parent_slug: str | None = None
    description: str | None = None
    coding_system: str | None = None
    code: str | None = None
    aliases: list[str] = Field(default_factory=list)
    icon: dict | None = None
    color: str | None = None
    status: str
    display_order: int = 0
    meta_data: dict | None = None
    scope: str | None = None
    tenant_id: UUID | None = None
    version: int | None = None
    created_at: Any | None = None
    updated_at: Any | None = None


class ConceptEdgeBase(BaseModel):
    src_type: str
    src_id: UUID
    dst_type: str
    dst_id: UUID
    relation: str
    properties: dict | None = None
    evidence: dict | None = None
    source: str = "manual"
    status: str = "approved"


class ConceptEdgeCreate(ConceptEdgeBase):
    tenant_scoped: bool = False


class ConceptEdgeResponse(ConceptEdgeBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None = None
    created_at: Any | None = None
    updated_at: Any | None = None


class ResolvedEndpointResponse(BaseModel):
    """A polymorphic edge endpoint resolved for display.

    Carries just enough to render a node/row — the entity table the id points
    into is identified by ``type``. The body stays in its source table (single
    source of truth); this is a display reference, not a copy.
    """

    type: str
    id: UUID
    label: str
    icon: dict | None = None
    color: str | None = None
    kind: str | None = None


class NeighborResponse(BaseModel):
    """A one-hop neighbor entry: the edge + the resolved endpoint on the other end."""

    edge: ConceptEdgeResponse
    direction: str
    endpoint: ResolvedEndpointResponse | None = None
