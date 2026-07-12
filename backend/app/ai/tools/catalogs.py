"""Catalog tools for the agentic chatbot.

Exposes the unified catalog meta-layer to the LLM:

- ``search_catalogs`` — ranked cross-catalog search (biomarkers, medications,
  vaccines, allergies, anatomy, diseases/concepts, …). Use before any specific
  catalog lookup to discover the right ``type`` + ``id``.
- ``explore_catalog_relations`` — multi-hop graph traversal over the
  polymorphic ``concept_edges`` graph. Answers "which organ does this biomarker
  affect?", "what diseases does this vaccine prevent?", "what medications treat
  this disease?".

Both tools are pure read paths over the registry, so they auto-cover newly-
registered catalogs. All read-only — AI never writes; proposals go through HITL.
"""

from __future__ import annotations

import json
from typing import List, Optional
from uuid import UUID

from langchain_core.tools import tool

from app.ai.tools.registry import ToolContext, register_chat_tool


@register_chat_tool("catalogs")
def build(ctx: ToolContext):
    """Build the catalog search + graph-exploration tools for this request."""

    @tool
    async def search_catalogs(
        query: str,
        types: Optional[List[str]] = None,
        limit: int = 10,
    ) -> str:
        """Search across all clinical catalogs (biomarkers, medications,
        vaccines, allergies, anatomy, diseases, clinical event types).

        Hybrid search: typo-tolerant trigram matching over names/slugs/codes
        PLUS full-text search over descriptions/indications/info/aliases,
        fused via Reciprocal Rank Fusion. Returns ranked hits with rich
        metadata so you can decide relevance without a follow-up lookup.

        Use this before any specific catalog lookup to discover the right
        entity. Especially powerful for:
        - "find by symptom": query="headache" matches medications whose
          indications mention headache.
        - "find by alias": query="FBS" matches "Fasting Blood Sugar" via
          its alias.
        - "find by concept": query="diabetes" matches diseases AND
          medications AND biomarkers across catalogs in one call.

        Args:
            query: the search term (min 2 characters). Multi-word queries
                are supported ("high blood pressure").
            types: optional list of catalog types to restrict the search to
                (e.g. ["biomarker", "medication", "disease"]). None = all.
            limit: max total results (default 10).

        Returns JSON: [{type, id, label, description, matched_on, snippet,
        score, ...}]. The ``matched_on`` field lists which fields matched
        (e.g. ["name", "alias", "text"]); ``snippet`` is a short excerpt
        showing the match context when the description/info matched.
        """
        from app.services.catalog_search_service import search_catalogs as _search

        results = await _search(
            ctx.db,
            ctx.tenant_id,
            query,
            types=types,
            limit_total=limit,
        )
        return json.dumps(results, default=str)

    @tool
    async def explore_catalog_relations(
        type: str,
        id: str,
        max_depth: int = 2,
        relations: Optional[List[str]] = None,
    ) -> str:
        """Explore the knowledge graph around a catalog item.

        Answers questions like "which organ does this biomarker affect?",
        "what diseases does this vaccine prevent?", "what medications treat
        this disease?". Returns a pruned subgraph of typed edges reachable
        within ``max_depth`` hops.

        Args:
            type: the catalog type — "biomarker", "medication", "vaccine",
                "allergy", "anatomy", "concept", "clinical_event_type".
            id: the item UUID.
            max_depth: traversal depth 1–3 (default 2). 1 = direct neighbors.
            relations: optional whitelist of relation types (e.g.
                ["AFFECTS", "TREATS", "PREVENTS"]). None = all relations.

        Returns JSON: {start: {type, id, label}, nodes: [...], edges: [...]}
        where each edge is {id, src: {type, id}, dst: {type, id}, relation, status}.
        """
        from app.models.enums import ConceptRelationType, EdgeEndpointType
        from app.services.catalog_graph_service import traverse

        # Catalog type names (as exposed by the registry / search results) →
        # EdgeEndpointType enum values. "vaccine" is the catalog type but the
        # edge endpoint type is "immunization" — the alias keeps the LLM-facing
        # vocabulary consistent with search_catalogs results.
        _TYPE_ALIASES = {
            "vaccine": "immunization",
            "concept": "concept",
            "biomarker": "biomarker",
            "medication": "medication",
            "allergy": "allergy",
            "anatomy": "anatomy",
            "clinical_event_type": "clinical_event_type",
        }
        try:
            etype = EdgeEndpointType(_TYPE_ALIASES.get(type, type))
        except ValueError:
            return json.dumps({"error": f"Unknown catalog type: {type!r}"})
        try:
            eid = UUID(id)
        except (ValueError, TypeError):
            return json.dumps({"error": f"Invalid id UUID: {id!r}"})

        whitelist: Optional[tuple[ConceptRelationType, ...]] = None
        if relations:
            resolved = []
            for r in relations:
                try:
                    resolved.append(ConceptRelationType(r))
                except ValueError:
                    pass
            whitelist = tuple(resolved) if resolved else None

        graph = await traverse(
            ctx.db,
            start_type=etype,
            start_id=eid,
            tenant_id=ctx.tenant_id,
            max_depth=max_depth,
            relation_whitelist=whitelist,
        )
        return json.dumps(graph, default=str)

    return [search_catalogs, explore_catalog_relations]
