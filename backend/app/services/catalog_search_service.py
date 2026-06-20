"""Catalog search service.

Unified, tenant-scoped, typo-tolerant search across the platform's catalog
tables: medications, biomarkers, allergies, clinical event types/categories.

Uses PostgreSQL `pg_trgm` trigram similarity (indexed via GIN `gin_trgm_ops`)
as the primary matching strategy, with `ilike` fallback on secondary text
columns for substring containment that trigram similarity would miss.

Design:
- All functions take an AsyncSession + tenant_id and apply uniform tenant
  scoping: `or_(tenant_id == tid, tenant_id.is_(None))`.
- The trigram similarity threshold (`set_limit`) is tuned per-session down to
  0.2 (default 0.3 is too strict for short names like "Lipitor").
- `name` is always ranked via `similarity(name, q) DESC` so the closest lexical
  match surfaces first.
- Empty / too-short queries (<2 chars) return [] rather than the whole table.
- These functions return ORM rows so callers can `to_dict()` as needed; the
  chatbot tools build their own compact summaries.

Future phasing:
- Phase 2 will add Postgres tsvector FTS (`websearch_to_tsquery`) for
  multi-word / "find by symptom" queries; the service will gain a `mode` arg.
- Phase 3 (pgvector) will add an optional `semantic=True` branch with
  reciprocal rank fusion; see project plan.
"""
from typing import List, Optional, Type, Any
from uuid import UUID

from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fhir.medication import MedicationCatalog
from app.models.fhir.allergy import AllergyCatalog
from app.models.biomarker_model import BiomarkerDefinition
from app.models.clinical_event import (
    ClinicalEventType,
    ClinicalEventCategory,
)


DEFAULT_LIMIT = 20
DEFAULT_THRESHOLD = 0.2  # trigram similarity minimum; default pg_trgm is 0.3
MIN_QUERY_LEN = 2


async def _set_similarity_threshold(db: AsyncSession, threshold: float) -> None:
    """Lower the per-session trigram similarity threshold.

    We use the SET command directly rather than func.set_limit() to avoid
    double-precision casting issues in asyncpg.
    """
    from sqlalchemy import text
    await db.execute(text(f"SET pg_trgm.similarity_threshold = {threshold}"))


def _normalize(query: Optional[str]) -> Optional[str]:
    if not query:
        return None
    q = query.strip()
    if len(q) < MIN_QUERY_LEN:
        return None
    return q


# ---------------------------------------------------------------------------
# Medications
# ---------------------------------------------------------------------------

async def search_medications(
    db: AsyncSession,
    tenant_id: UUID,
    query: Optional[str],
    limit: int = DEFAULT_LIMIT,
    threshold: float = DEFAULT_THRESHOLD,
) -> List[MedicationCatalog]:
    """Tenant-scoped fuzzy + substring search over the medication catalog.

    Ranking: trigram similarity on `name` first (handles typos like
    "metfromin"), with `ilike` fallback on `indications` / `description` for
    "find by symptom" queries that trigram won't catch (e.g. "headache").
    """
    q = _normalize(query)
    base = select(MedicationCatalog).where(
        or_(
            MedicationCatalog.tenant_id.is_(None),
            MedicationCatalog.tenant_id == tenant_id,
        )
    )
    if q is None:
        result = await db.execute(
            base.order_by(MedicationCatalog.name.asc()).limit(limit)
        )
        return list(result.scalars().all())

    await _set_similarity_threshold(db, threshold)

    stmt = base.where(
        or_(
            MedicationCatalog.name.op("%")(q),  # trigram similarity (uses GIN)
            MedicationCatalog.indications.op("%")(q),
            MedicationCatalog.name.ilike(f"%{q}%"),
            MedicationCatalog.indications.ilike(f"%{q}%"),
            MedicationCatalog.description.ilike(f"%{q}%"),
        )
    ).order_by(
        func.similarity(MedicationCatalog.name, q).desc(),
        MedicationCatalog.name.asc(),
    ).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Biomarkers
# ---------------------------------------------------------------------------

