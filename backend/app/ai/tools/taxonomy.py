"""Taxonomy / knowledge-graph tools for the agentic chatbot.

Exposes the unified concept layer to the LLM: search concepts by name/alias,
explore graph relationships (one-hop neighbors), and look up what concepts
(taxonomy tags) are linked to a specific domain entity (e.g. "what panels
is LDL in?").

All tools are live-read-only — they never write. AI-proposed concepts/edges
go through the HITL proposal flow (``propose_*`` tools in ``hitl_proposals``).
"""

from __future__ import annotations

import json
from typing import Optional

from langchain_core.tools import tool

from app.ai.tools.registry import ToolContext, register_chat_tool


@register_chat_tool("taxonomy")
def build(ctx: ToolContext):
    """Build the taxonomy/knowledge-graph tools for this request."""

    @tool
    async def search_concepts(
        search_term: str,
        kind: Optional[str] = None,
    ) -> str:
        """Search the unified medical taxonomy (concepts) by name, slug, or alias.

        Typo-tolerant (trigram similarity). Optionally filter by kind:
        'specialty', 'examination_category', 'event_category', 'biomarker_class',
        'biomarker_panel', 'anatomy_class', 'medication_class', 'disease',
        'body_system', 'symptom', 'procedure', 'lifestyle', 'factor', 'organ',
        'vaccine_class', 'document_category'.

        Returns JSON: [{id, name, slug, kind, description, coding_system, code}].
        """
        from app.services.catalog_search_service import search_concepts as _search
        from app.models.enums import ConceptKind

        resolved_kind = None
        if kind:
            try:
                resolved_kind = ConceptKind(kind)
            except ValueError:
                pass

        results = await _search(
            ctx.db,
            ctx.tenant_id,
            search_term,
            kind=resolved_kind,
            limit=20,
        )
        return json.dumps(
            [
                {
                    "id": str(c.id),
                    "name": c.name,
                    "slug": c.slug,
                    "kind": c.primary_kind.value if c.primary_kind else None,
                    "description": c.description,
                    "coding_system": c.coding_system,
                    "code": c.code,
                }
                for c in results
            ]
        )

    @tool
    async def get_concept_neighborhood(
        concept_id: str,
        relation: Optional[str] = None,
    ) -> str:
        """Get the one-hop graph neighbors of a concept.

        Returns concepts connected to the given concept via typed edges
        (EXAMINES, PERFORMS, MEMBER_OF, TREATS, etc.). Optionally filter
        by a specific relation type.

        Returns JSON: [{edge_relation, direction, concept_id, concept_name, concept_kind}].
        """
        from app.services.concept_service import ConceptService
        from uuid import UUID

        svc = ConceptService(ctx.db)
        try:
            cid = UUID(concept_id)
        except ValueError:
            return json.dumps({"error": "Invalid concept_id UUID"})

        from app.models.enums import ConceptRelationType

        resolved_relation = None
        if relation:
            try:
                resolved_relation = ConceptRelationType(relation)
            except ValueError:
                pass

        neighbors = await svc.get_neighbors(
            cid,
            ctx.tenant_id,
            relation=resolved_relation,
        )
        return json.dumps(
            [
                {
                    "edge_relation": n["edge"].relation.value,
                    "direction": n["direction"],
                    # Polymorphic endpoint payload {type, id, label, kind, ...}
                    "concept_id": n["endpoint"]["id"] if n["endpoint"] else None,
                    "concept_name": n["endpoint"]["label"] if n["endpoint"] else None,
                    "concept_kind": n["endpoint"]["kind"] if n["endpoint"] else None,
                }
                for n in neighbors
            ]
        )

    @tool
    async def get_entity_concepts(
        entity_type: str,
        entity_id: str,
        relation: Optional[str] = None,
    ) -> str:
        """Look up taxonomy concepts linked to a domain entity.

        Answers questions like "what biomarker panels is this biomarker in?"
        or "what specialty does this doctor have?".

        entity_type: 'biomarker', 'doctor', 'examination', 'anatomy',
        'medication', 'clinical_event_type', 'observation', 'allergy',
        'immunization', 'document'.
        relation: optional filter (e.g. 'MEMBER_OF', 'HAS_SPECIALTY').

        Returns JSON: [{concept_id, name, slug, kind, relation}].
        """
        from app.services.concept_service import ConceptService
        from app.models.enums import EdgeEndpointType, ConceptRelationType
        from uuid import UUID

        try:
            et = EdgeEndpointType(entity_type)
            eid = UUID(entity_id)
        except ValueError as e:
            return json.dumps({"error": f"Invalid parameter: {e}"})

        resolved_relation = None
        if relation:
            try:
                resolved_relation = ConceptRelationType(relation)
            except ValueError:
                pass

        svc = ConceptService(ctx.db)
        concepts = await svc.get_entity_concepts(
            et,
            eid,
            ctx.tenant_id,
            relation=resolved_relation,
        )
        return json.dumps(
            [
                {
                    "concept_id": str(c.id),
                    "name": c.name,
                    "slug": c.slug,
                    "kind": c.primary_kind.value if c.primary_kind else None,
                }
                for c in concepts
            ]
        )

    return [search_concepts, get_concept_neighborhood, get_entity_concepts]
