"""Pydantic schemas for Concept and ConceptEdge."""

from __future__ import annotations

from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConceptBase(BaseModel):
    slug: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    kinds: List[str] = Field(default_factory=list)
    primary_kind: Optional[str] = None
    # Legacy single-kind field — still accepted on write for backward
    # compatibility (wrapped to ``kinds=[kind]`` by the endpoint). Omitted
    # from responses in favor of ``kinds`` / ``primary_kind``.
    kind: Optional[str] = None
    parent_id: Optional[UUID] = None
    description: Optional[str] = None
    coding_system: Optional[str] = Field(None, max_length=50)
    code: Optional[str] = Field(None, max_length=100)
    aliases: List[str] = Field(default_factory=list)
    icon: Optional[dict] = None
    color: Optional[str] = Field(None, max_length=50)
    display_order: int = 0
    meta_data: Optional[dict] = None


class ConceptCreate(ConceptBase):
    tenant_scoped: bool = False


class ConceptUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[UUID] = None
    description: Optional[str] = None
    coding_system: Optional[str] = None
    code: Optional[str] = None
    aliases: Optional[List[str]] = None
    icon: Optional[dict] = None
    color: Optional[str] = None
    status: Optional[str] = None
    display_order: Optional[int] = None
    meta_data: Optional[dict] = None
    kinds: Optional[List[str]] = None
    primary_kind: Optional[str] = None


class ConceptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    kinds: List[str] = Field(default_factory=list)
    primary_kind: Optional[str] = None
    parent_id: Optional[UUID] = None
    description: Optional[str] = None
    coding_system: Optional[str] = None
    code: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    icon: Optional[dict] = None
    color: Optional[str] = None
    status: str
    display_order: int = 0
    meta_data: Optional[dict] = None
    tenant_id: Optional[UUID] = None
    version: Optional[int] = None
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None


class ConceptEdgeBase(BaseModel):
    src_type: str
    src_id: UUID
    dst_type: str
    dst_id: UUID
    relation: str
    properties: Optional[dict] = None
    evidence: Optional[dict] = None
    source: str = "manual"
    status: str = "approved"


class ConceptEdgeCreate(ConceptEdgeBase):
    tenant_scoped: bool = False


class ConceptEdgeResponse(ConceptEdgeBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: Optional[UUID] = None
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None


class ResolvedEndpointResponse(BaseModel):
    """A polymorphic edge endpoint resolved for display.

    Carries just enough to render a node/row — the entity table the id points
    into is identified by ``type``. The body stays in its source table (single
    source of truth); this is a display reference, not a copy.
    """

    type: str
    id: UUID
    label: str
    icon: Optional[dict] = None
    color: Optional[str] = None
    kind: Optional[str] = None


class NeighborResponse(BaseModel):
    """A one-hop neighbor entry: the edge + the resolved endpoint on the other end."""

    edge: ConceptEdgeResponse
    direction: str
    endpoint: Optional[ResolvedEndpointResponse] = None
