"""Default catalog, biomarker panel and state seeding (identity upsert — ADR 0055).

``default_catalog.json`` units and biomarkers carry immutable qualified
``seed_key``s (``unit:<kebab-symbol>`` / ``biomarker:<slug>``); the row ids
are the deterministic ``uuid5`` values derived from them. Panel memberships
are ``concept_edges`` rows with a deterministic id derived from
``panel-membership:<panel>/<biomarker>``; states upsert by identity.

Transaction ownership (ADR 0056): the seeders take a required session and
parsed pack content (items or the normalized collections mapping), flush,
and never commit — the caller owns the transaction.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalog_identity import seed_key_uuid
from app.core.seed_pack.bundled import bundled_stage_data, bundled_stage_items
from app.services.catalog.catalog_import_service import CatalogImportService, CatalogSeedIdentities
from app.services.catalog.concept_service import concepts_with_kind
from app.services.seed.identity import SeedIdentityHint, require_seed_key, upsert_by_identity

logger = logging.getLogger(__name__)

_IDENTITY_KEYS = ("seed_key", "id")


def _split_identity(
    raw: dict, catalog_type: str, *, required: bool = True
) -> tuple[dict, SeedIdentityHint | None]:
    """Strip the identity fields off a raw seed item for the typed payload,
    returning the cleaned item + its identity hint.

    ``required=False`` (units) tolerates a missing ``seed_key`` — the unit's
    symbol is its immutable natural key and the importer falls back to the
    symbol upsert when no hint is carried (an export of a running instance
    cannot recover a unit's curated seed key: the table has no column for
    it, the identity lives in the id). In that case the item's explicit
    ``id`` stays in the payload so the importer can reuse it at creation
    (a fresh install reproduces the exported identity).
    """
    qualified = raw.get("seed_key")
    if qualified is None and not required:
        clean = {k: v for k, v in raw.items() if k != "seed_key"}
        return clean, None
    hint = SeedIdentityHint(
        qualified=require_seed_key(raw, catalog_type),
        explicit_id=raw.get("id"),
    )
    clean = {k: v for k, v in raw.items() if k not in _IDENTITY_KEYS}
    return clean, hint


def _catalog_payload(data: Mapping[str, Sequence[dict[str, Any]]] | dict[str, Any]):
    """Build the typed import payload + identity hints from parsed content.

    ``data`` is the pack's normalized ``default_catalog`` collections mapping
    (``units``/``biomarkers`` lists).
    """
    from app.schemas.biomarker import CatalogImportPayload

    units_raw = list(data.get("units", []))
    bios_raw = list(data.get("biomarkers", []))
    unit_hints: dict[str, SeedIdentityHint] = {}
    bio_hints: dict[str, SeedIdentityHint] = {}
    clean_units = []
    clean_bios = []
    for raw in units_raw:
        clean, hint = _split_identity(raw, "unit", required=False)
        if hint is not None:
            unit_hints[clean["symbol"]] = hint
        clean_units.append(clean)
    for raw in bios_raw:
        clean, hint = _split_identity(raw, "biomarker")
        bio_hints[clean["slug"]] = hint
        clean_bios.append(clean)
    payload = CatalogImportPayload.model_validate(
        {**data, "units": clean_units, "biomarkers": clean_bios}
    )
    return payload, CatalogSeedIdentities(units=unit_hints, biomarkers=bio_hints)


class CatalogSeedMixin:
    async def seed_default_catalog(
        self,
        session: AsyncSession,
        data: Mapping[str, Sequence[dict[str, Any]]] | None = None,
    ) -> dict[str, int]:
        """Seed the default biomarker catalog (units + biomarker definitions).

        ``data`` defaults to the bundled pack's parsed ``default_catalog``
        collections and is upserted through :class:`CatalogImportService` in
        identity mode (deterministic id → ``seed_key``; never by
        symbol/slug). Must run AFTER :meth:`seed_concepts` so that
        ``biomarker_class`` concepts exist for ``class_concept_id``
        resolution. The caller owns the session and the transaction (the
        importer flushes; nothing commits here).

        Returns the standard stats shape ``{added, updated, unchanged,
        skipped, errors}`` with a ``details`` sub-dict preserving the
        units/biomarkers breakdown from the catalog importer.
        """

        def _wrap(details: dict[str, int]) -> dict[str, int]:
            return {
                "added": details.get("units_added", 0) + details.get("biomarkers_added", 0),
                "updated": details.get("units_updated", 0) + details.get("biomarkers_updated", 0),
                "unchanged": details.get("units_unchanged", 0)
                + details.get("biomarkers_unchanged", 0),
                "skipped": 0,
                "errors": details.get("errors", 0),
                "details": details,
            }

        if data is None:
            data = bundled_stage_data("default_catalog", self.seeds_dir)

        try:
            payload, identities = _catalog_payload(data)
        except Exception as e:
            logger.error(f"Failed to load default catalog seed: {e}")
            return _wrap(
                {
                    "units_added": 0,
                    "units_updated": 0,
                    "units_unchanged": 0,
                    "biomarkers_added": 0,
                    "biomarkers_updated": 0,
                    "biomarkers_unchanged": 0,
                    "errors": 1,
                }
            )
        result = await CatalogImportService(session).import_catalog(payload, identities=identities)
        for failure in result.failures:
            self.note_item_error(
                "default_catalog",
                {"seed_key": f"{failure.kind}:{failure.reference}"},
                failure.error or "import failure",
            )
        return _wrap(result.stats)

    async def seed_biomarker_panels(
        self,
        session: AsyncSession,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Seed biomarker panel membership edges (MEMBER_OF).

        ``items`` default to the bundled pack's parsed ``biomarker_panels``
        items — each carrying ``{seed_key, panel_slug, biomarker_slug}`` —
        and create ``concept_edges`` rows of ``src_type=biomarker,
        relation=MEMBER_OF, dst_type=concept`` with a deterministic id
        derived from the qualified panel-membership seed key.

        Both endpoints resolve **by identity** (ADR 0055): the deterministic
        uuid5 of the qualified seed key (``concept:<panel-slug>`` /
        ``biomarker:<slug>``), then the ``seed_key`` column — system rows only
        (``tenant_id IS NULL``), so a tenant biomarker sharing a member slug is
        never bound into a curated panel edge. An unresolved endpoint is
        skipped with a logged warning.

        Must run AFTER :meth:`seed_concepts` (panels exist) AND
        :meth:`seed_default_catalog` (biomarkers exist). The caller owns the
        session and the transaction.
        """
        from app.models.biomarker_model import BiomarkerDefinition
        from app.models.concept_model import Concept, ConceptEdge
        from app.models.enums import (
            ConceptKind,
            ConceptProvenance,
            ConceptRelationType,
            EdgeApprovalStatus,
            EdgeEndpointType,
        )

        if items is None:
            items = bundled_stage_items("biomarker_panels", self.seeds_dir)
        items = list(items)

        stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}
        if not await self._table_exists(session, "concept_edges"):
            logger.error("Table 'concept_edges' does not exist. Skipping panel memberships.")
            return {"added": 0, "updated": 0, "unchanged": 0, "skipped": len(items), "errors": 0}

        # Batch-resolve panels + biomarkers once (avoid N+1 over the
        # membership list), by identity: the derived uuid5 of the qualified
        # seed key first, then the ``seed_key`` column — system rows only,
        # never a tenant row that happens to share the slug.
        panel_slugs = {it["panel_slug"] for it in items if it.get("panel_slug")}
        bio_slugs = {it["biomarker_slug"] for it in items if it.get("biomarker_slug")}

        async def _resolve_by_identity(model, prefix: str, keys: set[str], *filters):
            derived = [seed_key_uuid(prefix, k) for k in keys]
            rows: dict[str, Any] = {}
            res = await session.execute(select(model).where(model.id.in_(derived), *filters))
            by_id = {r.id: r for r in res.scalars().all()}
            for k in keys:
                row = by_id.get(seed_key_uuid(prefix, k))
                if row is not None:
                    rows[k] = row
            missing = [k for k in keys if k not in rows]
            if missing and hasattr(model, "seed_key"):
                res = await session.execute(
                    select(model).where(
                        model.seed_key.in_([f"{prefix}:{k}" for k in missing]), *filters
                    )
                )
                for r in res.scalars().all():
                    rows[r.seed_key.split(":", 1)[1]] = r
            return rows

        panel_by_slug = await _resolve_by_identity(
            Concept,
            "concept",
            panel_slugs,
            concepts_with_kind(ConceptKind.BIOMARKER_PANEL),
            Concept.tenant_id.is_(None),
        )
        bio_by_slug = await _resolve_by_identity(
            BiomarkerDefinition,
            "biomarker",
            bio_slugs,
            BiomarkerDefinition.tenant_id.is_(None),
        )

        for it in items:
            try:
                require_seed_key(it, "panel-membership")
                panel = panel_by_slug.get(it.get("panel_slug"))
                bio = bio_by_slug.get(it.get("biomarker_slug"))
                if not panel or not bio:
                    logger.warning(
                        "Panel membership %s/%s resolves to no seeded system row — skipped.",
                        it.get("panel_slug"),
                        it.get("biomarker_slug"),
                    )
                    self.note_unresolved_ref(
                        "biomarker_panels",
                        it,
                        f"panel={it.get('panel_slug')} biomarker={it.get('biomarker_slug')}",
                    )
                    stats["skipped"] += 1
                    continue

                existing = await session.scalar(
                    select(ConceptEdge.id).where(
                        ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                        ConceptEdge.src_id == bio.id,
                        ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                        ConceptEdge.dst_id == panel.id,
                        ConceptEdge.relation == ConceptRelationType.MEMBER_OF,
                        ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                        ConceptEdge.tenant_id.is_(None),
                    )
                )
                if existing is not None:
                    stats["unchanged"] += 1
                    continue

                edge = ConceptEdge(
                    id=seed_key_uuid(
                        "panel-membership", f"{it['panel_slug']}/{it['biomarker_slug']}"
                    ),
                    src_type=EdgeEndpointType.BIOMARKER,
                    src_id=bio.id,
                    dst_type=EdgeEndpointType.CONCEPT,
                    dst_id=panel.id,
                    relation=ConceptRelationType.MEMBER_OF,
                    tenant_id=None,
                    source=ConceptProvenance.SEED,
                    status=EdgeApprovalStatus.APPROVED,
                )
                session.add(edge)
                stats["added"] += 1
            except Exception as e:
                self.note_item_error("biomarker_panels", it, e)
                logger.error(f"Error seeding panel membership {it}: {e}")
                stats["errors"] += 1

        await session.flush()
        return stats

    async def seed_biomarker_states(
        self,
        session: AsyncSession,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Seed the canonical ``biomarker_states`` catalog (universal, no tenant).

        ``items`` defaults to the bundled pack's parsed ``biomarker_states``
        and upserts by identity — the deterministic id derived from the
        item's ``biomarker-state:<slug>`` seed key — reconciling
        display/description/sort_order to the JSON. Codes are drawn from HL7
        v3-ObservationInterpretation, SNOMED CT and FHIR DataAbsentReason
        (plus a ``urn:uuid:health-assistant:custom-state`` namespace for
        proprietary codes like WITHIN_LIMITS / TRACE).

        Standalone — no upstream dependency. A STATE biomarker picks its
        accepted subset via the ``biomarker_allowed_states`` join table
        (managed by the biomarker CRUD endpoints, not the seed). The caller
        owns the session and the transaction.
        """
        from app.models.biomarker_model import BiomarkerState

        if items is None:
            items = bundled_stage_items("biomarker_states", self.seeds_dir)
        items = list(items)

        stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}
        if not await self._table_exists(session, "biomarker_states"):
            logger.error("Table 'biomarker_states' does not exist. Skipping state seed.")
            return {
                "added": 0,
                "updated": 0,
                "unchanged": 0,
                "skipped": len(items),
                "errors": 0,
            }

        for item in items:
            try:
                qualified = require_seed_key(item, "biomarker-state")
                _, action = await upsert_by_identity(
                    session,
                    BiomarkerState,
                    qualified,
                    {
                        "slug": item["slug"],
                        "code": item["code"],
                        "system": item["system"],
                        "display": item["display"],
                        "description": item.get("description"),
                        "category": item.get("category"),
                        "sort_order": item.get("sort_order", 0),
                    },
                    explicit_id=item.get("id"),
                )
                stats[action] += 1
            except Exception as e:
                self.note_item_error("biomarker_states", item, e)
                logger.error(f"Error seeding biomarker state {item.get('slug')}: {e}")
                stats["errors"] += 1
        await session.flush()
        return stats
