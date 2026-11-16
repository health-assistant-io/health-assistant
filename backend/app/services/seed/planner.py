"""The seed pack planner — diff a parsed pack against the database (ADR 0056).

One read-only pass turns a validated :class:`~app.core.seed_pack.ParsedPack`
into a deterministic :class:`PackPlan` document (the JSONB stored in
``seed_import_runs.plan`` — no timestamps, entries sorted by ``seed_key``):

* **Diff by identity** — every item is keyed by its immutable qualified
  ``seed_key`` (ADR 0055); the row id is the deterministic uuid5. Actions:
  ``create`` (no row), ``update`` (content hash differs), ``noop`` (content
  hash matches), ``conflict`` (equal-priority different content — a typed
  conflict that **blocks the apply**), ``skip`` (a higher-priority provider
  wins, or the row is ``user_modified``-frozen and content differs — surfaced
  as ``update_available``), ``override`` (this pack out-prioritizes another
  provider and takes the key over).
* **Precedence** — priority-ascending (bundled 0 < curated remote 100 <
  tenant overlay 200+), taken from the **source row** (registration pins the
  manifest to the same value — the planner never trusts the manifest number
  alone). Providers are active provenance rows of *other* enabled, approved
  sources **in the same tenant scope** (claims are matched on the entity id,
  which encodes the scope — a tenant overlay never conflicts with a system
  source's claim on the same seed key); the best (highest-priority) provider
  decides. Equal priority + different content is a conflict; lower priority
  loses and the loser is recorded (the ``conflict`` payload on the winning
  entry); higher priority wins and the displaced provider is recorded the
  same way.
* **``user_modified`` freeze** — a user-edited row is never auto-updated;
  differing pack content plans ``skip`` + ``update_available`` (adopting it
  is an explicit action, ADR 0056 assumption 5).
* **Retirement** — items this source previously provided but the new pack
  drops are retired (``state=retired`` in provenance; concept rows also
  ``status=retired``; the row itself is never deleted) only when no other
  active approved source still provides the key AND no patient/user data
  references the row (FK discovery against the database catalog; curated /
  seed-internal referrers never block).
* **``replaces``** — a manifest-declared rename merge: the old row is
  retired, curated edges are repointed to the replacement row (executed by
  the applier), patient ``*_catalog_id`` FKs are untouched. The replacement
  is the pack's single same-key-space item; an ambiguous pairing downgrades
  to retire-only with a loud ``REPLACES_AMBIGUOUS`` warning.
* **Warnings** — homoglyph / near-duplicate names within the pack and
  against existing rows (NFKD + casefold equality under different keys).

Tenant-scope packs plan against the tenant's own rows: every identity
lookup is scope-pinned (``tenant_id == <tenant>``), item ids derive
tenant-qualified UUIDs (``uuid5(NS, "<tenant>/<type>:<key>")`` — the
per-tenant-scope uniqueness of ADR 0055), and provider claims are matched
per entity id so tenants (and the system lane) stay isolated. Tenant packs
may only carry tenant-ownable types (the pack validator rejects global-only
item files before a plan exists).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalog_identity import seed_key_uuid
from app.core.seed_pack.items import REGISTRY, ParsedPack
from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerState
from app.models.clinical_event import ClinicalEventType
from app.models.concept_model import Concept, ConceptEdge
from app.models.enums import SeedItemAction
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.vaccine import VaccineCatalog
from app.models.seed_source_model import SeedSource, SeedSourceVersion
from app.services.seed.concepts import _seed_edge_identity
from app.services.seed.identity import find_identity_row, identity_id
from app.services.seed.provenance import ENTITY_TYPE_MODELS, get_provenance

logger = logging.getLogger(__name__)

WARN_NEAR_DUPLICATE = "NEAR_DUPLICATE_NAME"
WARN_REPLACES_AMBIGUOUS = "REPLACES_AMBIGUOUS"
WARN_REPLACES_UNKNOWN_KEY = "REPLACES_UNKNOWN_KEY"

REF_STAGES: frozenset[str] = frozenset({"concept_edges", "biomarker_panels"})
"""Edge stages: applied after EVERY pack's item stages (ADR 0056 assumption 8)."""

DATA_STAGE_COLLECTIONS: dict[str, str] = {"units": "unit", "biomarkers": "biomarker"}
"""``default_catalog`` collections → provenance entity types."""

# Curated/seed-internal referrers that never block a retirement: the catalog
# family itself (edges are repointed by ``replaces``, children are seed churn,
# self-references never dangle because retirement never deletes). Any row
# outside this set referencing the entity is patient/user data — the row is
# kept (never retired).
_PATIENT_REFERRER_EXCLUDED = frozenset(
    {
        "concepts",
        "concept_edges",
        "concept_kind_tags",
        "biomarker_definitions",
        "biomarker_reference_ranges",
        "biomarker_allowed_states",
        "medication_catalog",
        "allergy_catalog",
        "vaccine_catalog",
        "anatomy_structures",
        "anatomy_figures",
        "clinical_event_types",
        "units",
        "seed_sources",
        "seed_source_versions",
        "seed_import_runs",
        "seed_import_run_items",
        "seed_item_provenance",
        "seed_deferred_refs",
    }
)

