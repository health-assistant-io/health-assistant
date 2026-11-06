"""The SeedService composition root — one seeding face over the entity
families. _SEED_STAGE_NAMES and seed_all stay here; the family seeders
live in the sibling modules (methods moved verbatim, mixin composition
keeps the public class face)."""

import json
import logging
from pathlib import Path
from typing import Any, ClassVar

from sqlalchemy.ext.asyncio import AsyncSession

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
        #   lifecycle job reads. Seeded defaults; insert-missing only.
        "proposal_kinds",  # FIRST (plan 5): the HITL proposal-kind vocabulary
        #   every proposals.kind_id FKs into. Seeded from the family contract —
        #   data, not DDL.
        "ai_task_defs",  # FIRST (plan 4): the AI task vocabulary every
        #   ai_task_assignments / ai_call_logs row FKs into. Seeded from the
        #   one TaskDef registry — data, not DDL.
        "concepts",  # FIRST — anatomy_class/biomarker_class/… are referenced by
        #   body_parts (class_concept_slug→id), default_catalog (biomarker_class)
        #   and concept_edges. Must run before any stage that resolves a concept.
        "diseases",  # after concepts (disease concepts — kind=disease, ICD-10)
        "medications",  # standalone
        "vaccines",  # standalone (Phase 5; CVX-coded)
        "clinical_event_types",  # standalone
        "allergies",  # standalone
        "body_parts",  # after concepts (resolves anatomy_class)
        "anatomy_figures",  # after body_parts
        "concept_edges",  # after concepts + diseases + body_parts + vaccines
        "biomarker_states",  # standalone — universal state catalog (no deps)
        "default_catalog",  # after concepts (biomarker_class)
        "biomarker_panels",  # after concepts + default_catalog
    ]

    def __init__(self):
        self.seeds_dir = Path(__file__).parent.parent.parent.parent / "data" / "seeds"

    def _load_seed_json(self, filename: str) -> Any:
        """Load a seed JSON file, returning None + logging on any failure."""
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

    async def seed_all(self) -> dict[str, dict[str, int]]:
        """Run every seed stage in declared dependency order.

        Returns ``{stage_name: stats}``. Order is explicit in
        :attr:`_SEED_STAGE_NAMES` so dependencies (e.g. ``biomarker_class``
        concepts must exist before the default catalog; ``body_parts`` before
        ``concept_edges`` that resolve anatomy slugs) are reviewable in one
        place rather than scattered across call sites.
        """
        out: dict[str, dict[str, int]] = {}
        for name in self._SEED_STAGE_NAMES:
            logger.info("Seeding %s...", name)
            fn = getattr(self, f"seed_{name}")
            out[name] = await fn()
            logger.info("Seeded %s: %s", name, out[name])
        return out
