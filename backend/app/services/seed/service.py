"""The SeedService composition root — one seeding face over the entity
families. _SEED_STAGE_NAMES and seed_all stay here; the family seeders
live in the sibling modules (methods moved verbatim, mixin composition
keeps the public class face).

Transaction ownership (ADR 0056 Phase 2): every seeder takes a **required
session** and never opens its own or commits — the caller owns the
transaction (boot applies through the pack applier in ``main.py`` — one
transaction per stage, provenance + audit; the CLIs commit after their
stage; the hard reseed commits once). Item content comes from the
**bundled pack** (``app.core.seed_pack.bundled`` — manifest + hashed item
files), not from ad-hoc file reads; ``seed_all`` parses the pack once and
dispatches each stage's parsed items to its seeder. The applier reuses the
same stage dispatch per stage, filtered through the plan.

Per-item collection: the seeders record item failures via
:meth:`SeedService.note_item_error` and unresolved cross-item references
via :meth:`SeedService.note_unresolved_ref` — the applier persists them as
run items and ``seed_deferred_refs`` rows (retried on any new approval).
"""

import json
import logging
from pathlib import Path
from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seed_pack.bundled import load_bundled_pack
from app.services.seed.allergies import AllergySeedMixin
from app.services.seed.anatomy import AnatomySeedMixin
from app.services.seed.catalogs import CatalogSeedMixin
from app.services.seed.clinical_events import ClinicalEventSeedMixin
from app.services.seed.concepts import ConceptSeedMixin
from app.services.seed.medications import MedicationSeedMixin
from app.services.seed.system import SystemSeedMixin
from app.services.seed.vaccines import VaccineSeedMixin

logger = logging.getLogger(__name__)