_KEY_TYPE_TO_ENTITY: dict[str, str] = {
    "concept": "concept",
    "medication": "medication",
    "vaccine": "vaccine",
    "allergy": "allergy",
    "clinical-event-type": "clinical-event-type",
    "anatomy": "anatomy",
    "biomarker": "biomarker",
    "biomarker-state": "biomarker-state",
    "unit": "unit",
    "concept-edge": "concept-edge",
    "panel-membership": "concept-edge",
}


@dataclass(frozen=True)
class StageSpec:
    """One pack stage's plan/apply contract (the planner-side mirror of the
    coverage matrix)."""

    stage: str
    kind: str  # "item" | "data" | "ref" | "content"
    entity_type: str | None
    model: Any | None
    key_type: str | None


def _stage_specs() -> dict[str, StageSpec]:
    by_stage = {item.stage: item for item in REGISTRY}
    specs: dict[str, StageSpec] = {}
    mapping = {
        "concepts": (Concept, "concept"),
        "diseases": (Concept, "concept"),
        "medications": (MedicationCatalog, "medication"),
        "vaccines": (VaccineCatalog, "vaccine"),
        "clinical_event_types": (ClinicalEventType, "clinical-event-type"),
        "allergies": (AllergyCatalog, "allergy"),
        "body_parts": (AnatomyStructure, "anatomy"),
        "biomarker_states": (BiomarkerState, "biomarker-state"),
    }
    for stage, (model, entity_type) in mapping.items():
        specs[stage] = StageSpec(stage, "item", entity_type, model, by_stage[stage].key_space)
    specs["default_catalog"] = StageSpec(
        "default_catalog", "data", None, None, by_stage["default_catalog"].key_space
    )
    specs["concept_edges"] = StageSpec("concept_edges", "ref", "concept-edge", ConceptEdge, None)
    specs["biomarker_panels"] = StageSpec(
        "biomarker_panels", "ref", "concept-edge", ConceptEdge, "panel-membership"
    )
    specs["omb_picklist"] = StageSpec("omb_picklist", "content", None, None, "omb")
    return specs


STAGE_SPECS: dict[str, StageSpec] = _stage_specs()


def canonical_content(item: dict[str, Any]) -> bytes:
    """The content-bearing canonical form of a pack item (identity stripped).

    ``id``/``seed_key`` are identity, not content; everything else (names,
    codes, refs, kinds, ranges) is content. Canonical JSON — sorted keys, no
    whitespace — so the same content always hashes the same, across sources
    and runs.
    """
    payload = {k: v for k, v in item.items() if k not in ("id", "seed_key")}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def content_hash(item: dict[str, Any]) -> str:
    """The item's content hash (``canonical_content`` → sha256)."""
    return hashlib.sha256(canonical_content(item)).hexdigest()


def edge_seed_key(item: dict[str, Any]) -> str:
    """The synthetic tracking key of a ``concept_edges`` item (the rows carry
    no ``seed_key`` column; the identity tuple is the deterministic key)."""
    src_type = item.get("src_type", "concept")
    dst_type = item.get("dst_type", "concept")
    relation = str(item.get("relation", "?"))
    return (
        f"concept-edge:{src_type}:{item.get('src_slug')}/"
        f"{dst_type}:{item.get('dst_slug')}/{relation}"
    )


def edge_entity_id(item: dict[str, Any]) -> uuid.UUID:
    """The deterministic edge row id (mirrors the seeder's derivation)."""
    src_ref = (item.get("src_type", "concept"), item.get("src_slug"))
    dst_ref = (item.get("dst_type", "concept"), item.get("dst_slug"))
    return _seed_edge_identity(src_ref, dst_ref, str(item.get("relation", "?")))


def panel_entity_id(item: dict[str, Any]) -> uuid.UUID:
    """The deterministic panel-membership edge id (mirrors the seeder)."""
    return seed_key_uuid("panel-membership", f"{item['panel_slug']}/{item['biomarker_slug']}")


def panel_seed_key(item: dict[str, Any]) -> str:
    """The qualified tracking key of a panel-membership item."""
    return f"panel-membership:{item['panel_slug']}/{item['biomarker_slug']}"


