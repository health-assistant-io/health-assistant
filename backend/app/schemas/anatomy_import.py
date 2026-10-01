from typing import Any

from pydantic import BaseModel

from app.models.enums import CodingSystem, ConceptRelationType


class AnatomyImportNode(BaseModel):
    slug: str
    name: str
    # Accepts either a concept slug (legacy ``category`` enum value mapped to
    # lowercase: ``organ``, ``system``, ``region``, ``organ-part``, ``tissue``,
    # ``joint``, ``other-anatomy``) or None. The import service resolves it to
    # a ``class_concept_id``.
    class_concept_slug: str | None = None
    standard_system: CodingSystem | None = None
    standard_code: str | None = None
    description: str | None = None
    is_custom: bool = False
    display: dict[str, Any] | None = None


class AnatomyImportEdge(BaseModel):
    source_slug: str
    target_slug: str
    relation_type: ConceptRelationType


class AnatomyImportPayload(BaseModel):
    nodes: list[AnatomyImportNode] = []
    edges: list[AnatomyImportEdge] = []
