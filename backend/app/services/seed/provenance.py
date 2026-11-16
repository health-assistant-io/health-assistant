"""Seed-item provenance helpers — the ``user_modified`` hook and the
provenance upsert the applier writes (ADR 0056 D3/D5).

``seed_item_provenance`` is the one row per seeded catalog item:
``(entity_type, entity_id)`` unique, naming the source version that last
wrote it, its ``seed_key``, the applied ``content_hash``, the lifecycle
``state`` and ``user_modified``.

* :func:`entity_type_for` — the stable entity-type label for a model (the
  same labels the planner and the seeders use).
* :func:`mark_user_modified` — the service-layer hook every **human** write
  path calls (generic/domain catalog writes, anatomy import, atlas marker
  PATCH, HITL creates). It flips ``user_modified`` on the provenance row —
  creating one when a user-created item has no pack provenance yet — so the
  planner never auto-updates the row again ("update available" instead,
  ADR 0056 assumption 5). Best-effort: a provenance failure never fails the
  user's write. Seed/applier writes never call this — they leave the flag
  false.
* :func:`upsert_provenance` — the applier's write: point the row at the
  applying version, refresh the content hash, and flip the state (active on
  apply; retirement/restore are the caller's decision).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anatomy_model import AnatomyStructure
from app.models.biomarker_model import BiomarkerDefinition, BiomarkerState, Unit
from app.models.clinical_event import ClinicalEventType
from app.models.concept_model import Concept, ConceptEdge
from app.models.enums import ConceptStatus, SeedItemState
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.vaccine import VaccineCatalog
from app.models.seed_source_model import SeedItemProvenance

logger = logging.getLogger(__name__)

ENTITY_TYPES: dict[type, str] = {
    Concept: "concept",
    MedicationCatalog: "medication",
    VaccineCatalog: "vaccine",
    AllergyCatalog: "allergy",
    ClinicalEventType: "clinical-event-type",
    AnatomyStructure: "anatomy",
    BiomarkerDefinition: "biomarker",
    BiomarkerState: "biomarker-state",
    Unit: "unit",
    ConceptEdge: "concept-edge",
}
"""Model → provenance ``entity_type`` label (one vocabulary everywhere)."""

ENTITY_TYPE_MODELS: dict[str, type] = {label: model for model, label in ENTITY_TYPES.items()}

RESTORABLE_STATUS_MODELS: dict[str, Any] = {"concept": ConceptStatus}
"""entity_type → the status enum a retired row restores to ``ACTIVE``.

Only models with a status vocabulary carry a row-level retirement marker;
every other entity retires in the provenance lane alone (the row survives —
retire, never delete, ADR 0056 D5).
"""


def entity_type_for(model: type) -> str:
    """The provenance entity-type label for a catalog model (KeyError = bug)."""
    return ENTITY_TYPES[model]


async def get_provenance(
    session: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> SeedItemProvenance | None:
    """The provenance row for one entity, or ``None``."""
    return (
        await session.execute(
            select(SeedItemProvenance).where(
                SeedItemProvenance.entity_type == entity_type,
                SeedItemProvenance.entity_id == entity_id,
            )
        )
    ).scalar_one_or_none()


async def mark_user_modified(
    session: AsyncSession, entity_type: str, entity_id: uuid.UUID | Any
) -> None:
    """Flip ``user_modified`` on an item's provenance row (the human-write hook).

    Creates a provenance row when a user-created item has none yet
    (``source_version_id`` NULL, state ``active``) so the freeze survives the
    first pack that later targets the same key. Best-effort by contract: a
    provenance failure logs and returns — it must never fail the user's
    write, and its lookup runs under ``no_autoflush`` so it can never flush
    the caller's pending write ahead of the guarded commit (the write path's
    typed constraint translation stays intact). Nothing commits here; the
    flag lands in the caller's transaction. Seed/applier writes never call
    this — they leave the flag false.
    """
    try:
        entity_id = entity_id if isinstance(entity_id, uuid.UUID) else uuid.UUID(str(entity_id))
        with session.no_autoflush:
            row = await get_provenance(session, entity_type, entity_id)
        if row is None:
            session.add(
                SeedItemProvenance(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    state=SeedItemState.ACTIVE,
                    user_modified=True,
                )
            )
        else:
            row.user_modified = True
    except Exception:
        logger.warning(
            "seed provenance user_modified flag failed (type=%s id=%s)",
            entity_type,
            entity_id,
            exc_info=True,
        )


async def mark_user_modified_for_model(session: AsyncSession, model: type, entity_id: Any) -> None:
    """:func:`mark_user_modified` with the model → entity-type mapping."""
    await mark_user_modified(session, entity_type_for(model), entity_id)


async def upsert_provenance(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    seed_key: str | None,
    source_version_id: uuid.UUID,
    content_hash: str | None,
    state: SeedItemState = SeedItemState.ACTIVE,
) -> SeedItemProvenance:
    """Point an item's provenance at the applying version (the applier's write).

    ``user_modified`` is never written here — a frozen row keeps its flag
    (the planner refuses to auto-update it in the first place); ``applied_at``
    moves only when the version/hash actually changed.
    """
    row = await get_provenance(session, entity_type, entity_id)
    if row is None:
        row = SeedItemProvenance(
            entity_type=entity_type,
            entity_id=entity_id,
            seed_key=seed_key,
            source_version_id=source_version_id,
            content_hash=content_hash,
            state=state,
            user_modified=False,
        )
        session.add(row)
        return row
    row.source_version_id = source_version_id
    row.seed_key = seed_key if seed_key is not None else row.seed_key
    row.content_hash = content_hash if content_hash is not None else row.content_hash
    row.state = state
    return row