def normalize_name(value: str | None) -> str:
    """Homoglyph-insensitive comparison form: NFKD, combining marks stripped,
    casefolded, whitespace collapsed."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


@dataclass
class ItemPlan:
    """One pack item's planned outcome (a ``seed_import_run_items`` row in
    waiting). Mutable by design: the decision pass fills action/conflict."""

    stage: str
    entity_type: str
    seed_key: str
    entity_id: uuid.UUID | None
    action: SeedItemAction
    content_hash: str | None = None
    update_available: bool = False
    conflict: dict | None = None
    warnings: tuple[str, ...] = ()

    def to_doc(self) -> dict:
        return {
            "stage": self.stage,
            "entity_type": self.entity_type,
            "seed_key": self.seed_key,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "action": self.action.value,
            "content_hash": self.content_hash,
            "update_available": self.update_available,
            "conflict": self.conflict,
            "warnings": list(self.warnings),
        }


@dataclass
class RetirePlan:
    """A dropped item's retirement decision (never a delete)."""

    entity_type: str
    seed_key: str
    entity_id: uuid.UUID | None
    action: SeedItemAction  # RETIRE or SKIP (still provided / patient-referenced)
    reason: str | None = None
    replaces_new_key: str | None = None
    repoint: bool = False

    def to_doc(self) -> dict:
        return {
            "entity_type": self.entity_type,
            "seed_key": self.seed_key,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "action": self.action.value,
            "reason": self.reason,
            "replaces_new_key": self.replaces_new_key,
            "repoint": self.repoint,
        }


@dataclass
class PackPlan:
    """The planned diff of one pack version (deterministic, JSONB-ready)."""

    pack_id: str
    pack_version: str
    digest: str
    scope: str
    priority: int
    source_id: uuid.UUID | None
    source_kind: str | None
    stages: dict[str, list[ItemPlan]] = field(default_factory=dict)
    retires: list[RetirePlan] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def entries(self) -> list[ItemPlan]:
        return [entry for items in self.stages.values() for entry in items]

    @property
    def conflicts(self) -> list[ItemPlan]:
        return [e for e in self.entries if e.action is SeedItemAction.CONFLICT]

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for entry in self.entries:
            out[entry.action.value] += 1
        for retire in self.retires:
            out[retire.action.value] += 1
        out["warnings"] = sum(len(e.warnings) for e in self.entries) + len(self.warnings)
        out["errors"] = len(self.errors)
        return dict(sorted(out.items()))

    def to_doc(self) -> dict:
        """The deterministic JSONB plan document (``seed_import_runs.plan``)."""
        return {
            "pack": {
                "id": self.pack_id,
                "version": self.pack_version,
                "digest": self.digest,
                "scope": self.scope,
                "priority": self.priority,
            },
            "source": {
                "id": str(self.source_id) if self.source_id else None,
                "kind": self.source_kind,
            },
            "stages": {
                stage: [entry.to_doc() for entry in items]
                for stage, items in sorted(self.stages.items())
            },
            "retire": [retire.to_doc() for retire in self.retires],
            "warnings": sorted(self.warnings, key=lambda w: json.dumps(w, sort_keys=True)),
            "errors": list(self.errors),
            "summary": self.summary(),
        }


@dataclass(frozen=True)
class _Provider:
    """Another source's active claim on an (entity_type, seed_key).

    ``entity_id`` is the claimed row's id — it encodes the tenant scope
    (tenant overlays derive tenant-qualified ids), so the planner can keep
    cross-scope claims out of a tenant pack's diff.
    """

    source_id: uuid.UUID
    version_id: uuid.UUID | None
    priority: int
    content_hash: str | None
    entity_id: uuid.UUID | None = None


async def _load_providers(session: AsyncSession) -> dict[tuple[str, str], list[_Provider]]:
    """Every active provenance claim joined to its source, indexed for the diff.

    Only rows whose version is ``approved`` and whose source is ``enabled``
    count as a live provider — a revoked or disabled source does not hold a
    key against anyone.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT p.entity_type  AS entity_type,
                       p.seed_key     AS seed_key,
                       p.content_hash AS content_hash,
                       p.entity_id    AS entity_id,
                       s.id           AS source_id,
                       v.id           AS version_id,
                       s.priority     AS priority
                FROM seed_item_provenance p
                JOIN seed_source_versions v ON v.id = p.source_version_id
                JOIN seed_sources s ON s.id = v.source_id
                WHERE p.state = 'active'
                  AND p.seed_key IS NOT NULL
                  AND v.status = 'approved'
                  AND s.enabled IS TRUE
                """
            )
        )
    ).mappings()
    out: dict[tuple[str, str], list[_Provider]] = defaultdict(list)
    for row in rows:
        out[(row["entity_type"], row["seed_key"])].append(
            _Provider(
                source_id=row["source_id"],
                version_id=row["version_id"],
                priority=row["priority"] or 0,
                content_hash=row["content_hash"],
                entity_id=row["entity_id"],
            )
        )
    return out


