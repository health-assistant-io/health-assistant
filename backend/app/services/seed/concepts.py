"""Concept, disease and concept-edge seeding (identity upsert — ADR 0055).

Concepts and diseases carry an immutable qualified ``seed_key``
(``concept:<kebab-key>``); the row id is the deterministic ``uuid5``
derived from it, so a rename never forks the row and cross-file parent
references resolve by id. Upsert is by id, then ``seed_key``
(``tenant_id IS NULL``) — the ``slug`` stays display data and is never a
join key.

``concept_edges.json`` items keep readable endpoint refs
(``src_slug``/``dst_slug`` with an optional ``src_type``/``dst_type``);
each ref resolves **by identity** — the uuid5 of its qualified seed key
(``concept:<slug>``, ``medication:<kebab-key>``, ``anatomy:<slug>``,
``biomarker:<slug>``, ``vaccine:<slug>``), never by name — and every
curated edge gets the deterministic id
``uuid5(SEED_KEY_NAMESPACE, "concept-edge:<src_type>:<src_key>/"
"<dst_type>:<dst_key>/<relation>")``.

Transaction ownership (ADR 0056): the seeders take a required session and
parsed pack items, flush, and never commit — the caller owns the
transaction.
"""

import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalog_identity import SEED_KEY_NAMESPACE, seed_key_uuid
from app.core.seed_pack.bundled import bundled_stage_items
from app.services.catalog.concept_service import sync_concept_kind_tags
from app.services.seed.identity import (
    apply_values,
    find_identity_row,
    identity_id,
    item_label,
    require_seed_key,
)

logger = logging.getLogger(__name__)

_JSON_FIELDS = ("aliases", "icon", "meta_data")
_OPTIONAL_FIELDS = ("description", "coding_system", "code", "icon", "color")


def _seed_edge_identity(
    src_ref: tuple[str, str], dst_ref: tuple[str, str], relation: str
) -> uuid.UUID:
    """The deterministic id of a curated concept edge.

    ``uuid5(SEED_KEY_NAMESPACE, "concept-edge:<src_type>:<src_key>/"
    "<dst_type>:<dst_key>/<relation>")`` — derived from the readable refs,
    so a reseed recreates the same edge id (computed directly rather than
    through ``seed_key_uuid``: relation labels are uppercase and the seed
    key body charset is lowercase-only).
    """
    src_type, src_key = src_ref
    dst_type, dst_key = dst_ref
    return uuid.uuid5(
        SEED_KEY_NAMESPACE,
        f"concept-edge:{src_type}:{src_key}/{dst_type}:{dst_key}/{relation}",
    )


def concept_values(item: dict[str, Any], parent_id: Any) -> dict[str, Any]:
    """The mutable fields of a concept seed item (parent only when resolved)."""
    values: dict[str, Any] = {"name": item["name"]}
    for field in _OPTIONAL_FIELDS:
        if item.get(field) is not None:
            values[field] = item[field]
    if item.get("aliases") is not None:
        values["aliases"] = item["aliases"]
    if item.get("meta_data") is not None:
        values["meta_data"] = item["meta_data"]
    if item.get("display_order") is not None:
        values["display_order"] = item["display_order"]
    # A parent reference is never nullified on re-seed: an unresolved
    # parent keeps the previously-resolved value (same rule as anatomy).
    if parent_id is not None:
        values["parent_id"] = parent_id
    return values


