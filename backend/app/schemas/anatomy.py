from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import CodingSystem, ConceptRelationType


class AnatomyStructureBase(BaseModel):
    name: str
    slug: str
    class_concept_id: UUID | None = None
    # Write-friendly: the anatomy-class concept slug (e.g. ``organ``). Resolved
    # to ``class_concept_id`` by the service, so callers don't need the UUID.
    class_concept_slug: str | None = None
    standard_system: CodingSystem | None = None
    standard_code: str | None = None
    description: str | None = None
    is_custom: bool = False
    display: dict[str, Any] | None = None


class AnatomyStructureCreate(AnatomyStructureBase):
    pass


class AnatomyStructureUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    class_concept_id: UUID | None = None
    class_concept_slug: str | None = None
    standard_system: CodingSystem | None = None
    standard_code: str | None = None
    description: str | None = None
    is_custom: bool | None = None
    display: dict[str, Any] | None = None


class AnatomyStructureResponse(AnatomyStructureBase):
    id: UUID
    class_concept_name: str | None = None
    scope: str
    tenant_id: UUID | None = None
    created_by: UUID | None = None

    model_config = ConfigDict(from_attributes=True)


class AnatomyRelationBase(BaseModel):
    source_id: UUID
    target_id: UUID
    relation_type: ConceptRelationType


class AnatomyRelationCreate(AnatomyRelationBase):
    pass


class AnatomyRelationResponse(AnatomyRelationBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)


class AnatomyGraphNode(AnatomyStructureResponse):
    """A node in the graph, optionally including its relations."""

    outgoing_relations: list[AnatomyRelationResponse] = []
    incoming_relations: list[AnatomyRelationResponse] = []


class AnatomyListResponse(BaseModel):
    items: list[AnatomyStructureResponse]
    total: int


class AnatomyRelatedNode(BaseModel):
    relation_type: ConceptRelationType
    structure: AnatomyStructureResponse

    model_config = ConfigDict(from_attributes=True)


class AnatomyRelatedResponse(BaseModel):
    outgoing: list[AnatomyRelatedNode] = []
    incoming: list[AnatomyRelatedNode] = []


class AnatomyGraphEdge(BaseModel):
    source_id: UUID
    target_id: UUID
    relation_type: ConceptRelationType


class AnatomyGraphNodeItem(AnatomyStructureResponse):
    """A graph node annotated with its hop distance (``depth``) from the root."""

    depth: int = 0


class AnatomyGraphResponse(BaseModel):
    root_id: UUID
    nodes: list[AnatomyGraphNodeItem] = []
    edges: list[AnatomyGraphEdge] = []


# --- Anatomy figures (DB-driven body atlas, raster images) ---


class AnatomyFigureResponse(BaseModel):
    id: UUID
    slug: str
    label: str
    figure_key: str
    view_key: str
    image_path: str | None = None
    source_image_path: str | None = None
    width: int | None = None
    height: int | None = None
    sort_order: int = 0
    is_active: bool = True
    created_at: Any | None = None
    updated_at: Any | None = None

    model_config = ConfigDict(from_attributes=True)