class SeedPackPlanner:
    """Diff one pack version against the database into a :class:`PackPlan`."""

    def __init__(
        self,
        session: AsyncSession,
        source: SeedSource | None,
        version: SeedSourceVersion | None,
        pack: ParsedPack,
    ):
        self.session = session
        self.source = source
        self.version = version
        self.pack = pack
        self._tenant_id = source.tenant_id if source is not None else None
        """The overlay lane: ``None`` = system scope; a tenant id = the pack
        plans (and its applier writes) that tenant's rows — the
        ``scope_tenant_match`` constraint guarantees a tenant-scope source
        always carries its ``tenant_id``."""
        self._providers: dict[tuple[str, str], list[_Provider]] = {}
        self._prior_keys: dict[tuple[str, str], uuid.UUID | None] = {}

    @property
    def priority(self) -> int:
        """The apply precedence — the **source row's** priority (registration
        pins the manifest to the same value; the manifest number alone is
        never trusted, program Phase 10 item 5)."""
        if self.source is not None and self.source.priority is not None:
            return int(self.source.priority)
        return int(self.pack.manifest.get("priority", 0))

    async def plan(self) -> PackPlan:
        """Produce the plan (read-only; runs inside the applier's lock)."""
        scope = self.source.scope.value if self.source is not None and self.source.scope else None
        self._providers = await _load_providers(self.session)
        await self._load_prior_keys()
        plan = PackPlan(
            pack_id=str(self.pack.manifest.get("id")),
            pack_version=str(self.pack.manifest.get("version")),
            digest=self.pack.digest,
            scope=scope or str(self.pack.manifest.get("scope", "system")),
            priority=self.priority,
            source_id=self.source.id if self.source else None,
            source_kind=self.source.kind.value if self.source and self.source.kind else None,
        )
        for stage in self._pack_stages():
            spec = STAGE_SPECS[stage]
            if spec.kind == "content":
                continue  # delivered through the pack path; seeds no rows
            plan.stages[stage] = await self._plan_stage(stage, spec)
        plan.retires = await self._plan_retires(plan)
        plan.warnings.extend(await self._near_duplicate_warnings(plan))
        return plan

    def _pack_stages(self) -> list[str]:
        declared = sorted({parsed.stage for parsed in self.pack.files.values()})
        return [stage for stage in declared if stage in STAGE_SPECS]

    async def _load_prior_keys(self) -> None:
        """The (entity_type, seed_key) → entity_id map this source provides."""
        if self.source is None:
            return
        rows = (
            await self.session.execute(
                text(
                    """
                    SELECT p.entity_type AS entity_type,
                           p.seed_key    AS seed_key,
                           p.entity_id   AS entity_id
                    FROM seed_item_provenance p
                    JOIN seed_source_versions v ON v.id = p.source_version_id
                    WHERE v.source_id = :sid AND p.state = 'active'
                          AND p.seed_key IS NOT NULL
                    """
                ),
                {"sid": self.source.id},
            )
        ).mappings()
        for row in rows:
            self._prior_keys[(row["entity_type"], row["seed_key"])] = row["entity_id"]

    def _entry_key(self, spec: StageSpec, item: dict[str, Any]) -> str:
        if spec.stage == "concept_edges":
            return edge_seed_key(item)
        if spec.stage == "biomarker_panels":
            return panel_seed_key(item)
        return str(item.get("seed_key"))

    async def _entity_id(self, spec: StageSpec, item: dict[str, Any]) -> uuid.UUID | None:
        """The deterministic row id of an item (``None`` = not derivable)."""
        try:
            if spec.stage == "concept_edges":
                return edge_entity_id(item)
            if spec.stage == "biomarker_panels":
                return panel_entity_id(item)
            if spec.entity_type is None or spec.model is None:
                return None
            qualified = str(item.get("seed_key") or "")
            if not qualified and spec.entity_type == "anatomy":
                qualified = f"anatomy:{item.get('slug')}"
            if not qualified:
                return None
            return identity_id(qualified, item.get("id"), tenant_id=self._tenant_id)
        except (ValueError, KeyError, TypeError):
            return None

    async def _plan_stage(self, stage: str, spec: StageSpec) -> list[ItemPlan]:
        if stage == "default_catalog":
            return await self._plan_default_catalog(spec)
        entries: list[ItemPlan] = []
        for item in self.pack.stage_items(stage):
            entry = ItemPlan(
                stage=stage,
                entity_type=spec.entity_type or "",
                seed_key=self._entry_key(spec, item),
                entity_id=await self._entity_id(spec, item),
                action=SeedItemAction.NOOP,
                content_hash=content_hash(item),
            )
            await self._decide(spec, item, entry)
            entries.append(entry)
        entries.sort(key=lambda e: e.seed_key)
        return entries

    async def _plan_default_catalog(self, spec: StageSpec) -> list[ItemPlan]:
        entries: list[ItemPlan] = []
        data = self.pack.stage_data("default_catalog")
        for collection, entity_type in DATA_STAGE_COLLECTIONS.items():
            sub_spec = StageSpec(
                spec.stage,
                "item",
                entity_type,
                ENTITY_TYPE_MODELS[entity_type],
                "catalog",
            )
            for item in data.get(collection, []):
                entry = ItemPlan(
                    stage=spec.stage,
                    entity_type=entity_type,
                    seed_key=str(item.get("seed_key") or item.get("symbol") or item.get("slug")),
                    entity_id=await self._entity_id(sub_spec, item),
                    action=SeedItemAction.NOOP,
                    content_hash=content_hash(item),
                )
                await self._decide(sub_spec, item, entry)
                entries.append(entry)
        entries.sort(key=lambda e: (e.entity_type, e.seed_key))
        return entries

    async def _decide(self, spec: StageSpec, item: dict[str, Any], entry: ItemPlan) -> None:
        """The per-item decision: precedence → ``user_modified`` freeze →
        content comparison against provenance."""
        try:
            await self._decide_inner(spec, item, entry)
        except Exception as exc:  # a planning failure is a recorded skip, not a crash
            logger.warning("plan decision failed for %s: %s", entry.seed_key, exc)
            entry.action = SeedItemAction.SKIP
            entry.conflict = {"reason": "plan_error", "error": str(exc)}

    async def _decide_inner(self, spec: StageSpec, item: dict[str, Any], entry: ItemPlan) -> None:
        ours = self.priority
        providers = [
            p
            for p in self._providers.get((entry.entity_type, entry.seed_key), ())
            if self.source is None or p.source_id != self.source.id
        ]
        if self._tenant_id is not None and entry.entity_id is not None:
            # Tenant overlay isolation: provider claims are matched on the
            # entity id (which encodes the scope) — another tenant's (or the
            # system lane's) claim on the same seed key never enters this
            # pack's precedence decision.
            providers = [
                p for p in providers if p.entity_id is None or p.entity_id == entry.entity_id
            ]
        best = max(providers, key=lambda p: p.priority, default=None)

        if best is not None and best.priority > ours:
            entry.action = SeedItemAction.SKIP
            entry.conflict = {
                "role": "loser",
                "reason": "higher_priority_provider",
                "winner_source_id": str(best.source_id),
                "winner_priority": best.priority,
            }
            return
        if best is not None and best.priority == ours:
            if best.content_hash != entry.content_hash:
                entry.action = SeedItemAction.CONFLICT
                entry.conflict = {
                    "role": "conflict",
                    "reason": "equal_priority_different_content",
                    "other_source_id": str(best.source_id),
                    "other_content_hash": best.content_hash,
                }
            else:
                entry.action = SeedItemAction.NOOP
                entry.conflict = {
                    "role": "peer",
                    "reason": "equal_priority_same_content",
                    "other_source_id": str(best.source_id),
                }
            return

        if best is not None:
            entry.action = SeedItemAction.OVERRIDE
            entry.conflict = {
                "role": "winner",
                "reason": "higher_priority_takes_over",
                "loser_source_id": str(best.source_id),
                "loser_priority": best.priority,
                "loser_content_hash": best.content_hash,
            }

        existing = None
        if spec.stage in REF_STAGES and entry.entity_id is not None:
            # Edges carry no seed_key column (their identity is the
            # deterministic tuple id) — look the row up by id, system scope.
            # Tenant packs never carry ref stages (the capability matrix
            # rejects global-only item files before a plan exists).
            existing = (
                await self.session.execute(
                    select(spec.model).where(
                        spec.model.id == entry.entity_id, spec.model.tenant_id.is_(None)
                    )
                )
            ).scalar_one_or_none()
        elif spec.model is not None and entry.seed_key:
            existing = await find_identity_row(
                self.session, spec.model, entry.seed_key, tenant_id=self._tenant_id
            )
            if (
                existing is not None
                and entry.entity_id is not None
                and existing.id != entry.entity_id
            ):
                entry.entity_id = existing.id
        if existing is None:
            if entry.action is SeedItemAction.NOOP:
                entry.action = SeedItemAction.CREATE
            return

        prov = (
            await get_provenance(self.session, entry.entity_type, existing.id)
            if entry.entity_id is not None
            else None
        )
        if prov is not None and prov.user_modified:
            if prov.content_hash != entry.content_hash:
                entry.action = SeedItemAction.SKIP
                entry.update_available = True
                entry.conflict = {
                    **(entry.conflict or {}),
                    "reason": "user_modified_freeze",
                }
            elif entry.action is SeedItemAction.OVERRIDE:
                entry.action = SeedItemAction.NOOP
            else:
                entry.action = SeedItemAction.NOOP
            return
        if prov is None or prov.content_hash != entry.content_hash:
            if entry.action is SeedItemAction.NOOP:
                entry.action = SeedItemAction.UPDATE
        elif entry.action is SeedItemAction.OVERRIDE:
            # Same content as the row already carries — the takeover is still
            # recorded, but nothing needs writing beyond provenance.
            entry.action = SeedItemAction.OVERRIDE

    async def _plan_retires(self, plan: PackPlan) -> list[RetirePlan]:
        """Keys this source previously provided that the new pack drops, plus
        the manifest's ``replaces`` targets (the rename merges).

        ``replaces`` entries are key BODIES (the frozen contract pattern
        forbids ``:``); each body resolves against the pack's own key spaces
        — ``<type>:<body>`` — and must resolve to exactly one existing row.
        """
        replace_bodies = sorted({str(k) for k in self.pack.manifest.get("replaces") or []})
        pack_types = {e.seed_key.split(":", 1)[0] for e in plan.entries}
        replace_keys: list[str] = []
        for body in replace_bodies:
            resolved = await self._resolve_replaces_key(body, plan, pack_types)
            if resolved is not None:
                replace_keys.append(resolved)
        pack_keys = {
            (e.entity_type, e.seed_key)
            for e in plan.entries
            if e.action is not SeedItemAction.CONFLICT
        }
        out: list[RetirePlan] = []
        for (entity_type, seed_key), entity_id in sorted(self._prior_keys.items()):
            if (entity_type, seed_key) in pack_keys:
                continue
            out.append(await self._retire_decision(entity_type, seed_key, entity_id))
        for replaced_key in replace_keys:
            if any(r.seed_key == replaced_key for r in out):
                continue
            entity_type = _KEY_TYPE_TO_ENTITY.get(replaced_key.split(":", 1)[0])
            if entity_type is None:
                plan.warnings.append({"code": WARN_REPLACES_UNKNOWN_KEY, "seed_key": replaced_key})
                continue
            entity_id = self._prior_keys.get((entity_type, replaced_key))
            if entity_id is None:
                model = ENTITY_TYPE_MODELS.get(entity_type)
                row = await self._find_by_key(model, replaced_key)
                entity_id = row.id if row is not None else None
            if entity_id is None:
                plan.warnings.append({"code": WARN_REPLACES_UNKNOWN_KEY, "seed_key": replaced_key})
                continue
            out.append(
                await self._retire_decision(entity_type, replaced_key, entity_id, reason="replaced")
            )
        for decision in out:
            body = decision.seed_key.split(":", 1)[-1]
            if body in replace_bodies:
                decision.replaces_new_key = self._replacement_for(decision.seed_key, plan)
                decision.repoint = decision.replaces_new_key is not None
                if not decision.repoint:
                    plan.warnings.append(
                        {
                            "code": WARN_REPLACES_AMBIGUOUS,
                            "seed_key": body,
                            "reason": "no_single_replacement_in_pack",
                        }
                    )
        out.sort(key=lambda r: (r.entity_type, r.seed_key))
        return out

    async def _resolve_replaces_key(
        self, body: str, plan: PackPlan, pack_types: set[str]
    ) -> str | None:
        """Resolve a body-only ``replaces`` entry to one qualified seed key.

        Candidates are ``<type>:<body>`` over the pack's key spaces; the entry
        resolves only when exactly one candidate exists (in this source's
        prior keys or the database). Zero or several → a loud warning and no
        retirement/repoint for that entry.
        """
        candidates = [f"{key_type}:{body}" for key_type in sorted(pack_types)]
        found: list[str] = []
        for qualified in candidates:
            entity_type = _KEY_TYPE_TO_ENTITY.get(qualified.split(":", 1)[0])
            if entity_type is None:
                continue
            if (entity_type, qualified) in self._prior_keys:
                found.append(qualified)
                continue
            model = ENTITY_TYPE_MODELS.get(entity_type)
            if model is not None and await self._find_by_key(model, qualified) is not None:
                found.append(qualified)
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            plan.warnings.append(
                {"code": WARN_REPLACES_AMBIGUOUS, "seed_key": body, "candidates": sorted(found)}
            )
        else:
            plan.warnings.append({"code": WARN_REPLACES_UNKNOWN_KEY, "seed_key": body})
        return None

    async def _retire_decision(
        self,
        entity_type: str,
        seed_key: str,
        entity_id: uuid.UUID | None,
        reason: str = "dropped_by_pack",
    ) -> RetirePlan:
        """Retire only when nothing else provides the key and no patient/user
        data references the row (the row itself is never deleted).

        "Provides" is provenance OR a successful apply-run item of another
        enabled, approved source — identical content at equal priority leaves
        the provenance pointer with one owner, but every provider's run
        history records the claim.
        """
        if await self._other_source_provides(entity_type, seed_key, entity_id):
            return RetirePlan(
                entity_type=entity_type,
                seed_key=seed_key,
                entity_id=entity_id,
                action=SeedItemAction.NOOP,
                reason="still_provided_by_active_source",
            )
        if entity_id is not None and await self._patient_referenced(entity_type, entity_id):
            return RetirePlan(
                entity_type=entity_type,
                seed_key=seed_key,
                entity_id=entity_id,
                action=SeedItemAction.SKIP,
                reason="patient_referenced",
            )
        return RetirePlan(
            entity_type=entity_type,
            seed_key=seed_key,
            entity_id=entity_id,
            action=SeedItemAction.RETIRE,
            reason=reason,
        )

    async def _other_source_provides(
        self, entity_type: str, seed_key: str, entity_id: uuid.UUID | None = None
    ) -> bool:
        """Whether another enabled source with an approved version last
        applied this key successfully (create/update/noop/override). The
        claim is matched per entity id when one is known — a same-key claim
        on a different row (another tenant's overlay) never blocks."""
        from app.models.enums import SeedRunStatus as _RunStatus
        from app.models.enums import SeedVersionStatus as _VersionStatus
        from app.models.seed_source_model import (
            SeedImportRun,
            SeedImportRunItem,
            SeedSourceVersion,
        )

        stmt = (
            select(SeedImportRunItem.id)
            .join(SeedImportRun, SeedImportRun.id == SeedImportRunItem.run_id)
            .join(SeedSourceVersion, SeedSourceVersion.id == SeedImportRun.source_version_id)
            .join(SeedSource, SeedSource.id == SeedSourceVersion.source_id)
            .where(
                SeedImportRunItem.entity_type == entity_type,
                SeedImportRunItem.seed_key == seed_key,
                SeedImportRunItem.action.in_(
                    [
                        SeedItemAction.CREATE,
                        SeedItemAction.UPDATE,
                        SeedItemAction.NOOP,
                        SeedItemAction.OVERRIDE,
                    ]
                ),
                SeedImportRun.mode == "apply",
                SeedImportRun.status.in_((_RunStatus.OK, _RunStatus.PARTIAL)),
                SeedSourceVersion.status == _VersionStatus.APPROVED,
                SeedSource.enabled.is_(True),
            )
            .limit(1)
        )
        if self.source is not None:
            stmt = stmt.where(SeedSource.id != self.source.id)
        if entity_id is not None:
            # Scope isolation: a claim on the same seed key but a different
            # row (another tenant's overlay, or a forked explicit id) does
            # not keep THIS row alive.
            stmt = stmt.where(SeedImportRunItem.entity_id == entity_id)
        return (await self.session.execute(stmt)).first() is not None

    def _replacement_for(self, replaced_key: str, plan: PackPlan) -> str | None:
        """The pack item that replaces ``replaced_key``: the single same-key-space
        candidate; an ambiguous or absent pairing repoints nothing."""
        key_type, _body = replaced_key.split(":", 1)
        candidates = {
            e.seed_key
            for e in plan.entries
            if e.seed_key.split(":", 1)[0] == key_type and e.seed_key != replaced_key
        }
        return next(iter(candidates)) if len(candidates) == 1 else None

    async def _patient_referenced(self, entity_type: str, entity_id: uuid.UUID) -> bool:
        """Whether any non-catalog table references the entity row.

        Discovers the FKs from the database catalog (the same inventory shape
        the hard reseed uses) and counts referencing rows for every referrer
        outside the curated/seed-internal set — those are patient/user data,
        so the row survives untouched.
        """
        table = _ENTITY_TABLES.get(entity_type)
        if table is None:
            return False
        fks = (
            await self.session.execute(
                text(
                    """
                    SELECT src.relname AS src_table,
                           array_agg(src_att.attname ORDER BY cols.ord) AS src_cols
                    FROM pg_constraint con
                    JOIN pg_class src ON src.oid = con.conrelid
                    JOIN pg_class dst ON dst.oid = con.confrelid
                    JOIN unnest(con.conkey, con.confkey) WITH ORDINALITY
                         AS cols(src_attnum, dst_attnum, ord) ON TRUE
                    JOIN pg_attribute src_att
                         ON src_att.attrelid = src.oid AND src_att.attnum = cols.src_attnum
                    WHERE con.contype = 'f'
                      AND dst.relname = :dst
                      AND src.relname <> dst.relname
                      AND src.relname NOT IN :excluded
                    GROUP BY con.oid, src.relname
                    """
                ).bindparams(bindparam("excluded", expanding=True)),
                {"dst": table, "excluded": sorted(_PATIENT_REFERRER_EXCLUDED)},
            )
        ).mappings()
        for fk in fks:
            cols = list(fk["src_cols"])
            if len(cols) != 1:
                continue
            column = _validate_identifier(cols[0])
            src_table = _validate_identifier(fk["src_table"])
            count = (
                await self.session.execute(
                    text(f"SELECT count(*) FROM {src_table} WHERE {column} = :eid"),
                    {"eid": entity_id},
                )
            ).scalar_one()
            if count:
                return True
        return False

    async def _find_by_key(self, model: Any, qualified_key: str) -> Any:
        """A curated row by deterministic id, then ``seed_key`` — scope-pinned
        (system rows for the system lane, the pack's tenant rows for an
        overlay pack)."""
        if model is None:
            return None
        try:
            row_id = identity_id(qualified_key, tenant_id=self._tenant_id)
        except ValueError:
            return None
        if self._tenant_id is not None and hasattr(model, "tenant_id"):
            tenant_filter = model.tenant_id == self._tenant_id
        elif hasattr(model, "tenant_id"):
            tenant_filter = model.tenant_id.is_(None)
        else:
            tenant_filter = None
        stmt = select(model).where(model.id == row_id)
        if tenant_filter is not None:
            stmt = stmt.where(tenant_filter)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None and hasattr(model, "seed_key"):
            stmt = select(model).where(model.seed_key == qualified_key)
            if tenant_filter is not None:
                stmt = stmt.where(tenant_filter)
            row = (await self.session.execute(stmt)).scalar_one_or_none()
        return row

    async def _near_duplicate_warnings(self, plan: PackPlan) -> list[dict]:
        """Homoglyph / near-duplicate name pairs (in-pack + against the DB)."""
        warnings: list[dict] = []
        for stage, entries in sorted(plan.stages.items()):
            spec = STAGE_SPECS[stage]
            seen: dict[str, str] = {}
            for entry in entries:
                label = normalize_name(self._display_name(entry))
                if not label:
                    continue
                if label in seen and seen[label] != entry.seed_key:
                    warnings.append(
                        {
                            "code": WARN_NEAR_DUPLICATE,
                            "stage": stage,
                            "seed_key": entry.seed_key,
                            "other_seed_key": seen[label],
                        }
                    )
                seen.setdefault(label, entry.seed_key)
            db_names = await self._stage_db_names(spec)
            for entry in entries:
                label = normalize_name(self._display_name(entry))
                if label and label in db_names and entry.seed_key not in db_names[label]:
                    others = sorted(k for k in db_names[label] if k)
                    if not others:
                        continue
                    warnings.append(
                        {
                            "code": WARN_NEAR_DUPLICATE,
                            "stage": stage,
                            "seed_key": entry.seed_key,
                            "other_seed_key": others[0],
                            "scope": "database",
                        }
                    )
        dedup = {json.dumps(w, sort_keys=True): w for w in warnings}
        deduped = [dedup[k] for k in sorted(dedup)]
        flagged: dict[str, set[str]] = {}
        for w in deduped:
            if w.get("code") == WARN_NEAR_DUPLICATE:
                flagged.setdefault(w["seed_key"], set()).add(w["other_seed_key"])
                flagged.setdefault(w["other_seed_key"], set()).add(w["seed_key"])
        for entries in plan.stages.values():
            for entry in entries:
                if entry.seed_key in flagged:
                    entry.warnings = tuple(sorted({WARN_NEAR_DUPLICATE, *entry.warnings}))
        return deduped

    def _display_name(self, entry: ItemPlan) -> str | None:
        item = self._item_for(entry)
        if not isinstance(item, dict):
            return None
        return item.get("name") or item.get("slug") or item.get("display")

    def _item_for(self, entry: ItemPlan) -> dict[str, Any] | None:
        for parsed in self.pack.stage_files(entry.stage):
            for item in parsed.items():
                if self._entry_key(STAGE_SPECS[entry.stage], item) == entry.seed_key:
                    return item
        for rows in self.pack.stage_data(entry.stage).values():
            for item in rows:
                if str(item.get("seed_key") or item.get("symbol") or "") == entry.seed_key:
                    return item
        return None

    async def _stage_db_names(self, spec: StageSpec) -> dict[str, set[str]]:
        """Normalized display name → seed keys of existing rows in the pack's
        scope (system rows for the system lane, the tenant's rows for an
        overlay pack).

        Models without a ``seed_key`` column (anatomy, clinical event types)
        cannot be identity-compared — return nothing.
        """
        model = spec.model
        if model is None or not hasattr(model, "name") or not hasattr(model, "seed_key"):
            return {}
        stmt = select(model.name, model.seed_key)
        if hasattr(model, "tenant_id"):
            if self._tenant_id is not None:
                stmt = stmt.where(model.tenant_id == self._tenant_id)
            else:
                stmt = stmt.where(model.tenant_id.is_(None))
        rows = (await self.session.execute(stmt)).all()
        out: dict[str, set[str]] = defaultdict(set)
        for name, seed_key in rows:
            label = normalize_name(name)
            if label:
                out[label].add(seed_key or "")
        return out


_ENTITY_TABLES: dict[str, str] = {
    "concept": "concepts",
    "medication": "medication_catalog",
    "vaccine": "vaccine_catalog",
    "allergy": "allergy_catalog",
    "clinical-event-type": "clinical_event_types",
    "anatomy": "anatomy_structures",
    "biomarker": "biomarker_definitions",
    "biomarker-state": "biomarker_states",
    "unit": "units",
    "concept-edge": "concept_edges",
}


def _validate_identifier(identifier: str) -> str:
    """Refuse anything that is not a plain lowercase SQL identifier (the FK
    inventory returns catalog identifiers; this keeps the interpolation safe)."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", identifier):
        raise ValueError(f"unsafe identifier from FK inventory: {identifier!r}")
    return identifier
