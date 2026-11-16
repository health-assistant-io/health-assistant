"""Deterministic hard reseed of the curated system catalogs (ADR 0055).

``reset_curated_system_rows`` deletes the curated **system** rows of the
seeded catalogs (``tenant_id IS NULL``; seed-provenance edges only) and then
runs the normal ``SeedService.seed_all()`` pipeline, which recreates every
row under its deterministic ``uuid5`` identity.

Boundary: tenant rows are never **deleted** by the reset itself — but the
FKs from live data into the deleted system rows are ``SET NULL`` or
``CASCADE``. Deleting a referenced system row would silently null user
links (a medication statement's catalog link, an observation's unit or
biomarker link, a tenant biomarker's preferred unit, a tenant concept's
parent) or destroy tenant rows outright (anatomy link rows cascade, a
tenant notification rule on a system biomarker). The preflight therefore
**refuses** to run while any row that would *survive* the reset still
references a deleted one. The two RESTRICT shapes are counted explicitly
(tenant event types on system category concepts, tenant biomarker
allowed-state rows on global states); every ``ON DELETE SET NULL`` /
``CASCADE`` FK into a truncated table is discovered generically from the
database catalog — covering the tenant shapes too (a tenant biomarker
pinning a curated unit via ``preferred_unit_id``, a tenant reference range
pinning one via ``unit_id``, notification rules on system biomarkers,
instance rows linked to catalog rows, tenant children of system concepts)
and any catalog FK added later, with no change here. Rows that cascade
away *with* their truncated parent and are recreated by the seed
(``concept_kind_tags``, ``biomarker_reference_ranges``,
``biomarker_allowed_states``) are catalog churn, not data loss, and don't
block. It is safe only on installs without such references (no users, no
tenant data).

Idempotent — re-running converges (the second run re-deletes and re-seeds
the same deterministic ids).
"""

import logging
import re
from typing import Any

from sqlalchemy import bindparam, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.enums import ConceptProvenance

logger = logging.getLogger(__name__)


class CuratedResetBlocked(RuntimeError):
    """Live rows still reference curated system rows the reset would delete.

    ``reset_curated_system_rows`` never deletes tenant rows, but the FKs from
    tenant/user rows into system rows are ``SET NULL`` or ``CASCADE`` —
    deleting a referenced system row would silently null or destroy those
    references (a tenant biomarker pinning a curated unit, an instance
    medication statement linked to a system catalog row, a tenant
    notification rule on a system biomarker, a tenant concept child of a
    system parent). The error names the blocking counts. This reset is an
    operator action for installs without such references.
    """


async def _tenant_category_blockers(session: AsyncSession) -> int:
    """Tenant event types pinning a system ``event_category`` concept."""
    from app.models.clinical_event import ClinicalEventType
    from app.models.concept_model import Concept

    return (
        await session.execute(
            select(func.count())
            .select_from(ClinicalEventType)
            .where(
                ClinicalEventType.tenant_id.is_not(None),
                ClinicalEventType.category_concept_id.in_(
                    select(Concept.id).where(Concept.tenant_id.is_(None))
                ),
            )
        )
    ).scalar_one()


async def _tenant_state_blockers(session: AsyncSession) -> int:
    """Tenant biomarkers pinning a global ``biomarker_states`` row."""
    from app.models.biomarker_model import BiomarkerAllowedState, BiomarkerDefinition

    return (
        await session.execute(
            select(func.count())
            .select_from(BiomarkerAllowedState)
            .where(
                BiomarkerAllowedState.biomarker_id.in_(
                    select(BiomarkerDefinition.id).where(BiomarkerDefinition.tenant_id.is_not(None))
                )
            )
        )
    ).scalar_one()


# The tables the reset truncates, mapped to the SQL predicate selecting the
# rows each delete removes (``None`` = every row goes, so none of its rows
# survive to dangle — e.g. the ``units`` self-reference ``base_unit_id``).
# Mirrors the delete sequence in ``reset_curated_system_rows``.
_TRUNCATED_TABLES: dict[str, str | None] = {
    "clinical_event_types": "tenant_id IS NULL",
    "concept_edges": "tenant_id IS NULL AND source = 'seed'",
    "medication_catalog": "tenant_id IS NULL",
    "allergy_catalog": "tenant_id IS NULL",
    "vaccine_catalog": "tenant_id IS NULL",
    "biomarker_definitions": "tenant_id IS NULL",
    "anatomy_structures": "tenant_id IS NULL",
    "concepts": "tenant_id IS NULL",
    "units": None,
    "biomarker_states": None,
}

# Curated child tables whose rows cascade away with their truncated parent
# and are recreated by the seed pipeline: a dangling reference among them is
# catalog churn, not data loss. (Contrast ``event_anatomy_links``, which
# attaches live clinical events to catalog rows — cascade-destroyed links
# are data loss and stay blockers.)
_SEED_RECREATED_CHILD_TABLES = frozenset(
    {"concept_kind_tags", "biomarker_reference_ranges", "biomarker_allowed_states"}
)