class SeedService(
    ClinicalEventSeedMixin,
    MedicationSeedMixin,
    AnatomySeedMixin,
    VaccineSeedMixin,
    AllergySeedMixin,
    ConceptSeedMixin,
    CatalogSeedMixin,
    SystemSeedMixin,
):
    _SEED_STAGE_NAMES: ClassVar[list[str]] = [
        "retention_policies",  # FIRST (plan 6): the retention windows every
        #   lifecycle job reads. Seeded defaults; insert-missing only. App-only
        #   stage (code/contract vocabulary — never a pack item).
        "proposal_kinds",  # FIRST (plan 5): the HITL proposal-kind vocabulary
        #   every proposals.kind_id FKs into. Seeded from the family contract —
        #   data, not DDL. App-only stage.
        "ai_task_defs",  # FIRST (plan 4): the AI task vocabulary every
        #   ai_task_assignments / ai_call_logs row FKs into. Seeded from the
        #   one TaskDef registry — data, not DDL. App-only stage.
        "concepts",  # FIRST — anatomy_class/biomarker_class/… are referenced by
        #   body_parts (class_concept_slug→id), default_catalog (biomarker_class)
        #   and concept_edges. Must run before any stage that resolves a concept.
        "diseases",  # after concepts (disease concepts — kind=disease, ICD-10)
        "medications",  # standalone
        "vaccines",  # standalone (Phase 5; CVX-coded)
        "clinical_event_types",  # standalone
        "allergies",  # standalone
        "body_parts",  # after concepts (resolves anatomy_class)
        "anatomy_figures",  # after body_parts (binary figures — bundled-only,
        #   copied from data/seeds/anatomy_figures/, never a pack item in v1)
        "biomarker_states",  # standalone — universal state catalog (no deps)
        "default_catalog",  # after concepts (biomarker_class)
        "concept_edges",  # after concepts + diseases + body_parts + vaccines
        #   + default_catalog — the file's biomarker endpoints resolve against
        #   biomarker_definitions, so the default catalog must exist first.
        "biomarker_panels",  # after concepts + default_catalog
    ]

    _ITEM_STAGES: ClassVar[frozenset[str]] = frozenset(
        {
            "concepts",
            "diseases",
            "medications",
            "vaccines",
            "clinical_event_types",
            "allergies",
            "body_parts",
            "biomarker_states",
            "concept_edges",
            "biomarker_panels",
        }
    )
    """Stages whose seeder takes the parsed pack's entity list."""

    _DATA_STAGES: ClassVar[frozenset[str]] = frozenset({"default_catalog"})
    """Stages whose seeder takes the parsed pack's whole collections mapping."""

    def __init__(self, tenant_id=None):
        self.seeds_dir = Path(__file__).parent.parent.parent.parent / "data" / "seeds"
        self.tenant_id = tenant_id
        """The tenant overlay lane: ``None`` seeds system rows; a tenant id
        seeds that tenant's overlay rows (ADR 0056 tenant packs — set by the
        applier from the source row's tenancy; the bundled boot and the CLIs
        stay ``None``)."""
        self.item_errors: list[dict[str, str | None]] = []
        """Per-item failures of the last stage run (run-item attribution)."""
        self.unresolved_refs: list[dict[str, Any]] = []
        """Per-item unresolved references of the last stage run (the
        applier persists them as ``seed_deferred_refs`` rows)."""

    def note_item_error(self, stage: str, item: Any, exc: BaseException) -> None:
        """Record one item's failure for the applier's run items (alongside
        the existing log + stats['errors'] contract)."""
        self.item_errors.append(
            {
                "stage": stage,
                "seed_key": item.get("seed_key") if isinstance(item, dict) else None,
                "error": str(exc),
            }
        )

    def note_unresolved_ref(
        self, stage: str, item: Any, ref: str, *, key: str | None = None
    ) -> None:
        """Record one unresolved cross-item reference; the applier turns it
        into a ``seed_deferred_refs`` row (retried on every new approval).
        ``key`` overrides the tracking key for items without a ``seed_key``
        (edges use their deterministic identity string)."""
        self.unresolved_refs.append(
            {
                "stage": stage,
                "seed_key": key or (item.get("seed_key") if isinstance(item, dict) else None),
                "item": item,
                "ref": ref,
            }
        )

    def _load_seed_json(self, filename: str) -> Any:
        """Load a seed JSON file, returning None + logging on any failure.

        Retained for tooling and non-pack side inputs; the JSON item path
        flows through the bundled pack instead.
        """
        file_path = self.seeds_dir / filename
        if not file_path.exists():
            logger.warning(f"Seed file not found: {file_path}")
            return None
        try:
            with open(file_path) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load {file_path}: {e}")
            return None

    async def _table_exists(self, session: AsyncSession, table_name: str) -> bool:
        def check(sync_session):
            from sqlalchemy import inspect

            # Get connection from session
            conn = sync_session.connection()
            ins = inspect(conn)
            return ins.has_table(table_name)

        return await session.run_sync(check)

    async def seed_all(
        self,
        session: AsyncSession,
        pack: Any | None = None,
    ) -> dict[str, dict[str, int]]:
        """Run every seed stage in declared dependency order.

        Parses the bundled pack once (validated manifest + hashed item files)
        and dispatches each stage's parsed items to its seeder; app-only
        stages (retention policies, proposal kinds, AI task defs) seed from
        their registries, and the binary figure stage reads its side inputs
        from the seeds directory. ``pack`` accepts a pre-parsed
        :class:`~app.core.seed_pack.ParsedPack` (the Phase-9B applier's
        non-bundled packs); the caller owns the session and the transaction —
        nothing here commits.

        Returns ``{stage_name: stats}``. Order is explicit in
        :attr:`_SEED_STAGE_NAMES` so dependencies (e.g. ``biomarker_class``
        concepts must exist before the default catalog; ``body_parts`` before
        ``concept_edges`` that resolve anatomy slugs) are reviewable in one
        place rather than scattered across call sites.
        """
        if pack is None:
            pack = load_bundled_pack(self.seeds_dir)
        out: dict[str, dict[str, int]] = {}
        for name in self._SEED_STAGE_NAMES:
            logger.info("Seeding %s...", name)
            fn = getattr(self, f"seed_{name}")
            if name in self._ITEM_STAGES:
                out[name] = await fn(session, pack.stage_items(name))
            elif name in self._DATA_STAGES:
                out[name] = await fn(session, pack.stage_data(name))
            else:
                out[name] = await fn(session)
            logger.info("Seeded %s: %s", name, out[name])
        return out
