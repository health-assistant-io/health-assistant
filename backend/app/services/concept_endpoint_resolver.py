"""Polymorphic endpoint resolver for ``concept_edges``.

A ``ConceptEdge`` row's endpoints are polymorphic — ``src_type``/``dst_type``
tag the table the UUID refers to (``concept`` → ``concepts.id``, ``anatomy``
→ ``anatomy_structures.id``, …). This module turns a bag of ``(type, id)``
pairs into a uniform, display-ready payload so the graph UI and the
recommendation engine don't each have to know how to fetch every entity table.

Single source of truth: this only **resolves references** for display. It
never copies entity rows into the concept table — organs stay in
``anatomy_structures``, biomarkers in ``biomarker_definitions``, etc.

Adding a new endpoint type = add one resolver function + register it in
``_RESOLVERS``. Unknown types / missing rows fall back to a
``"{type}:{id-prefix}"`` label so the graph never breaks on stale references.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition
from app.models.concept_model import Concept, ConceptKindTag
from app.models.enums import EdgeEndpointType
from app.models.examination_model import ExaminationModel


def _payload(
    etype: EdgeEndpointType,
    eid: Any,
    label: str,
    icon: Optional[dict] = None,
    color: Optional[str] = None,
    kind: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "type": etype.value,
        "id": str(eid),
        "label": label,
        "icon": icon,
        "color": color,
        "kind": kind,
    }


async def _resolve_concepts(
    db: AsyncSession, ids: List[UUID]
) -> Dict[UUID, Dict[str, Any]]:
    out: Dict[UUID, Dict[str, Any]] = {}
    rows = (
        await db.execute(
            select(Concept).where(Concept.id.in_(ids), Concept.deleted_at.is_(None))
        )
    ).scalars().all()
    for c in rows:
        out[c.id] = _payload(
            EdgeEndpointType.CONCEPT,
            c.id,
            c.name,
            icon=c.icon,
            color=c.color,
            kind=c.primary_kind.value if c.primary_kind else None,
        )
    return out


async def _resolve_anatomy(
    db: AsyncSession, ids: List[UUID]
) -> Dict[UUID, Dict[str, Any]]:
    """Resolve anatomy_structures; pull display color/kind from the row's
    ``class_concept`` (the anatomy_class concept, e.g. "organ")."""
    out: Dict[UUID, Dict[str, Any]] = {}
    rows = (
        await db.execute(select(AnatomyStructure).where(AnatomyStructure.id.in_(ids)))
    ).scalars().all()
    # Bulk-load any class concepts in one round-trip.
    concept_ids = {r.class_concept_id for r in rows if r.class_concept_id}
    class_map: Dict[UUID, Concept] = {}
    if concept_ids:
        class_rows = (
            await db.execute(select(Concept).where(Concept.id.in_(concept_ids)))
        ).scalars().all()
        class_map = {c.id: c for c in class_rows}
    for r in rows:
        cls = class_map.get(r.class_concept_id) if r.class_concept_id else None
        out[r.id] = _payload(
            EdgeEndpointType.ANATOMY,
            r.id,
            r.name,
            icon=cls.icon if cls else None,
            color=cls.color if cls else None,
            kind=cls.name if cls else None,
        )
    return out


async def _resolve_biomarkers(
    db: AsyncSession, ids: List[UUID]
) -> Dict[UUID, Dict[str, Any]]:
    out: Dict[UUID, Dict[str, Any]] = {}
    rows = (
        await db.execute(select(BiomarkerDefinition).where(BiomarkerDefinition.id.in_(ids)))
    ).scalars().all()
    concept_ids = {r.class_concept_id for r in rows if r.class_concept_id}
    class_map: Dict[UUID, Concept] = {}
    if concept_ids:
        class_rows = (
            await db.execute(select(Concept).where(Concept.id.in_(concept_ids)))
        ).scalars().all()
        class_map = {c.id: c for c in class_rows}
    for r in rows:
        cls = class_map.get(r.class_concept_id) if r.class_concept_id else None
        out[r.id] = _payload(
            EdgeEndpointType.BIOMARKER,
            r.id,
            r.name,
            color=cls.color if cls else None,
            kind=cls.name if cls else None,
        )
    return out


async def _resolve_examinations(
    db: AsyncSession, ids: List[UUID]
) -> Dict[UUID, Dict[str, Any]]:
    out: Dict[UUID, Dict[str, Any]] = {}
    rows = (
        await db.execute(select(ExaminationModel).where(ExaminationModel.id.in_(ids)))
    ).scalars().all()
    # Resolve each examination's category concept for a richer label.
    cat_ids = {r.category_concept_id for r in rows if r.category_concept_id}
    cat_map: Dict[UUID, Concept] = {}
    if cat_ids:
        cat_rows = (
            await db.execute(select(Concept).where(Concept.id.in_(cat_ids)))
        ).scalars().all()
        cat_map = {c.id: c for c in cat_rows}
    for r in rows:
        cat = cat_map.get(r.category_concept_id) if r.category_concept_id else None
        date_str = r.examination_date.isoformat() if r.examination_date else "no date"
        label = f"{cat.name + ' ' if cat else ''}Examination ({date_str})"
        out[r.id] = _payload(
            EdgeEndpointType.EXAMINATION,
            r.id,
            label,
            color=cat.color if cat else None,
            kind=cat.primary_kind.value if cat and cat.primary_kind else None,
        )
    return out


# Registry — add a new endpoint type by appending one entry.
_RESOLVERS = {
    EdgeEndpointType.CONCEPT: _resolve_concepts,
    EdgeEndpointType.ANATOMY: _resolve_anatomy,
    EdgeEndpointType.BIOMARKER: _resolve_biomarkers,
    EdgeEndpointType.EXAMINATION: _resolve_examinations,
}


async def resolve_endpoints(
    db: AsyncSession,
    pairs: List[Tuple[EdgeEndpointType, UUID]],
) -> Dict[UUID, Dict[str, Any]]:
    """Resolve a bag of polymorphic ``(type, id)`` pairs into display payloads.

    Returns ``{id: payload}`` keyed by the endpoint UUID. Anything that can't
    be resolved (unknown type, stale id, missing row) gets a fallback payload
    so callers never have to special-case None beyond "label-only".
    """
    by_type: Dict[EdgeEndpointType, List[UUID]] = defaultdict(list)
    for etype, eid in pairs:
        by_type[etype].append(eid)

    resolved: Dict[UUID, Dict[str, Any]] = {}
    for etype, ids in by_type.items():
        resolver = _RESOLVERS.get(etype)
        if resolver is None:
            # No dedicated resolver — emit fallbacks for the whole batch.
            for eid in ids:
                resolved[eid] = _payload(
                    etype, eid, f"{etype.value}:{str(eid)[:8]}", kind=None
                )
            continue
        resolved.update(await resolver(db, ids))

    # Backfill fallbacks for any ids the resolver didn't find (deleted rows).
    for etype, eid in pairs:
        if eid not in resolved:
            resolved[eid] = _payload(
                etype, eid, f"{etype.value}:{str(eid)[:8]}", kind=None
            )
    return resolved


__all__ = ["resolve_endpoints"]