# Every FK whose referenced table is truncated, with its delete action:
# 'c' = CASCADE, 'n' = SET NULL. RESTRICT FKs ('r') fail the delete loudly
# and are counted by the dedicated RESTRICT helpers instead.
_FK_INVENTORY_SQL = text(
    """
    SELECT con.conname AS constraint_name,
           src.relname AS src_table,
           dst.relname AS dst_table,
           con.confdeltype::text AS on_delete,
           array_agg(src_att.attname ORDER BY cols.ord) AS src_cols,
           array_agg(dst_att.attname ORDER BY cols.ord) AS dst_cols
    FROM pg_constraint AS con
    JOIN pg_class AS src ON src.oid = con.conrelid
    JOIN pg_namespace AS src_ns ON src_ns.oid = src.relnamespace
    JOIN pg_class AS dst ON dst.oid = con.confrelid
    JOIN pg_namespace AS dst_ns ON dst_ns.oid = dst.relnamespace
    JOIN unnest(con.conkey, con.confkey) WITH ORDINALITY
         AS cols(src_attnum, dst_attnum, ord) ON TRUE
    JOIN pg_attribute AS src_att
         ON src_att.attrelid = src.oid AND src_att.attnum = cols.src_attnum
    JOIN pg_attribute AS dst_att
         ON dst_att.attrelid = dst.oid AND dst_att.attnum = cols.dst_attnum
    WHERE con.contype = 'f'
      AND con.confdeltype IN ('c', 'n')
      AND src_ns.nspname = 'public'
      AND dst_ns.nspname = 'public'
      AND dst.relname IN :truncated
    GROUP BY con.oid, con.conname, src.relname, dst.relname, con.confdeltype
    """
).bindparams(bindparam("truncated", expanding=True))


def _quote(ident: str) -> str:
    """Quote an identifier only when it isn't a plain lowercase one, so the
    blocker labels stay readable (``concepts.parent_id``, not quoted)."""
    if re.fullmatch(r"[a-z_][a-z0-9_]*", ident):
        return ident
    return '"' + ident.replace('"', '""') + '"'


def _cols(cols) -> str:
    return ", ".join(_quote(c) for c in cols)


def _refs_deleted_row(src_cols, dst_table: str, dst_cols, dst_pred: str | None) -> str:
    """SQL predicate: the FK columns point at a row the reset deletes."""
    sub = f"SELECT {_cols(dst_cols)} FROM {_quote(dst_table)}"
    if dst_pred:
        sub += f" WHERE {dst_pred}"
    return f"({_cols(src_cols)}) IN ({sub})"


async def _catalog_fk_blockers(session: AsyncSession) -> dict[str, int]:
    """Rows that survive the reset yet reference a deleted catalog row.

    One count per ``ON DELETE SET NULL``/``CASCADE`` FK into a truncated
    table, restricted to referencing rows the reset does not delete itself:
    rows of a truncated referencing table are filtered by that table's own
    delete predicate, and seed-recreated child rows are filtered by their
    (surviving) parent. Returns ``{"table.cols→table (action)": count}``.
    """
    inventory = (
        (await session.execute(_FK_INVENTORY_SQL, {"truncated": sorted(_TRUNCATED_TABLES)}))
        .mappings()
        .all()
    )

    cascade_parents: dict[str, list] = {}
    for fk in inventory:
        if fk["on_delete"] == "c":
            cascade_parents.setdefault(fk["src_table"], []).append(fk)

    blockers: dict[str, int] = {}
    for fk in inventory:
        src_table = fk["src_table"]
        conditions: list[str] = []
        if src_table in _TRUNCATED_TABLES:
            src_pred = _TRUNCATED_TABLES[src_table]
            if src_pred is None:
                # The reset deletes every row of the referencing table too —
                # nothing survives to dangle (units.base_unit_id).
                continue
            conditions.append(f"NOT ({src_pred})")
        conditions.append(
            _refs_deleted_row(
                fk["src_cols"],
                fk["dst_table"],
                fk["dst_cols"],
                _TRUNCATED_TABLES[fk["dst_table"]],
            )
        )
        if src_table in _SEED_RECREATED_CHILD_TABLES:
            for parent_fk in cascade_parents.get(src_table, ()):
                conditions.append(
                    "NOT "
                    + _refs_deleted_row(
                        parent_fk["src_cols"],
                        parent_fk["dst_table"],
                        parent_fk["dst_cols"],
                        _TRUNCATED_TABLES[parent_fk["dst_table"]],
                    )
                )
        count = (
            await session.execute(
                text(f"SELECT count(*) FROM {_quote(src_table)} WHERE {' AND '.join(conditions)}")
            )
        ).scalar_one()
        if count:
            action = "CASCADE" if fk["on_delete"] == "c" else "SET NULL"
            blockers[f"{src_table}.{_cols(fk['src_cols'])}→{fk['dst_table']} ({action})"] = count
    return blockers


