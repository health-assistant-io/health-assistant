from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from app.models.enums import AnatomyCategory, AnatomyRelationType, CodingSystem

class AnatomyImportNode(BaseModel):
    slug: str
    name: str
    category: AnatomyCategory
    standard_system: Optional[CodingSystem] = None
    standard_code: Optional[str] = None
    description: Optional[str] = None
    is_custom: bool = False
    display: Optional[Dict[str, Any]] = None

class AnatomyImportEdge(BaseModel):
    source_slug: str
    target_slug: str
    relation_type: AnatomyRelationType

class AnatomyImportPayload(BaseModel):
    nodes: List[AnatomyImportNode] = []
    edges: List[AnatomyImportEdge] = []