async def search_biomarkers(
    db: AsyncSession,
    tenant_id: UUID,
    query: Optional[str],
    limit: int = DEFAULT_LIMIT,
    threshold: float = DEFAULT_THRESHOLD,
) -> List[BiomarkerDefinition]:
    """Tenant-scoped fuzzy + substring search over biomarker definitions.

    Replaces the legacy `search_available_biomarkers` chatbot tool that used
    unindexed POSIX regex (~*) with no tenant scoping.
    """
    q = _normalize(query)
    base = select(BiomarkerDefinition).where(
        or_(
            BiomarkerDefinition.tenant_id.is_(None),
            BiomarkerDefinition.tenant_id == tenant_id,
        )
    )
    if q is None:
        result = await db.execute(base.limit(limit))
        return list(result.scalars().all())

    await _set_similarity_threshold(db, threshold)

    stmt = base.where(
        or_(
            BiomarkerDefinition.name.op("%")(q),
            BiomarkerDefinition.slug.op("%")(q),
            BiomarkerDefinition.name.ilike(f"%{q}%"),
            BiomarkerDefinition.slug.ilike(f"%{q}%"),
            BiomarkerDefinition.code.ilike(f"%{q}%"),
        )
    ).order_by(
        func.similarity(BiomarkerDefinition.name, q).desc(),
        BiomarkerDefinition.slug.asc(),
    ).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Allergies
# ---------------------------------------------------------------------------

async def search_allergies(
    db: AsyncSession,
    tenant_id: UUID,
    query: Optional[str],
    limit: int = DEFAULT_LIMIT,
    threshold: float = DEFAULT_THRESHOLD,
) -> List[AllergyCatalog]:
    """Tenant-scoped fuzzy + substring search over the allergy catalog."""
    q = _normalize(query)
    base = select(AllergyCatalog).where(
        or_(
            AllergyCatalog.tenant_id.is_(None),
            AllergyCatalog.tenant_id == tenant_id,
        )
    )
    if q is None:
        result = await db.execute(base.order_by(AllergyCatalog.name.asc()).limit(limit))
        return list(result.scalars().all())

    await _set_similarity_threshold(db, threshold)

    stmt = base.where(
        or_(
            AllergyCatalog.name.op("%")(q),
            AllergyCatalog.name.ilike(f"%{q}%"),
        )
    ).order_by(
        func.similarity(AllergyCatalog.name, q).desc(),
        AllergyCatalog.name.asc(),
    ).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Clinical event types & categories
# ---------------------------------------------------------------------------

async def search_clinical_event_types(
    db: AsyncSession,
    tenant_id: UUID,
    query: Optional[str],
    limit: int = DEFAULT_LIMIT,
    threshold: float = DEFAULT_THRESHOLD,
) -> List[ClinicalEventType]:
    """Tenant-scoped fuzzy + substring search over clinical event types."""
    q = _normalize(query)
    base = select(ClinicalEventType).where(
        or_(
            ClinicalEventType.tenant_id.is_(None),
            ClinicalEventType.tenant_id == tenant_id,
        )
    )
    if q is None:
        result = await db.execute(base.order_by(ClinicalEventType.name.asc()).limit(limit))
        return list(result.scalars().all())

    await _set_similarity_threshold(db, threshold)

    stmt = base.where(
        or_(
            ClinicalEventType.name.op("%")(q),
            ClinicalEventType.slug.op("%")(q),
            ClinicalEventType.name.ilike(f"%{q}%"),
            ClinicalEventType.slug.ilike(f"%{q}%"),
        )
    ).order_by(
        func.similarity(ClinicalEventType.name, q).desc(),
        ClinicalEventType.slug.asc(),
    ).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def search_clinical_event_categories(
    db: AsyncSession,
    tenant_id: UUID,
    query: Optional[str],
    limit: int = DEFAULT_LIMIT,
    threshold: float = DEFAULT_THRESHOLD,
) -> List[ClinicalEventCategory]:
    """Tenant-scoped fuzzy + substring search over clinical event categories."""
    q = _normalize(query)
    base = select(ClinicalEventCategory).where(
        or_(
            ClinicalEventCategory.tenant_id.is_(None),
            ClinicalEventCategory.tenant_id == tenant_id,
        )
    )
    if q is None:
        result = await db.execute(base.order_by(ClinicalEventCategory.name.asc()).limit(limit))
        return list(result.scalars().all())

    await _set_similarity_threshold(db, threshold)

    stmt = base.where(
        or_(
            ClinicalEventCategory.name.op("%")(q),
            ClinicalEventCategory.slug.op("%")(q),
            ClinicalEventCategory.name.ilike(f"%{q}%"),
            ClinicalEventCategory.slug.ilike(f"%{q}%"),
        )
    ).order_by(
        func.similarity(ClinicalEventCategory.name, q).desc(),
        ClinicalEventCategory.slug.asc(),
    ).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())
