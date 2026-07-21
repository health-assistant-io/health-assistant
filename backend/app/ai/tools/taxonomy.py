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

        Hybrid search (trigram + full-text + RRF) — matches the name, slug,
        aliases (exact and substring), AND description text. Optionally
        filter by kind:
        'specialty', 'examination_category', 'event_category', 'biomarker_class',
        'biomarker_panel', 'anatomy_class', 'medication_class', 'disease',
        'body_system', 'symptom', 'procedure', 'lifestyle', 'factor', 'organ',
        'vaccine_class', 'document_category'.

        Returns JSON: [{id, name, slug, kind, description, coding_system, code,
        matched_on, snippet}].
        """
        from app.models.enums import ConceptKind
        from app.models.concept_model import Concept
        from app.services.catalog_search_service import (
            _hybrid_search_one,
            _specs_by_type,
        )
        from app.services.concept_service import concepts_with_kind
        from sqlalchemy import select

        resolved_kind = None
        if kind:
            try:
                resolved_kind = ConceptKind(kind)
            except ValueError:
                pass

        specs = _specs_by_type()
        spec = specs["concept"]

        # Kind filter: inject as extra static predicate (ids resolved up-front
        # from concept_kind_tags, never from user input).
        extra_sql = ""
        extra_params = {}
        if resolved_kind is not None:
            kind_rows = await ctx.db.execute(
                select(Concept.id).where(concepts_with_kind(resolved_kind))
            )
            kind_ids = [r[0] for r in kind_rows.all()]
            if not kind_ids:
                return json.dumps([])
            extra_sql = "AND t.id = ANY(:kind_ids)"
            extra_params["kind_ids"] = kind_ids

        hits = await _hybrid_search_one(
            ctx.db,
            spec,
            search_term,
            ctx.tenant_id,
            limit=20,
            extra_where_sql=extra_sql,
            extra_params=extra_params,
        )
        if not hits:
            return json.dumps([])
        # Bulk-fetch concept rows in rank order for the rich payload.
        rows = (
            (
                await ctx.db.execute(
                    select(Concept).where(Concept.id.in_([h.row_id for h in hits]))
                )
            )
            .scalars()
            .all()
        )
        by_id = {r.id: r for r in rows}
        out = []
        for h in hits:
            c = by_id.get(h.row_id)
            if c is None:
                continue
            out.append(
                {
                    "id": str(c.id),
                    "name": c.name,
                    "slug": c.slug,
                    "kind": c.primary_kind.value if c.primary_kind else None,
                    "description": c.description,
                    "coding_system": c.coding_system,
                    "code": c.code,
                    "matched_on": h.matched_on,
                    "snippet": h.snippet,
                    "score": h.score,
                }
            )
        return json.dumps(out)

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

    @tool
    async def get_link_schema(
        src_type: Optional[str] = None,
        dst_type: Optional[str] = None,
    ) -> str:
        """Discover which link relations the knowledge-graph supports.

        Use this BEFORE calling any ``propose_*`` tool with a ``links`` argument
        so you only propose relations the form will accept. Invalid combos are
        silently dropped by the propose tool, but using this upfront avoids the
        round-trip.

        Args:
            src_type: Optional EdgeEndpointType ('biomarker', 'medication',
                'allergy', 'vaccine', 'clinical_event_type', 'anatomy',
                'concept', 'doctor', 'examination', 'observation', 'document').
                When given, only relations FROM that type are returned.
            dst_type: Optional EdgeEndpointType. When given together with
                ``src_type``, returns the flat list of valid relations for
                that specific pair.

        Returns JSON:
            - With ``src_type`` and ``dst_type``: ``{"relations": ["TREATS", ...]}``
            - With ``src_type`` only: ``{"concept": ["TREATS", ...], "biomarker": [...]}``
            - With neither: ``[{"src_type": ..., "dst_type": ..., "relations": [...]}, ...]``
        """
        from app.ai.tools.propose_link import (
            relations_for,
            relations_for_source,
            serialize_full_schema,
        )
        from app.models.enums import EdgeEndpointType

        if src_type and dst_type:
            try:
                s = EdgeEndpointType(src_type)
                d = EdgeEndpointType(dst_type)
            except ValueError as exc:
                return json.dumps({"error": f"Invalid endpoint type: {exc}"})
            return json.dumps({"relations": relations_for(s, d)})
        if src_type:
            try:
                s = EdgeEndpointType(src_type)
            except ValueError as exc:
                return json.dumps({"error": f"Invalid endpoint type: {exc}"})
            return json.dumps(relations_for_source(s))
        return json.dumps(serialize_full_schema())

    return [
        search_concepts,
        get_concept_neighborhood,
        get_entity_concepts,
        get_link_schema,
    ]