class ConceptSeedMixin:
    async def seed_concepts(
        self,
        session: AsyncSession,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Sync the unified concept taxonomy from the bundled (or given) items.

        The caller owns the session and the transaction (flush only, no
        commit).
        """
        if items is None:
            items = bundled_stage_items("concepts", self.seeds_dir)
        return await self._process_concepts(session, list(items))

    async def _process_concepts(
        self, session: AsyncSession, data: Sequence[dict[str, Any]]
    ) -> dict[str, int]:
        from app.models.concept_model import Concept, ConceptKindTag
        from app.models.enums import CatalogScope, ConceptKind, ConceptStatus

        stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}

        data = data if isinstance(data, list) else data.get("items", [])
        if not await self._table_exists(session, "concepts"):
            logger.error("Table 'concepts' does not exist. Skipping seeding.")
            return {"added": 0, "updated": 0, "unchanged": 0, "skipped": len(data), "errors": 0}

        slug_to_id: dict[str, Any] = {}

        async def _resolve_parent(parent_slug: str | None) -> Any:
            if not parent_slug:
                return None
            if parent_slug in slug_to_id:
                return slug_to_id[parent_slug]
            # Parents seeded by an earlier stage/file (e.g. diseases.json)
            # carry the same deterministic id — resolve by identity, not slug.
            derived = seed_key_uuid("concept", parent_slug)
            return await session.scalar(
                select(Concept.id).where(Concept.id == derived, Concept.tenant_id.is_(None))
            )

        for item in data:
            try:
                # Accept both the single "kind" and the "kinds" array.
                raw_kinds = item.get("kinds")
                if raw_kinds:
                    kinds = [ConceptKind(k) for k in raw_kinds]
                else:
                    kinds = [ConceptKind(item["kind"])]
                slug = item["slug"]
                qualified = require_seed_key(item, "concept")
                parent_id = await _resolve_parent(item.get("parent_slug"))
                if item.get("parent_slug") and parent_id is None:
                    self.note_unresolved_ref(
                        "concepts",
                        item,
                        f"parent_slug={item.get('parent_slug')}",
                    )

                db_concept = await find_identity_row(
                    session, Concept, qualified, explicit_id=item.get("id")
                )

                if db_concept:
                    values = concept_values(item, parent_id)
                    json_present = tuple(f for f in _JSON_FIELDS if f in values)
                    changed = apply_values(db_concept, values, json_present)
                    # Reconcile kind tags to the JSON's `kinds` — without this,
                    # changing a concept's kinds in the seed file and re-seeding
                    # would silently do nothing (the create path sets tags, the
                    # update path used to skip them).
                    desired_tags = {t.kind for t in (db_concept.kind_tags or [])}
                    desired_kinds = set(kinds)
                    tags_changed = (
                        desired_tags != desired_kinds
                        or db_concept.primary_kind not in desired_kinds
                    )
                    if tags_changed:
                        sync_concept_kind_tags(db_concept, kinds)
                        changed = True
                    stats["updated" if changed else "unchanged"] += 1
                    slug_to_id[slug] = db_concept.id
                else:
                    new_concept = Concept(
                        id=identity_id(qualified, item.get("id")),
                        seed_key=qualified,
                        slug=slug,
                        name=item["name"],
                        primary_kind=kinds[0],
                        tenant_id=None,
                        scope=CatalogScope.SYSTEM,
                        description=item.get("description"),
                        coding_system=item.get("coding_system"),
                        code=item.get("code"),
                        aliases=item.get("aliases", []),
                        icon=item.get("icon"),
                        color=item.get("color"),
                        display_order=item.get("display_order", 0),
                        meta_data=item.get("meta_data"),
                        parent_id=parent_id,
                        status=ConceptStatus.ACTIVE,
                    )
                    for k in kinds:
                        new_concept.kind_tags.append(ConceptKindTag(kind=k))
                    session.add(new_concept)
                    await session.flush()
                    stats["added"] += 1
                    slug_to_id[slug] = new_concept.id
            except Exception as e:
                self.note_item_error("concepts", item, e)
                logger.error(f"Error seeding concept {item_label(item)}: {e}")
                stats["errors"] += 1

        await session.flush()
        return stats

    async def seed_diseases(
        self,
        session: AsyncSession,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Sync disease reference concepts (``kind=disease``, ICD-10 codes).

        Diseases live as concepts — they inherit full CRUD/search/edges/FHIR
        for free via the concept layer. ``items`` defaults to the bundled
        pack's parsed diseases and flows through :meth:`_process_concepts` so
        the identity upsert + kind-tag reconciliation logic is shared. Must
        run AFTER :meth:`seed_concepts` (for any parent_slug refs) and BEFORE
        :meth:`seed_concept_edges` (so specialty/medication/vaccine → disease
        edges resolve). The caller owns the session and the transaction.
        """
        if items is None:
            items = bundled_stage_items("diseases", self.seeds_dir)
        return await self._process_concepts(session, list(items))

    async def seed_concept_edges(
        self,
        session: AsyncSession,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Sync concept relationships (edges) from the bundled (or given) items.

        The caller owns the session and the transaction (flush only, no
        commit).
        """
        if items is None:
            items = bundled_stage_items("concept_edges", self.seeds_dir)
        return await self._process_concept_edges(session, list(items))

    async def _process_concept_edges(
        self, session: AsyncSession, data: Sequence[dict[str, Any]]
    ) -> dict[str, int]:
        from app.models.concept_model import ConceptEdge
        from app.models.enums import (
            ConceptProvenance,
            ConceptRelationType,
            EdgeApprovalStatus,
            EdgeEndpointType,
        )

        stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}

        data = data if isinstance(data, list) else data.get("items", [])
        if not await self._table_exists(session, "concept_edges"):
            logger.error("Table 'concept_edges' does not exist. Skipping.")
            return {"added": 0, "updated": 0, "unchanged": 0, "skipped": len(data), "errors": 0}

        # Resolve a readable seed ref to an endpoint {type, id} **by identity**:
        # the deterministic uuid5 of the ref's qualified seed key first
        # (``concept:<slug>``, ``medication:<kebab-key>``, …), then the
        # ``seed_key`` column — never by name. Defaults to "concept" so
        # existing seed entries (which omit the type) keep working unchanged.
        # Add a branch here to support more polymorphic endpoint tables
        # (examination, …).
        from app.models.anatomy_model import AnatomyStructure
        from app.models.biomarker_model import BiomarkerDefinition
        from app.models.concept_model import Concept
        from app.models.fhir.medication import MedicationCatalog
        from app.models.fhir.vaccine import VaccineCatalog

        _ENDPOINT_TABLES = {
            "concept": Concept,
            "anatomy": AnatomyStructure,
            "biomarker": BiomarkerDefinition,
            "medication": MedicationCatalog,
            "vaccine": VaccineCatalog,
            # Exported files carry the enum tag ("immunization"), hand-written
            # ones the readable "vaccine" — both resolve to the vaccine catalog.
            "immunization": VaccineCatalog,
        }
        # The edge enum tags vaccines "immunization" (the patient-instance
        # endpoint type); the seed-key prefix stays "vaccine:".
        _ENDPOINT_TYPES = {
            "concept": EdgeEndpointType.CONCEPT,
            "anatomy": EdgeEndpointType.ANATOMY,
            "biomarker": EdgeEndpointType.BIOMARKER,
            "medication": EdgeEndpointType.MEDICATION,
            "vaccine": EdgeEndpointType.IMMUNIZATION,
            "immunization": EdgeEndpointType.IMMUNIZATION,
        }

        async def _resolve_endpoint(key: str, etype: str):
            if not key:
                return None
            model = _ENDPOINT_TABLES.get(etype)
            endpoint_type = _ENDPOINT_TYPES.get(etype)
            if model is None or endpoint_type is None:
                logger.warning(f"Unknown seed endpoint type '{etype}' (key={key}); skipping.")
                return None
            # The identity prefix stays the readable "vaccine" for both tags —
            # the seed key was minted as ``vaccine:<key>`` regardless of the
            # endpoint tag the file uses.
            identity_type = "vaccine" if etype == "immunization" else etype
            derived = seed_key_uuid(identity_type, key)
            eid = await session.scalar(
                select(model.id).where(model.id == derived, model.tenant_id.is_(None))
            )
            if eid is None and hasattr(model, "seed_key"):
                # A row created before the deterministic scheme keeps its id;
                # its seed_key column is the second identity lookup. Models
                # without the column (anatomy) resolve by the derived id only.
                eid = await session.scalar(
                    select(model.id).where(
                        model.seed_key == f"{identity_type}:{key}", model.tenant_id.is_(None)
                    )
                )
            if eid is None:
                logger.warning(
                    f"Seed edge endpoint {etype}:{key} resolves to no seeded row — skipped."
                )
                return None
            return (endpoint_type, eid)

        for item in data:
            try:
                src_type = item.get("src_type", "concept")
                dst_type = item.get("dst_type", "concept")
                relation = ConceptRelationType(item["relation"])

                src_ref = (src_type, item.get("src_slug"))
                dst_ref = (dst_type, item.get("dst_slug"))
                src = await _resolve_endpoint(item.get("src_slug"), src_type)
                dst = await _resolve_endpoint(item.get("dst_slug"), dst_type)

                if not src or not dst:
                    self.note_unresolved_ref(
                        "concept_edges",
                        item,
                        f"endpoints {src_type}:{item.get('src_slug')} -> "
                        f"{dst_type}:{item.get('dst_slug')}",
                        key=(
                            f"concept-edge:{src_type}:{item.get('src_slug')}/"
                            f"{dst_type}:{item.get('dst_slug')}/{relation.value}"
                        ),
                    )
                    stats["skipped"] += 1
                    continue

                src_etype, src_id = src
                dst_etype, dst_id = dst

                # Only an APPROVED tuple short-circuits: a pending/rejected
                # proposal between the same endpoints must not suppress the
                # curated seed edge (mirrors the vaccine seeder).
                existing = await session.scalar(
                    select(ConceptEdge.id).where(
                        ConceptEdge.src_type == src_etype,
                        ConceptEdge.src_id == src_id,
                        ConceptEdge.dst_type == dst_etype,
                        ConceptEdge.dst_id == dst_id,
                        ConceptEdge.relation == relation,
                        ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                        ConceptEdge.tenant_id.is_(None),
                    )
                )
                if existing is not None:
                    stats["unchanged"] += 1
                    continue

                edge = ConceptEdge(
                    id=_seed_edge_identity(src_ref, dst_ref, relation.value),
                    src_type=src_etype,
                    src_id=src_id,
                    dst_type=dst_etype,
                    dst_id=dst_id,
                    relation=relation,
                    tenant_id=None,
                    source=ConceptProvenance.SEED,
                    status=EdgeApprovalStatus.APPROVED,
                )
                session.add(edge)
                stats["added"] += 1
            except Exception as e:
                self.note_item_error("concept_edges", item, e)
                logger.error(f"Error seeding concept edge {item}: {e}")
                stats["errors"] += 1

        await session.flush()
        return stats
