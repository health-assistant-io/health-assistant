"""Seed export service — the inverse of :class:`SeedService`.

Serializes the running instance's taxonomy / anatomy / biomarker / catalog data
back into the identity-keyed ``data/seeds/*.json`` format so a curator can
build up the canonical set via the UI + AI, then snapshot it into the shipped
seeds.

Identity model (ADR 0055): every curated item exports its ``id`` and its
immutable ``seed_key`` (``<type>:<kebab-key>``); ``coding_system``/``code``
are attributes in the item body where present; the ``slug`` is display data
and never a join key. Seeders upsert by id, then ``seed_key`` — never by
name or slug — so an export re-ingests into the same rows with zero writes
(the round-trip test pins this). Vaccine → disease links are graph data: they
export as the readable ``target_diseases`` list of ``vaccines.json``, derived
from the approved ``PREVENTS`` edges (the reverse of the vaccine seeder);
``concept_edges.json`` excludes those edges to keep one owner per dataset.

Source scope: pass ``tenant_id=None`` (default) to export the global taxonomy
(``tenant_id IS NULL`` rows — what ships), or a specific ``tenant_id`` to treat
that tenant as a template (emit its rows with scope stripped).

Determinism: every file is sorted by its natural identity key (``seed_key``,
falling back to slug/name for rows outside the curated identity), uses a fixed
field order, and emits a metadata block with **no timestamps** — re-exporting
an unchanged DB is a byte-level no-op (the CI ``--check`` gate pins this).

Canonical pack: :meth:`SeedExportService.build_pack` wraps the exported files
as one canonical seed pack (``manifest.json`` + ``items/*.json``, ADR 0056)
covering **every** pack item file — including ``biomarker_states.json`` and
``omb_race_ethnicity.json`` (the OMB picklist flows through the pack path, so
it exports as a normalized pass-through). The whole-pack digest is stable and
the pack validates through the same contract as the bundled one, so an export
can be registered/uploaded back through the source lane and round-trips with
zero writes.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalog_identity import seed_key_uuid
from app.core.seed_pack import (
    MANIFEST_NAME,
    PACK_SCHEMA_VERSION,
    SeedPackInvalid,
    content_digest,
    validate_pack,
)
from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition, BiomarkerState, Unit
from app.models.clinical_event import ClinicalEventType
from app.models.concept_model import Concept, ConceptEdge
from app.models.enums import (
    ConceptKind,
    ConceptRelationType,
    EdgeApprovalStatus,
    EdgeEndpointType,
)
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.vaccine import VaccineCatalog
from app.services.catalog.concept_service import concepts_with_kind

logger = logging.getLogger(__name__)

SEED_VERSION = "1.0.0"
SEED_SOURCE = "exported-from-instance"

EXPORT_PACK_ID = "exported-instance-seeds"
EXPORT_PACK_LICENSE = "CC-BY-SA-4.0"
EXPORT_PACK_DESCRIPTION = (
    "Canonical export of the running instance's curated catalog "
    "(concepts, diseases, medications, vaccines, allergies, clinical event "
    "types, anatomy structures, biomarker states, default catalog, edges, "
    "panels, OMB picklist). Deterministic: no timestamps, sorted, hashed."
)


class SeedExportService:
    def __init__(
        self,
        db: AsyncSession,
        tenant_id: UUID | None = None,
        seeds_dir: Path | None = None,
    ):
        self.db = db
        self.tenant_id = tenant_id  # None = global (tenant_id IS NULL)
        self.seeds_dir = seeds_dir  # None = the repo's bundled seeds (pack path)

    # ------------------------------------------------------------------
    # scope + envelope helpers
    # ------------------------------------------------------------------

    def _tenant_cond(self, model):
        """Filter rows by source scope: global (tenant_id IS NULL) or a
        specific template tenant."""
        if self.tenant_id is None:
            return model.tenant_id.is_(None)
        return model.tenant_id == self.tenant_id

    def _envelope(self, name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """The seed-file envelope — metadata carries **no timestamp** so a
        re-export of an unchanged DB is byte-identical (the ``--check`` drift
        gate and the round-trip test both pin this)."""
        return {
            "metadata": {
                "version": SEED_VERSION,
                "source": SEED_SOURCE,
                "count": len(items),
            },
            "items": items,
        }

    @staticmethod
    def _identity_head(row) -> dict[str, Any]:
        """The identity head of an exported item: its ``id`` and, where the
        row carries one, its immutable ``seed_key`` (ADR 0055) — the two keys
        the seeders upsert by, never name or slug."""
        head: dict[str, Any] = {"id": str(row.id)}
        if getattr(row, "seed_key", None):
            head["seed_key"] = row.seed_key
        return head

    @staticmethod
    def _identity_sort_key(row) -> str:
        """Sort by ``seed_key`` (the natural identity key), falling back to
        slug then name for rows outside the curated identity."""
        return (
            getattr(row, "seed_key", None) or getattr(row, "slug", None) or getattr(row, "name", "")
        )

    # ------------------------------------------------------------------
    # concepts
    # ------------------------------------------------------------------

    async def export_concepts(self) -> dict[str, Any]:
        # Exclude disease-kind concepts — they ship in ``diseases.json`` (the
        # inverse of the seed pipeline's separate ``seed_diseases`` stage) so
        # re-exporting an unchanged DB is a git no-op.
        rows = (
            (
                await self.db.execute(
                    select(Concept).where(
                        self._tenant_cond(Concept),
                        Concept.deleted_at.is_(None),
                        ~concepts_with_kind(ConceptKind.DISEASE),
                    )
                )
            )
            .scalars()
            .unique()
            .all()
        )
        rows.sort(key=self._identity_sort_key)

        parent_ids = {r.parent_id for r in rows if r.parent_id}
        parent_slug: dict[UUID, str] = {}
        if parent_ids:
            pres = (
                (await self.db.execute(select(Concept).where(Concept.id.in_(parent_ids))))
                .scalars()
                .all()
            )
            parent_slug = {p.id: p.slug for p in pres}

        items: list[dict[str, Any]] = []
        for c in rows:
            kinds = [t.kind.value for t in (c.kind_tags or [])]
            item = self._identity_head(c)
            item["slug"] = c.slug
            item["name"] = c.name
            if kinds:
                item["kinds"] = sorted(kinds)
            pslug = parent_slug.get(c.parent_id) if c.parent_id else None
            if pslug:
                item["parent_slug"] = pslug
            if c.coding_system:
                item["coding_system"] = c.coding_system
            if c.code:
                item["code"] = c.code
            if c.aliases:
                item["aliases"] = list(c.aliases)
            if c.description:
                item["description"] = c.description
            if c.icon:
                item["icon"] = c.icon
            if c.color:
                item["color"] = c.color
            if c.display_order:
                item["display_order"] = c.display_order
            items.append(item)
        return self._envelope("concepts", items)

    async def export_diseases(self) -> dict[str, Any]:
        """Export disease-kind concepts (``diseases.json``).

        The inverse of :meth:`SeedService.seed_diseases` — disease concepts live
        in the same ``concepts`` table but ship in a separate seed file so the
        curated disease reference (ICD-10 codes) is independently maintainable.
        Same item shape as :meth:`export_concepts`.
        """
        rows = (
            (
                await self.db.execute(
                    select(Concept).where(
                        self._tenant_cond(Concept),
                        Concept.deleted_at.is_(None),
                        concepts_with_kind(ConceptKind.DISEASE),
                    )
                )
            )
            .scalars()
            .unique()
            .all()
        )
        rows.sort(key=self._identity_sort_key)

        items: list[dict[str, Any]] = []
        for c in rows:
            kinds = [t.kind.value for t in (c.kind_tags or [])]
            item = self._identity_head(c)
            item["slug"] = c.slug
            item["name"] = c.name
            if kinds:
                item["kinds"] = sorted(kinds)
            if c.coding_system:
                item["coding_system"] = c.coding_system
            if c.code:
                item["code"] = c.code
            if c.aliases:
                item["aliases"] = list(c.aliases)
            if c.description:
                item["description"] = c.description
            if c.icon:
                item["icon"] = c.icon
            if c.color:
                item["color"] = c.color
            items.append(item)
        return self._envelope("diseases", items)

    # ------------------------------------------------------------------
    # concept edges
    # ------------------------------------------------------------------

    async def export_concept_edges(self) -> dict[str, Any]:
        """Export the approved graph edges with readable endpoint refs.

        Exactly the vaccine → disease ``PREVENTS`` edges (``src_type=
        immunization, dst_type=concept``) are excluded here: they belong to
        ``vaccines.json`` (the readable ``target_diseases`` list of
        :meth:`export_vaccines`), keeping one owner per dataset — the vaccine
        seeder re-materializes them as edges on import. Any other ``PREVENTS``
        shape (e.g. an immunization endpoint pointing at a non-concept)
        exports here so no edge vanishes from the files.
        """
        rows = (
            (
                await self.db.execute(
                    select(ConceptEdge).where(
                        self._tenant_cond(ConceptEdge),
                        ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                        ~and_(
                            ConceptEdge.relation == ConceptRelationType.PREVENTS,
                            ConceptEdge.src_type == EdgeEndpointType.IMMUNIZATION,
                            ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )

        slug_maps = await self._build_endpoint_slug_maps(rows)
        items: list[dict[str, Any]] = []
        for e in rows:
            src = self._endpoint_slug(e.src_type, e.src_id, slug_maps)
            dst = self._endpoint_slug(e.dst_type, e.dst_id, slug_maps)
            if not src or not dst:
                logger.warning(
                    "Skipping edge %s/%s -> %s/%s: endpoint not in exported set",
                    e.src_type.value,
                    e.src_id,
                    e.dst_type.value,
                    e.dst_id,
                )
                continue
            item = {
                "src_slug": src,
                "src_type": e.src_type.value,
                "dst_slug": dst,
                "dst_type": e.dst_type.value,
                "relation": e.relation.value,
            }
            items.append(item)
        items.sort(key=lambda x: (x["src_slug"], x["dst_slug"], x["relation"]))
        return self._envelope("concept_edges", items)

    async def _build_endpoint_slug_maps(
        self, edges: list[ConceptEdge]
    ) -> dict[EdgeEndpointType, dict[UUID, str]]:
        """id → readable seed ref per endpoint type.

        Concept/anatomy/biomarker/vaccine refs are slugs; a medication has no
        slug, so its ref is the ``seed_key`` key part (the
        ``medication:<kebab-key>`` identity the edge seeder resolves by) with
        the name as the fallback for rows outside the curated identity.
        """
        ids_by_type: dict[EdgeEndpointType, set] = {
            EdgeEndpointType.CONCEPT: set(),
            EdgeEndpointType.ANATOMY: set(),
            EdgeEndpointType.BIOMARKER: set(),
            EdgeEndpointType.MEDICATION: set(),
            EdgeEndpointType.IMMUNIZATION: set(),
        }
        for e in edges:
            if e.src_type in ids_by_type:
                ids_by_type[e.src_type].add(e.src_id)
            if e.dst_type in ids_by_type:
                ids_by_type[e.dst_type].add(e.dst_id)

        maps: dict[EdgeEndpointType, dict[UUID, str]] = {}
        if ids_by_type[EdgeEndpointType.CONCEPT]:
            rows = (
                (
                    await self.db.execute(
                        select(Concept).where(Concept.id.in_(ids_by_type[EdgeEndpointType.CONCEPT]))
                    )
                )
                .scalars()
                .all()
            )
            maps[EdgeEndpointType.CONCEPT] = {r.id: r.slug for r in rows}
        if ids_by_type[EdgeEndpointType.ANATOMY]:
            rows = (
                (
                    await self.db.execute(
                        select(AnatomyStructure).where(
                            AnatomyStructure.id.in_(ids_by_type[EdgeEndpointType.ANATOMY])
                        )
                    )
                )
                .scalars()
                .all()
            )
            maps[EdgeEndpointType.ANATOMY] = {r.id: r.slug for r in rows}
        if ids_by_type[EdgeEndpointType.BIOMARKER]:
            rows = (
                (
                    await self.db.execute(
                        select(BiomarkerDefinition).where(
                            BiomarkerDefinition.id.in_(ids_by_type[EdgeEndpointType.BIOMARKER])
                        )
                    )
                )
                .scalars()
                .all()
            )
            maps[EdgeEndpointType.BIOMARKER] = {r.id: r.slug for r in rows}
        if ids_by_type[EdgeEndpointType.MEDICATION]:
            # MedicationCatalog has no slug — emit the seed-key key part as the
            # join key (the edge seeder resolves it by identity, never by name).
            rows = (
                (
                    await self.db.execute(
                        select(MedicationCatalog).where(
                            MedicationCatalog.id.in_(ids_by_type[EdgeEndpointType.MEDICATION])
                        )
                    )
                )
                .scalars()
                .all()
            )
            maps[EdgeEndpointType.MEDICATION] = {
                r.id: (r.seed_key.split(":", 1)[1] if r.seed_key else r.name) for r in rows
            }
        if ids_by_type[EdgeEndpointType.IMMUNIZATION]:
            # Emit the readable seed-key key part (the curated form is
            # ``vaccine:<slug>``) — the edge seeder resolves it by identity.
            rows = (
                (
                    await self.db.execute(
                        select(VaccineCatalog).where(
                            VaccineCatalog.id.in_(ids_by_type[EdgeEndpointType.IMMUNIZATION])
                        )
                    )
                )
                .scalars()
                .all()
            )
            maps[EdgeEndpointType.IMMUNIZATION] = {
                r.id: (r.seed_key.split(":", 1)[1] if r.seed_key else r.slug) for r in rows
            }
        return maps

    @staticmethod
    def _endpoint_slug(etype: EdgeEndpointType, eid: UUID, maps: dict) -> str | None:
        m = maps.get(etype)
        return m.get(eid) if m else None

    # ------------------------------------------------------------------
    # anatomy
    # ------------------------------------------------------------------

    async def export_anatomy_structures(
        self, structure_cache: dict[UUID, str] | None = None
    ) -> dict[str, Any]:
        rows = (
            (
                await self.db.execute(
                    select(AnatomyStructure).where(self._tenant_cond(AnatomyStructure))
                )
            )
            .scalars()
            .unique()
            .all()
        )
        rows.sort(key=self._identity_sort_key)

        if structure_cache is not None:
            structure_cache.update({r.id: r.slug for r in rows})

        concept_ids = {r.class_concept_id for r in rows if r.class_concept_id}
        concept_slug: dict[UUID, str] = {}
        if concept_ids:
            crows = (
                (await self.db.execute(select(Concept).where(Concept.id.in_(concept_ids))))
                .scalars()
                .all()
            )
            concept_slug = {c.id: c.slug for c in crows}

        items: list[dict[str, Any]] = []
        for s in rows:
            item = self._identity_head(s)
            item["slug"] = s.slug
            item["name"] = s.name
            if s.id == seed_key_uuid("anatomy", s.slug):
                # The identity-seeded row: emit its mechanical seed key (the
                # table has no column for it). A row outside the curated
                # identity stays id-only.
                item["seed_key"] = f"anatomy:{s.slug}"
            if s.class_concept_id:
                cslug = concept_slug.get(s.class_concept_id)
                if cslug:
                    item["class_concept_slug"] = cslug
            if s.standard_system:
                item["standard_system"] = s.standard_system.value
            if s.standard_code:
                item["standard_code"] = s.standard_code
            if s.description:
                item["description"] = s.description
            if s.display:
                item["display"] = s.display
            items.append(item)
        return self._envelope("anatomy_structures", items)

    async def export_anatomy_relations(
        self, slug_by_id: dict[UUID, str] | None = None
    ) -> dict[str, Any]:
        if slug_by_id is None:
            rows = (
                (
                    await self.db.execute(
                        select(AnatomyStructure).where(self._tenant_cond(AnatomyStructure))
                    )
                )
                .scalars()
                .unique()
                .all()
            )
            slug_by_id = {r.id: r.slug for r in rows}
        all_edges = (
            (
                await self.db.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.src_type == EdgeEndpointType.ANATOMY,
                        ConceptEdge.dst_type == EdgeEndpointType.ANATOMY,
                        ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                    )
                )
            )
            .scalars()
            .all()
        )
        items: list[dict[str, Any]] = []
        for e in all_edges:
            src = slug_by_id.get(e.src_id)
            dst = slug_by_id.get(e.dst_id)
            if not src or not dst:
                continue
            items.append(
                {
                    "source_slug": src,
                    "target_slug": dst,
                    "relation_type": e.relation.value,
                }
            )
        items.sort(key=lambda x: (x["source_slug"], x["target_slug"]))
        return self._envelope("anatomy_relations", items)

    # ------------------------------------------------------------------
    # default catalog (units + biomarkers)
    # ------------------------------------------------------------------

    async def export_default_catalog(self) -> dict[str, Any]:
        units = (await self.db.execute(select(Unit).order_by(Unit.symbol))).scalars().all()
        bios = (
            (
                await self.db.execute(
                    select(BiomarkerDefinition).where(self._tenant_cond(BiomarkerDefinition))
                )
            )
            .scalars()
            .all()
        )
        bios.sort(key=self._identity_sort_key)

        concept_ids = {b.class_concept_id for b in bios if b.class_concept_id}
        unit_ids = {b.preferred_unit_id for b in bios if b.preferred_unit_id}
        concept_slug: dict[UUID, str] = {}
        if concept_ids:
            crows = (
                (await self.db.execute(select(Concept).where(Concept.id.in_(concept_ids))))
                .scalars()
                .all()
            )
            concept_slug = {c.id: c.slug for c in crows}
        unit_symbol: dict[UUID, str] = {}
        if unit_ids:
            urows = (
                (await self.db.execute(select(Unit).where(Unit.id.in_(unit_ids)))).scalars().all()
            )
            unit_symbol = {u.id: u.symbol for u in urows}

        units_out = [
            {
                "id": str(u.id),
                "symbol": u.symbol,
                "name": u.name,
                "quantity_type": u.quantity_type.value if u.quantity_type else "OTHER",
            }
            for u in units
        ]
        biomarkers_out: list[dict[str, Any]] = []
        for b in bios:
            item = self._identity_head(b)
            item["slug"] = b.slug
            item["name"] = b.name
            item["coding_system"] = b.coding_system.value if b.coding_system else "loinc"
            if b.code:
                item["code"] = b.code
            if b.class_concept_id:
                cslug = concept_slug.get(b.class_concept_id)
                if cslug:
                    item["class_concept_slug"] = cslug
            if b.preferred_unit_id:
                sym = unit_symbol.get(b.preferred_unit_id)
                if sym:
                    item["preferred_unit_symbol"] = sym
            if b.aliases:
                item["aliases"] = list(b.aliases)
            if b.info:
                item["info"] = b.info
            if b.reference_range_min is not None:
                item["reference_range_min"] = b.reference_range_min
            if b.reference_range_max is not None:
                item["reference_range_max"] = b.reference_range_max
            if b.is_telemetry:
                item["is_telemetry"] = True
            # State biomarker discriminator (round-trip on seed export/import).
            if b.value_type and b.value_type.value != "quantity":
                item["value_type"] = b.value_type.value
            if b.supports_multi_state:
                item["supports_multi_state"] = True
            if b.allowed_states:
                item["allowed_states"] = [
                    {
                        "state_slug": s.state.slug,
                        "is_normal": s.is_normal,
                        "sort_order": s.sort_order,
                    }
                    for s in b.allowed_states
                    if s.state is not None
                ]
            biomarkers_out.append(item)

        return {
            "metadata": {
                "version": SEED_VERSION,
                "source": SEED_SOURCE,
                "units_count": len(units_out),
                "biomarkers_count": len(biomarkers_out),
            },
            "units": units_out,
            "biomarkers": biomarkers_out,
        }

    # ------------------------------------------------------------------
    # biomarker panels (MEMBER_OF edges)
    # ------------------------------------------------------------------

    async def export_biomarker_panels(self) -> dict[str, Any]:
        edges = (
            (
                await self.db.execute(
                    select(ConceptEdge).where(
                        self._tenant_cond(ConceptEdge),
                        ConceptEdge.relation == ConceptRelationType.MEMBER_OF,
                        ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                        ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                        ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not edges:
            return self._envelope("biomarker_panels", [])
        slug_maps = await self._build_endpoint_slug_maps(edges)
        items: list[dict[str, Any]] = []
        for e in edges:
            bio = self._endpoint_slug(e.src_type, e.src_id, slug_maps)
            panel = self._endpoint_slug(e.dst_type, e.dst_id, slug_maps)
            if not bio or not panel:
                continue
            items.append(
                {
                    "seed_key": f"panel-membership:{panel}/{bio}",
                    "panel_slug": panel,
                    "biomarker_slug": bio,
                }
            )
        items.sort(key=lambda x: (x["panel_slug"], x["biomarker_slug"]))
        return self._envelope("biomarker_panels", items)

    # ------------------------------------------------------------------
    # clinical event types
    # ------------------------------------------------------------------

    async def export_clinical_event_types(self) -> dict[str, Any]:
        rows = (
            (
                await self.db.execute(
                    select(ClinicalEventType).where(
                        or_(
                            ClinicalEventType.tenant_id.is_(None),
                            ClinicalEventType.tenant_id == self.tenant_id,
                        )
                        if self.tenant_id is not None
                        else ClinicalEventType.tenant_id.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        concept_ids = {r.category_concept_id for r in rows if r.category_concept_id}
        concept_slug: dict[UUID, str] = {}
        if concept_ids:
            crows = (
                (await self.db.execute(select(Concept).where(Concept.id.in_(concept_ids))))
                .scalars()
                .all()
            )
            concept_slug = {c.id: c.slug for c in crows}
        items: list[dict[str, Any]] = []
        for t in rows:
            item = self._identity_head(t)
            item["slug"] = t.slug
            item["name"] = t.name
            if t.id == seed_key_uuid("clinical-event-type", t.slug):
                # The identity-seeded row: emit its mechanical seed key (the
                # table has no column for it). A row outside the curated
                # identity stays id-only.
                item["seed_key"] = f"clinical-event-type:{t.slug}"
            if t.category_concept_id:
                cslug = concept_slug.get(t.category_concept_id)
                if cslug:
                    item["category_slug"] = cslug
            if t.description:
                item["description"] = t.description
            if t.icon:
                item["icon"] = t.icon
            if t.color:
                item["color"] = t.color
            if t.metadata_schema:
                item["metadata_schema"] = t.metadata_schema
            items.append(item)
        items.sort(key=lambda x: x.get("seed_key") or x["slug"])
        return self._envelope("clinical_event_types", items)

    # ------------------------------------------------------------------
    # biomarker states (universal controlled vocabulary)
    # ------------------------------------------------------------------

    async def export_biomarker_states(self) -> dict[str, Any]:
        """Export the categorical biomarker-state vocabulary
        (``biomarker_states.json``) — the inverse of
        :meth:`CatalogImportService.seed_biomarker_states`.

        The table is universal (no ``tenant_id``). The identity-seeded rows
        (id == ``uuid5("biomarker-state:<slug>")``) carry their mechanical
        ``seed_key``; rows created outside the curated identity stay
        slug-keyed with an explicit ``id`` head (the seeder's ``explicit_id``
        path re-ingests them into the same rows).
        """
        rows = (await self.db.execute(select(BiomarkerState))).scalars().all()
        rows.sort(key=lambda s: (s.slug or "", str(s.id)))
        items: list[dict[str, Any]] = []
        for s in rows:
            item: dict[str, Any] = {}
            if s.id == seed_key_uuid("biomarker-state", s.slug):
                item["seed_key"] = f"biomarker-state:{s.slug}"
            else:
                item["id"] = str(s.id)
            item["slug"] = s.slug
            item["code"] = s.code
            item["system"] = s.system
            item["display"] = s.display
            if s.description:
                item["description"] = s.description
            if s.category:
                item["category"] = s.category
            item["sort_order"] = s.sort_order
            items.append(item)
        return self._envelope("biomarker_states", items)

    # ------------------------------------------------------------------
    # OMB race/ethnicity/language picklist (pack-path pass-through)
    # ------------------------------------------------------------------

    async def export_omb_picklist(self) -> dict[str, Any]:
        """Export the OMB picklist (``omb_race_ethnicity.json``).

        The picklist has no table — it flows through the pack path
        (``bundled_stage_data("omb_picklist")``) and is read from there at
        request time. The export is therefore a **normalization pass-through**
        of the pack-path data: the three collections sorted by ``seed_key``,
        the descriptive metadata kept (version/source/schema note) and the
        timestamp dropped, so the exported pack covers every registered item
        file and re-ingests byte-stably.
        """
        from app.core.seed_pack import omb_picklist as bundled_omb_picklist
        from app.core.seed_pack.bundled import bundled_seeds_dir as _bundled_seeds_dir

        data = bundled_omb_picklist(self.seeds_dir)
        out: dict[str, Any] = {"metadata": {"version": SEED_VERSION, "source": SEED_SOURCE}}
        try:
            raw = json.loads(
                ((self.seeds_dir or _bundled_seeds_dir()) / "omb_race_ethnicity.json").read_text(
                    encoding="utf-8"
                )
            )
            note = (raw.get("metadata") or {}).get("schema_note")
            if note:
                out["metadata"]["schema_note"] = note
        except (OSError, json.JSONDecodeError):
            pass
        for key in ("races", "ethnicities", "languages"):
            rows = [
                {"seed_key": r["seed_key"], "code": r["code"], "display": r["display"]}
                for r in sorted(data.get(key, []), key=lambda r: r.get("seed_key", ""))
                if isinstance(r, dict) and r.get("seed_key")
            ]
            out[key] = rows
        return out

    # ------------------------------------------------------------------
    # medication + allergy catalogs
    # ------------------------------------------------------------------

    async def export_medications(self) -> dict[str, Any]:
        rows = (
            (
                await self.db.execute(
                    select(MedicationCatalog).where(self._tenant_cond(MedicationCatalog))
                )
            )
            .scalars()
            .all()
        )
        rows.sort(key=self._identity_sort_key)
        items: list[dict[str, Any]] = []
        for m in rows:
            item = self._identity_head(m)
            item["name"] = m.name
            if m.coding_system or m.code:
                item["coding_system"] = m.coding_system
                item["code"] = m.code
            if m.description:
                item["description"] = m.description
            if m.indications:
                item["indications"] = m.indications
            if m.side_effects:
                item["side_effects"] = list(m.side_effects)
            if m.contraindications:
                item["contraindications"] = m.contraindications
            if m.dosage_info:
                item["dosage_info"] = m.dosage_info
            items.append(item)
        return self._envelope("medications", items)

    async def export_allergies(self) -> dict[str, Any]:
        rows = (
            (await self.db.execute(select(AllergyCatalog).where(self._tenant_cond(AllergyCatalog))))
            .scalars()
            .all()
        )
        rows.sort(key=self._identity_sort_key)
        items: list[dict[str, Any]] = []
        for a in rows:
            item = self._identity_head(a)
            item["name"] = a.name
            if a.coding_system or a.code:
                item["coding_system"] = a.coding_system
                item["code"] = a.code
            if a.category:
                item["category"] = a.category.value
            if a.description:
                item["description"] = a.description
            if a.typical_reactions:
                item["typical_reactions"] = list(a.typical_reactions)
            items.append(item)
        return self._envelope("allergies", items)

    # ------------------------------------------------------------------
    # vaccines (rows + the readable target_diseases derived from edges)
    # ------------------------------------------------------------------

    async def export_vaccines(self) -> dict[str, Any]:
        """Export the CVX-coded vaccine catalog (``vaccines.json``).

        Vaccine → disease links are graph data: the item carries the readable
        ``target_diseases`` slug list **derived from the approved PREVENTS
        edges** (the inverse of the vaccine seeder, which re-materializes the
        list as edges on import). One owner per dataset: ``concept_edges.json``
        excludes exactly these edges.
        """
        rows = (
            (await self.db.execute(select(VaccineCatalog).where(self._tenant_cond(VaccineCatalog))))
            .scalars()
            .all()
        )
        rows.sort(key=self._identity_sort_key)

        prevented: dict[UUID, list[str]] = {}
        edge_rows = (
            await self.db.execute(
                select(ConceptEdge.src_id, Concept.slug)
                .join(Concept, Concept.id == ConceptEdge.dst_id)
                .where(
                    self._tenant_cond(ConceptEdge),
                    ConceptEdge.src_type == EdgeEndpointType.IMMUNIZATION,
                    ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                    ConceptEdge.relation == ConceptRelationType.PREVENTS,
                    ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                    Concept.deleted_at.is_(None),
                )
                .order_by(Concept.slug)
            )
        ).all()
        for src_id, slug in edge_rows:
            prevented.setdefault(src_id, []).append(slug)

        items: list[dict[str, Any]] = []
        for v in rows:
            item = self._identity_head(v)
            item["slug"] = v.slug
            item["name"] = v.name
            if v.coding_system or v.code:
                item["coding_system"] = v.coding_system
                item["code"] = v.code
            if prevented.get(v.id):
                item["target_diseases"] = prevented[v.id]
            if v.description:
                item["description"] = v.description
            if v.dose_schedule:
                item["dose_schedule"] = v.dose_schedule
            if v.contraindications:
                item["contraindications"] = v.contraindications
            if v.side_effects:
                item["side_effects"] = list(v.side_effects)
            items.append(item)
        return self._envelope("vaccines", items)

    # ------------------------------------------------------------------
    # orchestrator: export every seed file
    # ------------------------------------------------------------------

    EXPORTERS: ClassVar[dict[str, str]] = {
        "concepts.json": "export_concepts",
        "diseases.json": "export_diseases",
        "vaccines.json": "export_vaccines",
        "concept_edges.json": "export_concept_edges",
        "anatomy_structures.json": "export_anatomy_structures",
        "biomarker_states.json": "export_biomarker_states",
        "default_catalog.json": "export_default_catalog",
        "biomarker_panels.json": "export_biomarker_panels",
        "omb_race_ethnicity.json": "export_omb_picklist",
        "clinical_event_types.json": "export_clinical_event_types",
        "medications.json": "export_medications",
        "allergies.json": "export_allergies",
    }

    async def export_all(self) -> dict[str, dict[str, Any]]:
        """Run every exporter, returning ``{filename: payload_dict}``.

        Does not touch the filesystem — the CLI / endpoint handles the safety
        pipeline (staging dir → backup → atomic write). Keeping I/O out of
        this method makes it unit-testable with a real DB and no disk.
        """
        out: dict[str, dict[str, Any]] = {}
        for filename, method_name in self.EXPORTERS.items():
            payload = await getattr(self, method_name)()
            if payload is not None:
                out[filename] = payload
        return out

    # ------------------------------------------------------------------
    # the canonical pack (ADR 0056) — one format for export/CI/round-trip
    # ------------------------------------------------------------------

    async def build_pack(self) -> tuple[dict, dict[str, bytes]]:
        """Wrap the export as one canonical seed pack: ``manifest.json`` plus
        ``items/*.json`` under the pack contract (sorted, hashed, validated,
        no timestamps anywhere).

        Returns ``(manifest, files)`` where ``files`` maps ``items/<file>``
        paths to exact bytes. The pack is always **system-scoped**: the
        canonical export is the global canonical taxonomy, and a
        template-tenant export emits its rows with scope stripped (curator
        input for the system lane) — a tenant-scoped pack could not carry
        the global-only item files the export covers. Raises
        :class:`SeedPackInvalid` when the produced pack fails the contract —
        an export bug is a loud error, never a broken pack.
        """
        payloads = await self.export_all()
        files: dict[str, bytes] = {}
        item_files: list[dict[str, str]] = []
        for filename in sorted(payloads):
            data = self._serialize(payloads[filename])
            files[f"items/{filename}"] = data
            item_files.append({"path": f"items/{filename}", "sha256": content_digest(data)})
        manifest = {
            "id": EXPORT_PACK_ID,
            "schema_version": PACK_SCHEMA_VERSION,
            "version": SEED_VERSION,
            "license": EXPORT_PACK_LICENSE,
            "scope": "system",
            "priority": 0,
            "description": EXPORT_PACK_DESCRIPTION,
            "item_files": item_files,
        }
        errors = validate_pack(manifest, files)
        if errors:
            raise SeedPackInvalid(errors)
        return manifest, files

    async def build_zip_bytes(self) -> bytes:
        """Build the canonical pack ZIP (``manifest.json`` + ``items/*.json``)
        for download — fully deterministic: fixed entry order, fixed archive
        timestamps, stored file order sorted by path. The receiver unpacks it
        into ``backend/data/seeds/`` via ``scripts/unpack_seeds_zip.py``
        (which verifies every sha256 pin against the manifest). No filesystem
        access here; fully unit-testable."""
        manifest, files = await self.build_pack()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            entries = [(MANIFEST_NAME, self._serialize(manifest))]
            entries += sorted(files.items())
            for name, data in entries:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                zf.writestr(info, data)
        return buf.getvalue()

    # ------------------------------------------------------------------
    # filesystem writer (safety pipeline) — used by the CLI / endpoint
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(payload: dict[str, Any]) -> bytes:
        return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"

    @staticmethod
    def _payload_count(payload: dict[str, Any]) -> int:
        """The item count of an exported file: the sum of its list collections."""
        total = 0
        for value in payload.values():
            if isinstance(value, list):
                total += len(value)
        return total

    async def write_all(self, out_dir: Path, backup: bool = True) -> dict[str, Any]:
        """Write every seed file into ``out_dir`` with the safety pipeline.

        - writes to ``<out_dir>/.export-staging/`` first
        - backs up existing files to ``<out_dir>/.backup-<timestamp>/``
        - atomically renames staging → ``out_dir``

        Returns a report ``{files: {name: {count, bytes}}, backup_dir: str}``.
        """
        out_dir = Path(out_dir)
        staging = out_dir / ".export-staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)

        payloads = await self.export_all()
        report: dict[str, Any] = {"files": {}, "backup_dir": None}

        for filename, payload in payloads.items():
            data = self._serialize(payload)
            (staging / filename).write_bytes(data)
            report["files"][filename] = {
                "count": self._payload_count(payload),
                "bytes": len(data),
            }

        if backup and out_dir.exists():
            backup_dir = out_dir / f".backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
            backup_dir.mkdir(parents=True, exist_ok=True)
            for filename in self.EXPORTERS:
                src = out_dir / filename
                if src.exists():
                    shutil.copy2(src, backup_dir / filename)
            report["backup_dir"] = str(backup_dir)

        for filename in self.EXPORTERS:
            src = staging / filename
            dst = out_dir / filename
            if src.exists():
                dst.write_bytes(src.read_bytes())
        shutil.rmtree(staging)
        return report