async def reset_curated_system_rows(session: AsyncSession | None = None) -> dict[str, Any]:
    """Delete curated system rows for the seeded catalogs, then reseed.

    Deletion is scoped to system rows only: ``tenant_id IS NULL`` on every
    catalog table (``units`` and ``biomarker_states`` are global tables with
    no tenant column), and for ``concept_edges`` additionally
    ``source = 'seed'`` so human-curated system edges survive. The seed
    pipeline then recreates the deleted rows under their deterministic ids.

    Order matters: ``clinical_event_types`` deletes before ``concepts``
    (RESTRICT FK on ``category_concept_id``), and ``biomarker_definitions``
    before ``biomarker_states`` (the allowed-states join rows cascade with
    their biomarker and RESTRICT the states).

    Returns ``{"deleted": {table: count}, "seeded": {stage: stats}}``.

    Raises:
        CuratedResetBlocked: before deleting anything, when a row that would
            survive the reset still references a deleted one — RESTRICT FKs
            are counted explicitly, and every ``SET NULL``/``CASCADE`` FK
            into a truncated table is discovered generically from the
            database catalog (see the module docstring for the boundary).
    """
    from app.models.anatomy_model import AnatomyStructure
    from app.models.biomarker_model import BiomarkerDefinition, BiomarkerState, Unit
    from app.models.clinical_event import ClinicalEventType
    from app.models.concept_model import Concept, ConceptEdge
    from app.models.fhir.allergy import AllergyCatalog
    from app.models.fhir.medication import MedicationCatalog
    from app.models.fhir.vaccine import VaccineCatalog
    from app.services.seed.service import SeedService

    async def _count(result) -> int:
        return result.rowcount or 0

    own_session = session is None
    if own_session:
        session = AsyncSessionLocal()

    try:
        # Refuse before deleting anything when a row that would survive the
        # reset references a row the reset removes: RESTRICT FKs (tenant
        # event types on system category concepts, tenant biomarker
        # allowed-state rows on global states) fail the delete; SET NULL/
        # CASCADE FKs (instance rows linked to catalog rows, anatomy links,
        # tenant children, unit pins) would silently null or destroy the
        # referencing rows instead. Tenant rows are never deleted by this
        # reset, so their references must survive.
        category_blockers = await _tenant_category_blockers(session)
        state_blockers = await _tenant_state_blockers(session)
        reference_blockers = await _catalog_fk_blockers(session)
        if category_blockers or state_blockers or any(reference_blockers.values()):
            named = {key: count for key, count in reference_blockers.items() if count}
            raise CuratedResetBlocked(
                f"live rows still reference curated system rows "
                f"({category_blockers} tenant clinical event type(s) on system "
                f"category concepts, {state_blockers} allowed-state row(s) of "
                f"tenant biomarkers on global states, "
                f"{sum(reference_blockers.values())} surviving reference "
                f"row(s): {named}) — the hard reseed is only safe "
                f"on installs without tenant or instance references"
            )

        deleted: dict[str, int] = {}

        # RESTRICT-referencing children first, then everything else.
        deleted["clinical_event_types"] = await _count(
            await session.execute(
                delete(ClinicalEventType).where(ClinicalEventType.tenant_id.is_(None))
            )
        )
        deleted["concept_edges(seed)"] = await _count(
            await session.execute(
                delete(ConceptEdge).where(
                    ConceptEdge.tenant_id.is_(None),
                    ConceptEdge.source == ConceptProvenance.SEED,
                )
            )
        )
        for model, name in (
            (MedicationCatalog, "medication_catalog"),
            (AllergyCatalog, "allergy_catalog"),
            (VaccineCatalog, "vaccine_catalog"),
            (BiomarkerDefinition, "biomarker_definitions"),
            (AnatomyStructure, "anatomy_structures"),
            (Concept, "concepts"),
            (Unit, "units"),
            (BiomarkerState, "biomarker_states"),
        ):
            stmt = delete(model)
            if hasattr(model, "tenant_id"):
                stmt = stmt.where(model.tenant_id.is_(None))
            deleted[name] = await _count(await session.execute(stmt))
        await session.commit()
        logger.info("Hard reseed: deleted curated system rows: %s", deleted)

        # The seed pipeline runs in one transaction the reset owns (the
        # seeders never commit — ADR 0056); the deterministic ids converge.
        seeded = await SeedService().seed_all(session)
        await session.commit()
        return {"deleted": deleted, "seeded": seeded}
    finally:
        if own_session:
            await session.close()
