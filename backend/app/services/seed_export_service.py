"""Seed export service — the inverse of :class:`SeedService`.

Serializes the running instance's taxonomy / anatomy / biomarker / catalog data
back into the slug-keyed ``data/seeds/*.json`` format so a curator can build up
the canonical set via the UI + AI, then snapshot it into the shipped seeds.

Identity model: **slug is the join key** (stable identity), ``name`` is mutable
display. ``coding_system`` + ``code`` are emitted as identity *evidence* in the
item body where present (FHIR-aligned) but are NOT the join key — see
``dev/plans/seed-export-service-2026-07-07.md`` for the design discussion.

Source scope: pass ``tenant_id=None`` (default) to export the global taxonomy
(``tenant_id IS NULL`` rows — what ships), or a specific ``tenant_id`` to treat
that tenant as a template (emit its rows with scope stripped).

Determinism: every file is sorted by its natural key (slug or name), uses a
fixed field order, and a regenerated metadata block. Re-exporting an unchanged
DB is a git no-op.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anatomy_model import AnatomyRelation, AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition, Unit
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
from app.services.concept_service import concepts_with_kind

logger = logging.getLogger(__name__)

SEED_VERSION = "1.0.0"
SEED_SOURCE = "exported-from-instance"


class SeedExportService:
    def __init__(self, db: AsyncSession, tenant_id: Optional[UUID] = None):
        self.db = db
        self.tenant_id = tenant_id  # None = global (tenant_id IS NULL)

    # ------------------------------------------------------------------
    # scope + envelope helpers
    # ------------------------------------------------------------------

    def _tenant_cond(self, model):
        """Filter rows by source scope: global (tenant_id IS NULL) or a
        specific template tenant."""
        if self.tenant_id is None:
            return model.tenant_id.is_(None)
        return model.tenant_id == self.tenant_id

    def _envelope(self, name: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "metadata": {
                "version": SEED_VERSION,
                "source": SEED_SOURCE,
                "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "count": len(items),
            },
            "items": items,
        }

    # ------------------------------------------------------------------
    # concepts
    # ------------------------------------------------------------------

    async def export_concepts(self) -> Dict[str, Any]:
        # Exclude disease-kind concepts — they ship in ``diseases.json`` (the
        # inverse of the seed pipeline's separate ``seed_diseases`` stage) so
        # re-exporting an unchanged DB is a git no-op.
        rows = (
            (
                await self.db.execute(
                    select(Concept)
                    .where(
                        self._tenant_cond(Concept),
                        Concept.deleted_at.is_(None),
                        ~concepts_with_kind(ConceptKind.DISEASE),
                    )
                    .order_by(Concept.slug)
                )
            )
            .scalars()
            .unique()
            .all()
        )

        parent_ids = {r.parent_id for r in rows if r.parent_id}
        parent_slug: Dict[UUID, str] = {}
        if parent_ids:
            pres = (
                (
                    await self.db.execute(
                        select(Concept).where(Concept.id.in_(parent_ids))
                    )
                )
                .scalars()
                .all()
            )
            parent_slug = {p.id: p.slug for p in pres}

        items: List[Dict[str, Any]] = []
        for c in rows:
            kinds = [t.kind.value for t in (c.kind_tags or [])]
            item: Dict[str, Any] = {"slug": c.slug, "name": c.name}
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

    async def export_diseases(self) -> Dict[str, Any]:
        """Export disease-kind concepts (``diseases.json``).

        The inverse of :meth:`SeedService.seed_diseases` — disease concepts live
        in the same ``concepts`` table but ship in a separate seed file so the
        curated disease reference (ICD-10 codes) is independently maintainable.
        Same item shape as :meth:`export_concepts`.
        """
        rows = (
            (
                await self.db.execute(
                    select(Concept)
                    .where(
                        self._tenant_cond(Concept),
                        Concept.deleted_at.is_(None),
                        concepts_with_kind(ConceptKind.DISEASE),
                    )
                    .order_by(Concept.slug)
                )
            )
            .scalars()
            .unique()
            .all()
        )

        items: List[Dict[str, Any]] = []
        for c in rows:
            kinds = [t.kind.value for t in (c.kind_tags or [])]
            item: Dict[str, Any] = {"slug": c.slug, "name": c.name}
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

    async def export_concept_edges(self) -> Dict[str, Any]:
        rows = (
            (
                await self.db.execute(
                    select(ConceptEdge).where(
                        self._tenant_cond(ConceptEdge),
                        ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                    )
                )
            )
            .scalars()
            .all()
        )

        slug_maps = await self._build_endpoint_slug_maps(rows)
        items: List[Dict[str, Any]] = []
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
        self, edges: List[ConceptEdge]
    ) -> Dict[EdgeEndpointType, Dict[UUID, str]]:
        ids_by_type: Dict[EdgeEndpointType, set] = {
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

        maps: Dict[EdgeEndpointType, Dict[UUID, str]] = {}
        if ids_by_type[EdgeEndpointType.CONCEPT]:
            rows = (
                (
                    await self.db.execute(
                        select(Concept).where(
                            Concept.id.in_(ids_by_type[EdgeEndpointType.CONCEPT])
                        )
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
                            AnatomyStructure.id.in_(
                                ids_by_type[EdgeEndpointType.ANATOMY]
                            )
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
                            BiomarkerDefinition.id.in_(
                                ids_by_type[EdgeEndpointType.BIOMARKER]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            maps[EdgeEndpointType.BIOMARKER] = {r.id: r.slug for r in rows}
        if ids_by_type[EdgeEndpointType.MEDICATION]:
            # MedicationCatalog has no slug — the seed loader resolves edges by
            # case-insensitive ``name``, so emit ``name`` as the join key.
            rows = (
                (
                    await self.db.execute(
                        select(MedicationCatalog).where(
                            MedicationCatalog.id.in_(
                                ids_by_type[EdgeEndpointType.MEDICATION]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            maps[EdgeEndpointType.MEDICATION] = {r.id: r.name for r in rows}
        if ids_by_type[EdgeEndpointType.IMMUNIZATION]:
            # VaccineCatalog has a slug — emit it as the join key.
            rows = (
                (
                    await self.db.execute(
                        select(VaccineCatalog).where(
                            VaccineCatalog.id.in_(
                                ids_by_type[EdgeEndpointType.IMMUNIZATION]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            maps[EdgeEndpointType.IMMUNIZATION] = {r.id: r.slug for r in rows}
        return maps

    @staticmethod
    def _endpoint_slug(etype: EdgeEndpointType, eid: UUID, maps: Dict) -> Optional[str]:
        m = maps.get(etype)
        return m.get(eid) if m else None

    # ------------------------------------------------------------------
    # anatomy
    # ------------------------------------------------------------------

    async def export_anatomy_structures(
        self, structure_cache: Optional[Dict[UUID, str]] = None
    ) -> Dict[str, Any]:
        rows = (
            (
                await self.db.execute(
                    select(AnatomyStructure)
                    .where(self._tenant_cond(AnatomyStructure))
                    .order_by(AnatomyStructure.slug)
                )
            )
            .scalars()
            .unique()
            .all()
        )

        if structure_cache is not None:
            structure_cache.update({r.id: r.slug for r in rows})

        concept_ids = {r.class_concept_id for r in rows if r.class_concept_id}
        concept_slug: Dict[UUID, str] = {}
        if concept_ids:
            crows = (
                (
                    await self.db.execute(
                        select(Concept).where(Concept.id.in_(concept_ids))
                    )
                )
                .scalars()
                .all()
            )
            concept_slug = {c.id: c.slug for c in crows}

        items: List[Dict[str, Any]] = []
        for s in rows:
            item: Dict[str, Any] = {"slug": s.slug, "name": s.name}
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
        self, slug_by_id: Optional[Dict[UUID, str]] = None
    ) -> Dict[str, Any]:
        if slug_by_id is None:
            rows = (
                (
                    await self.db.execute(
                        select(AnatomyStructure).where(
                            self._tenant_cond(AnatomyStructure)
                        )
                    )
                )
                .scalars()
                .unique()
                .all()
            )
            slug_by_id = {r.id: r.slug for r in rows}
        all_relations = (await self.db.execute(select(AnatomyRelation))).scalars().all()
        items: List[Dict[str, Any]] = []
        for r in all_relations:
            src = slug_by_id.get(r.source_id)
            dst = slug_by_id.get(r.target_id)
            if not src or not dst:
                continue
            items.append(
                {
                    "source_slug": src,
                    "target_slug": dst,
                    "relation_type": r.relation_type.value,
                }
            )
        items.sort(key=lambda x: (x["source_slug"], x["target_slug"]))
        return self._envelope("anatomy_relations", items)

    # ------------------------------------------------------------------
    # default catalog (units + biomarkers)
    # ------------------------------------------------------------------

    async def export_default_catalog(self) -> Dict[str, Any]:
        units = (
            (await self.db.execute(select(Unit).order_by(Unit.symbol))).scalars().all()
        )
        bios = (
            (
                await self.db.execute(
                    select(BiomarkerDefinition)
                    .where(self._tenant_cond(BiomarkerDefinition))
                    .order_by(BiomarkerDefinition.slug)
                )
            )
            .scalars()
            .all()
        )

        concept_ids = {b.class_concept_id for b in bios if b.class_concept_id}
        unit_ids = {b.preferred_unit_id for b in bios if b.preferred_unit_id}
        concept_slug: Dict[UUID, str] = {}
        if concept_ids:
            crows = (
                (
                    await self.db.execute(
                        select(Concept).where(Concept.id.in_(concept_ids))
                    )
                )
                .scalars()
                .all()
            )
            concept_slug = {c.id: c.slug for c in crows}
        unit_symbol: Dict[UUID, str] = {}
        if unit_ids:
            urows = (
                (await self.db.execute(select(Unit).where(Unit.id.in_(unit_ids))))
                .scalars()
                .all()
            )
            unit_symbol = {u.id: u.symbol for u in urows}

        units_out = [
            {
                "symbol": u.symbol,
                "name": u.name,
                "quantity_type": u.quantity_type.value if u.quantity_type else "OTHER",
            }
            for u in units
        ]
        biomarkers_out: List[Dict[str, Any]] = []
        for b in bios:
            item: Dict[str, Any] = {
                "slug": b.slug,
                "name": b.name,
                "coding_system": b.coding_system.value if b.coding_system else "loinc",
            }
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
            biomarkers_out.append(item)

        return {
            "metadata": {
                "version": SEED_VERSION,
                "source": SEED_SOURCE,
                "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "units_count": len(units_out),
                "biomarkers_count": len(biomarkers_out),
            },
            "units": units_out,
            "biomarkers": biomarkers_out,
        }

    # ------------------------------------------------------------------
    # biomarker panels (MEMBER_OF edges)
    # ------------------------------------------------------------------

    async def export_biomarker_panels(self) -> Dict[str, Any]:
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
        items: List[Dict[str, Any]] = []
        for e in edges:
            bio = self._endpoint_slug(e.src_type, e.src_id, slug_maps)
            panel = self._endpoint_slug(e.dst_type, e.dst_id, slug_maps)
            if not bio or not panel:
                continue
            items.append({"panel_slug": panel, "biomarker_slug": bio})
        items.sort(key=lambda x: (x["panel_slug"], x["biomarker_slug"]))
        return self._envelope("biomarker_panels", items)

    # ------------------------------------------------------------------
    # clinical event types
    # ------------------------------------------------------------------

    async def export_clinical_event_types(self) -> Dict[str, Any]:
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
        concept_slug: Dict[UUID, str] = {}
        if concept_ids:
            crows = (
                (
                    await self.db.execute(
                        select(Concept).where(Concept.id.in_(concept_ids))
                    )
                )
                .scalars()
                .all()
            )
            concept_slug = {c.id: c.slug for c in crows}
        items: List[Dict[str, Any]] = []
        for t in rows:
            item: Dict[str, Any] = {"slug": t.slug, "name": t.name}
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
        items.sort(key=lambda x: x["slug"])
        return self._envelope("clinical_event_types", items)

    # ------------------------------------------------------------------
    # medication + allergy catalogs
    # ------------------------------------------------------------------

    async def export_medications(self) -> Dict[str, Any]:
        rows = (
            (
                await self.db.execute(
                    select(MedicationCatalog)
                    .where(self._tenant_cond(MedicationCatalog))
                    .order_by(MedicationCatalog.name)
                )
            )
            .scalars()
            .all()
        )
        items: List[Dict[str, Any]] = []
        for m in rows:
            item: Dict[str, Any] = {"name": m.name}
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

    async def export_allergies(self) -> Dict[str, Any]:
        rows = (
            (
                await self.db.execute(
                    select(AllergyCatalog)
                    .where(self._tenant_cond(AllergyCatalog))
                    .order_by(AllergyCatalog.name)
                )
            )
            .scalars()
            .all()
        )
        items: List[Dict[str, Any]] = []
        for a in rows:
            item: Dict[str, Any] = {"name": a.name}
            if a.category:
                item["category"] = a.category.value
            if a.description:
                item["description"] = a.description
            if a.typical_reactions:
                item["typical_reactions"] = list(a.typical_reactions)
            items.append(item)
        return self._envelope("allergies", items)

    # ------------------------------------------------------------------
    # orchestrator: export every file (the safety pipeline is Phase 3)
    # ------------------------------------------------------------------

    EXPORTERS: Dict[str, str] = {
        "concepts.json": "export_concepts",
        "diseases.json": "export_diseases",
        "concept_edges.json": "export_concept_edges",
        "anatomy_structures.json": "export_anatomy_structures",
        "anatomy_relations.json": "export_anatomy_relations",
        "default_catalog.json": "export_default_catalog",
        "biomarker_panels.json": "export_biomarker_panels",
        "clinical_event_types.json": "export_clinical_event_types",
        "medications.json": "export_medications",
        "allergies.json": "export_allergies",
    }

    async def export_all(self) -> Dict[str, Dict[str, Any]]:
        """Run every exporter, returning ``{filename: payload_dict}``.

        Does not touch the filesystem — the CLI / endpoint (Phase 3) handles
        the safety pipeline (staging dir → backup → atomic write). Keeping I/O
        out of this method makes it unit-testable with a real DB and no disk.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for filename, method_name in self.EXPORTERS.items():
            out[filename] = await getattr(self, method_name)()
        return out

    async def build_zip_bytes(self) -> bytes:
        """Build a ZIP of every seed file (flat layout: ``concepts.json`` at the
        archive root) suitable for download. The receiver unpacks it into
        ``backend/data/seeds/`` — via ``scripts/unpack_seeds_zip.py`` (which
        backs up existing files first) or manually. No filesystem access here;
        fully unit-testable."""
        payloads = await self.export_all()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for filename, payload in payloads.items():
                zf.writestr(filename, self._serialize(payload))
        return buf.getvalue()

    # ------------------------------------------------------------------
    # filesystem writer (safety pipeline) — used by the CLI / endpoint
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(payload: Dict[str, Any]) -> bytes:
        return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"

    async def write_all(self, out_dir: Path, backup: bool = True) -> Dict[str, Any]:
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
        report: Dict[str, Any] = {"files": {}, "backup_dir": None}

        for filename, payload in payloads.items():
            data = self._serialize(payload)
            (staging / filename).write_bytes(data)
            items = payload.get("items", payload.get("biomarkers", []))
            report["files"][filename] = {
                "count": len(items) if isinstance(items, list) else 0,
                "bytes": len(data),
            }

        if backup and out_dir.exists():
            backup_dir = (
                out_dir
                / f".backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            )
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
