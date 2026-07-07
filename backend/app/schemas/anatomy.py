from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Dict, Any
from uuid import UUID
from app.models.enums import AnatomyRelationType, CodingSystem


class AnatomyStructureBase(BaseModel):
    name: str
    slug: str
    class_concept_id: Optional[UUID] = None
    standard_system: Optional[CodingSystem] = None
    standard_code: Optional[str] = None
    description: Optional[str] = None
    is_custom: bool = False
    display: Optional[Dict[str, Any]] = None


class AnatomyStructureCreate(AnatomyStructureBase):
    pass


class AnatomyStructureUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    class_concept_id: Optional[UUID] = None
    standard_system: Optional[CodingSystem] = None
    standard_code: Optional[str] = None
    description: Optional[str] = None
    is_custom: Optional[bool] = None
    display: Optional[Dict[str, Any]] = None


class AnatomyStructureResponse(AnatomyStructureBase):
    id: UUID
    tenant_id: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)


class AnatomyRelationBase(BaseModel):
    source_id: UUID
    target_id: UUID
    relation_type: AnatomyRelationType


class AnatomyRelationCreate(AnatomyRelationBase):
    pass


class AnatomyRelationResponse(AnatomyRelationBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)


class AnatomyGraphNode(AnatomyStructureResponse):
    """A node in the graph, optionally including its relations."""

    outgoing_relations: List[AnatomyRelationResponse] = []
    incoming_relations: List[AnatomyRelationResponse] = []


class AnatomyListResponse(BaseModel):
    items: List[AnatomyStructureResponse]
    total: int


class AnatomyRelatedNode(BaseModel):
    relation_type: AnatomyRelationType
    structure: AnatomyStructureResponse

    model_config = ConfigDict(from_attributes=True)


class AnatomyRelatedResponse(BaseModel):
    outgoing: List[AnatomyRelatedNode] = []
    incoming: List[AnatomyRelatedNode] = []


class AnatomyGraphEdge(BaseModel):
    source_id: UUID
    target_id: UUID
    relation_type: AnatomyRelationType


class AnatomyGraphNodeItem(AnatomyStructureResponse):
    """A graph node annotated with its hop distance (``depth``) from the root."""

    depth: int = 0


class AnatomyGraphResponse(BaseModel):
    root_id: UUID
    nodes: List[AnatomyGraphNodeItem] = []
    edges: List[AnatomyGraphEdge] = []


# --- Anatomy figures (DB-driven body atlas, raster images) ---


class AnatomyFigureResponse(BaseModel):
    id: UUID
    slug: str
    label: str
    figure_key: str
    view_key: str
    image_path: Optional[str] = None
    source_image_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    sort_order: int = 0
    is_active: bool = True
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None

    model_config = ConfigDict(from_attributes=True)
