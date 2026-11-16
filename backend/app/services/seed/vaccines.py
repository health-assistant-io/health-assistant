"""Vaccine catalog seeding (identity upsert — ADR 0055).

Every curated item carries an immutable qualified ``seed_key``
(``vaccine:<kebab-key>``); the row id is the deterministic ``uuid5``
derived from it. Upsert is by id, then ``seed_key`` (scope-pinned: system
rows ``tenant_id IS NULL``, tenant overlay rows the pack's tenant) — the
``slug`` stays display data and is never a join key.

The item's readable ``target_diseases`` refs are materialized as approved
``PREVENTS`` ``concept_edges`` rows (``src_type=immunization``,
``source=seed``): each ref resolves to the disease concept **by identity**
(the deterministic uuid5 of its ``concept:<slug>`` seed key, with the
``seed_key``-column fallback — never by name), and the edge id is the
uuid5 of ``vaccine-prevents:<vaccine-key>/<disease-key>``. The graph is the
single source of truth for vaccine → disease links (migration 0012 dropped
the JSONB column); the seed file keeps the human-readable list. Edge
seeding is create-missing only — like every stage, nothing is deleted by a
seed (the hard reseed converges stale links).

Transaction ownership (ADR 0056): the seeder takes a required session and
parsed pack items, flushes, and never commits — the caller owns the
transaction.
"""

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalog_identity import seed_key_uuid
from app.core.seed_pack.bundled import bundled_stage_items
from app.services.seed.identity import item_label, require_seed_key, upsert_by_identity

logger = logging.getLogger(__name__)

_JSON_FIELDS = ("dose_schedule", "side_effects")
_OPTIONAL_FIELDS = ("description", "contraindications")


def vaccine_values(item: dict[str, Any]) -> dict[str, Any]:
    """The mutable fields of a vaccine seed item (slug included as display)."""
    values: dict[str, Any] = {
        "slug": item["slug"],
        "name": item["name"],
        "coding_system": item.get("coding_system", "cvx"),
    }
    if item.get("code") is not None:
        values["code"] = item["code"]
    for field in _OPTIONAL_FIELDS:
        if item.get(field) is not None:
            values[field] = item[field]
    for field in _JSON_FIELDS:
        if item.get(field) is not None:
            values[field] = item[field]
    return values


