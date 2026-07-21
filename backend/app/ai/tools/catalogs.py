"""Catalog tools for the agentic chatbot.

Exposes the unified catalog meta-layer to the LLM:

- ``search_catalogs`` — ranked cross-catalog search (biomarkers, medications,
  vaccines, allergies, anatomy, diseases/concepts, …). Use before any specific
  catalog lookup to discover the right ``type`` + ``id``.
- ``explore_catalog_relations`` — multi-hop graph traversal over the
  polymorphic ``concept_edges`` graph. Answers "which organ does this biomarker
  affect?", "what diseases does this vaccine prevent?", "what medications treat
  this disease?".
- ``discover_missing_related`` — one-shot discovery helper for multi-step
  catalog creation. Given a primary entity and a list of likely-related items
  (e.g. "Metformin" + [TREATS "Type 2 Diabetes", AFFECTS "HbA1c"]), reports
  which exist and which are missing so the LLM can emit a single ``ask_user``
  multi_choice question listing the missing ones to create.

Both tools are pure read paths over the registry, so they auto-cover newly-
registered catalogs. All read-only — AI never writes; proposals go through HITL.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.tools import tool

from app.ai.tools.registry import ToolContext, register_chat_tool


#: Cap on the size of ``related`` in ``discover_missing_related``. Bounds the
#: work and the result payload so the LLM cannot bloat the chat context.
MAX_RELATED_ITEMS = 10


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

    @tool
    async def discover_missing_related(
        primary_type: str,
        primary_name: str,
        related: List[dict],
    ) -> str:
        """Discover which entities the LLM will need to create when planning a
        multi-step catalog addition with links.

        Given a PRIMARY entity (e.g. "Metformin", ``primary_type='medication'``)
        and a list of LIKELY-RELATED items (e.g. the disease it TREATS, the
        biomarker it AFFECTS), return which exist in the catalog and which are
        missing — so you can ask the user, in ONE ``ask_user`` multi_choice,
        which missing items to create alongside the primary.

        Use this when the user wants to create a primary entity that should
        link to related concepts/biomarkers. It saves you N+1 ``search_catalogs``
        calls — one round-trip discovers everything.

        Typical flow:
        1. Call this tool with the primary + related candidates.
        2. If any ``related[*].exists == false``, emit ONE ``ask_user`` with a
           ``multi_choice`` question listing the missing items as options
           (label=name, detail=type/relation, value=stable id like the name).
        3. On the resume turn (with the user's picks), emit parallel
           ``propose_define_*`` calls for the chosen items. STOP.
        4. On the second resume turn (with all defines confirmed and their
           ids), emit the primary ``propose_define_*`` call with ``links[]``
           populated from the confirmed ids + the suggested_relation values.

        Args:
            primary_type: Catalog type of the primary entity — one of
                ``biomarker``, ``medication``, ``vaccine``, ``allergy``,
                ``anatomy``, ``concept``, ``clinical_event_type``.
            primary_name: Canonical name of the primary entity (e.g. "Metformin").
            related: 1–10 items; each ``{"type": <catalog_type>, "name": <str>,
                "suggested_relation": <optional relation code like "TREATS">}``.
                The relation is for YOUR bookkeeping; the tool surfaces it
                back in the result so you can pass it to ``propose_define_*``
                later. The destination type must be a valid EdgeEndpointType
                pair-able with the primary (consult ``get_link_schema``).

        Returns JSON: ``{primary: {exists, match}, items: [{type, name,
        suggested_relation, exists, matches}]}``. ``matches`` is the top 3
        search hits per related item (so you can disambiguate "is this really
        missing, or did I name it slightly differently?").
        """
        from app.ai.tools.ask_user import ALLOWED_CATALOG_TYPES
        from app.services.catalog_search_service import search_catalogs as _search

        # Validate primary_type against the catalog whitelist.
        if primary_type not in ALLOWED_CATALOG_TYPES:
            return json.dumps(
                {
                    "error": f"primary_type {primary_type!r} is not a supported catalog "
                    f"type. Allowed: {sorted(ALLOWED_CATALOG_TYPES)}"
                }
            )
        if not primary_name or not str(primary_name).strip():
            return json.dumps({"error": "primary_name is required"})
        if not isinstance(related, list) or not related:
            return json.dumps({"error": "related must be a non-empty list"})
        if len(related) > MAX_RELATED_ITEMS:
            return json.dumps(
                {
                    "error": f"too many related items: {len(related)} > "
                    f"{MAX_RELATED_ITEMS}. Split into multiple batches or trim."
                }
            )

        # One primary lookup + one per related item. Search is tenant-scoped
        # via ctx.tenant_id inside _search.
        async def _lookup(catalog_type: str, name: str) -> List[Dict[str, Any]]:
            try:
                hits = await _search(
                    ctx.db,
                    ctx.tenant_id,
                    name,
                    types=[catalog_type],
                    limit_total=3,
                )
            except Exception:
                return []
            # Trim to the small, LLM-relevant fields.
            return [
                {
                    "id": str(h.get("id")),
                    "name": h.get("label") or h.get("name") or h.get("id"),
                    "slug": h.get("slug"),
                    "type": h.get("type") or catalog_type,
                    "matched_on": h.get("matched_on"),
                }
                for h in hits
            ]

        primary_matches = await _lookup(primary_type, primary_name)
        primary_block: Dict[str, Any] = {
            "type": primary_type,
            "name": primary_name,
            "exists": bool(primary_matches),
            "match": primary_matches[0] if primary_matches else None,
        }

        items: List[Dict[str, Any]] = []
        for raw in related:
            if not isinstance(raw, dict):
                items.append(
                    {
                        "type": None,
                        "name": None,
                        "suggested_relation": None,
                        "exists": False,
                        "matches": [],
                        "error": "related item must be an object",
                    }
                )
                continue
            r_type = str(raw.get("type", "")).strip()
            r_name = str(raw.get("name", "")).strip()
            r_rel = raw.get("suggested_relation")
            if r_type not in ALLOWED_CATALOG_TYPES:
                items.append(
                    {
                        "type": r_type or None,
                        "name": r_name or None,
                        "suggested_relation": r_rel,
                        "exists": False,
                        "matches": [],
                        "error": f"type {r_type!r} not supported",
                    }
                )
                continue
            if not r_name:
                items.append(
                    {
                        "type": r_type,
                        "name": None,
                        "suggested_relation": r_rel,
                        "exists": False,
                        "matches": [],
                        "error": "name is required",
                    }
                )
                continue
            matches = await _lookup(r_type, r_name)
            items.append(
                {
                    "type": r_type,
                    "name": r_name,
                    "suggested_relation": r_rel,
                    "exists": bool(matches),
                    "matches": matches,
                }
            )

        return json.dumps(
            {"primary": primary_block, "items": items}, default=str
        )

    return [search_catalogs, explore_catalog_relations, discover_missing_related]