class VaccineSeedMixin:
    async def seed_vaccines(
        self,
        session: AsyncSession,
        items: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Sync CVX-coded vaccine reference definitions + their PREVENTS edges.

        Upserts rows by id, then ``seed_key`` (system rows, ``tenant_id=None``);
        the item's ``target_diseases`` refs materialize as approved ``PREVENTS``
        ``concept_edges`` rows resolved by identity. ``items`` defaults to the
        bundled pack's parsed vaccines; the caller owns the session and the
        transaction (flush only, no commit). The stage totals count rows and
        edges together; ``details`` carries the per-kind breakdown.
        """
        if items is None:
            items = bundled_stage_items("vaccines", self.seeds_dir)
        return await self._process_vaccines(session, list(items))

    async def _process_vaccines(
        self, session: AsyncSession, data: Sequence[dict[str, Any]]
    ) -> dict[str, int]:
        from app.models.fhir.vaccine import VaccineCatalog

        stats = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}
        edges = {"added": 0, "unchanged": 0, "skipped": 0}
        data = data if isinstance(data, list) else data.get("items", [])
        if not await self._table_exists(session, "vaccine_catalog"):
            logger.error("Table 'vaccine_catalog' does not exist. Skipping vaccines.")
            return {"added": 0, "updated": 0, "unchanged": 0, "skipped": len(data), "errors": 0}
        for item in data:
            try:
                qualified = require_seed_key(item, "vaccine")
                _, vaccine_key = qualified.split(":", 1)
                row, action = await upsert_by_identity(
                    session,
                    VaccineCatalog,
                    qualified,
                    vaccine_values(item),
                    tenant_id=self.tenant_id,
                    explicit_id=item.get("id"),
                    json_fields=_JSON_FIELDS,
                )
                stats[action] += 1
                await self._seed_prevents_edges(
                    session,
                    row,
                    vaccine_key,
                    item.get("target_diseases") or [],
                    edges,
                    tenant_id=self.tenant_id,
                    item=item,
                )
            except Exception as e:
                self.note_item_error("vaccines", item, e)
                logger.error(f"Error seeding vaccine {item_label(item)}: {e}")
                stats["errors"] += 1

        await session.flush()
        stats["added"] += edges["added"]
        stats["unchanged"] += edges["unchanged"]
        stats["skipped"] += edges["skipped"]
        stats["details"] = {"edges": edges}
        return stats

    async def _seed_prevents_edges(
        self,
        session: AsyncSession,
        vaccine_row,
        vaccine_key: str,
        disease_slugs: list[str],
        edges_stats: dict[str, int],
        *,
        tenant_id=None,
        item: dict[str, Any] | None = None,
    ) -> None:
        """Materialize one vaccine's ``target_diseases`` refs as PREVENTS edges.

        Each ref resolves to the system disease concept by identity
        (uuid5 of ``concept:<slug>``, then the ``seed_key`` column); an
        unresolvable ref is recorded as an unresolved reference (the applier
        defers it) and skipped with a logged warning — never guessed by name.
        The edge id is the uuid5 of the qualified pair (tenant-scoped for an
        overlay vaccine — ``scoped_seed_uuid`` — so a tenant pack reusing a
        system key derives its own edges), so a reseed is edge-id-stable; an
        identical approved tuple short-circuits. A tenant overlay vaccine's
        edges inherit its tenancy; the disease endpoints stay system rows.
        """
        from app.models.concept_model import Concept, ConceptEdge
        from app.models.enums import (
            ConceptProvenance,
            ConceptRelationType,
            EdgeApprovalStatus,
            EdgeEndpointType,
        )
        from app.services.seed.identity import scoped_seed_uuid

        for disease_slug in dict.fromkeys(disease_slugs):
            derived = seed_key_uuid("concept", disease_slug)
            concept_id = await session.scalar(
                select(Concept.id).where(
                    Concept.id == derived,
                    Concept.tenant_id.is_(None),
                    Concept.deleted_at.is_(None),
                )
            )
            if concept_id is None:
                concept_id = await session.scalar(
                    select(Concept.id).where(
                        Concept.seed_key == f"concept:{disease_slug}",
                        Concept.tenant_id.is_(None),
                        Concept.deleted_at.is_(None),
                    )
                )
            if concept_id is None:
                logger.warning(
                    "vaccine %s: target disease ref %r resolves to no seeded "
                    "disease concept — edge skipped",
                    vaccine_key,
                    disease_slug,
                )
                if item is not None:
                    self.note_unresolved_ref("vaccines", item, f"target_diseases={disease_slug}")
                edges_stats["skipped"] += 1
                continue
            tenant_edge = (
                ConceptEdge.tenant_id.is_(None)
                if tenant_id is None
                else ConceptEdge.tenant_id == tenant_id
            )
            existing = await session.scalar(
                select(ConceptEdge.id).where(
                    tenant_edge,
                    ConceptEdge.src_type == EdgeEndpointType.IMMUNIZATION,
                    ConceptEdge.src_id == vaccine_row.id,
                    ConceptEdge.dst_type == EdgeEndpointType.CONCEPT,
                    ConceptEdge.dst_id == concept_id,
                    ConceptEdge.relation == ConceptRelationType.PREVENTS,
                    ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                )
            )
            if existing is not None:
                edges_stats["unchanged"] += 1
                continue
            session.add(
                ConceptEdge(
                    id=scoped_seed_uuid(
                        "vaccine-prevents", f"{vaccine_key}/{disease_slug}", tenant_id=tenant_id
                    ),
                    src_type=EdgeEndpointType.IMMUNIZATION,
                    src_id=vaccine_row.id,
                    dst_type=EdgeEndpointType.CONCEPT,
                    dst_id=concept_id,
                    relation=ConceptRelationType.PREVENTS,
                    tenant_id=tenant_id,
                    source=ConceptProvenance.SEED,
                    status=EdgeApprovalStatus.APPROVED,
                )
            )
            edges_stats["added"] += 1
