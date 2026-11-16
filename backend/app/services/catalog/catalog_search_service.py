"""Unified catalog search service — hybrid (trigram + FTS) with reciprocal rank fusion.

Single source of truth for catalog search across the platform. All AI tools,
REST endpoints, and domain services that need to "find a catalog entity by
name, alias, symptom, or description" call into :func:`search_catalogs` (or a
per-catalog thin wrapper).

Ranking architecture (hybrid + RRF)
-----------------------------------

For each catalog table, a single SQL query runs both matching strategies and
fuses them via Reciprocal Rank Fusion (RRF, k=60 — the standard constant):

1. **Trigram** (``pg_trgm`` ``%`` operator + ``similarity()``) — typo-tolerant
   lexical matching over the primary label surface (``name``/``slug``).
   Best for short identifiers: drug names ("Lipitor"), biomarker slugs
   ("hba1c"), LOINC codes. Uses the ``ix_<table>_trgm`` GIN index.

2. **FTS** (``websearch_to_tsquery`` + ``ts_rank_cd`` over a ``'simple'``
   tsvector) — multi-word / "find by concept" matching over the full text
   surface (``description``, ``indications``, ``info``, ``aliases``).
   Best for symptom-style queries: "headache", "high blood pressure".
   Uses the ``ix_<table>_fts`` GIN expression index.

3. **Substring fallback** (``ILIKE '%q%'``) — catches case-variant substrings
   that trigram similarity may miss for very short queries.

RRF combines the two ranked lists into one without needing to reconcile the
incompatible score scales (trigram is 0-1, ``ts_rank_cd`` is unbounded).
Each catalog is queried independently, then the dispatcher fuses GLOBALLY
across catalogs so the LLM sees the best N hits regardless of which catalog
they live in.

The result payload is enriched with:
- ``description`` / ``coding`` / ``kind`` / ``scope`` — context for the LLM
  to decide relevance without a follow-up ``get_*_details`` call.
- ``matched_on`` — list of fields that matched (``name``/``slug``/``alias``/
  ``description``/``indications``/...).
- ``snippet`` — ``ts_headline`` excerpt of the matching text.
- ``score`` — the RRF score (for transparency / debugging).

Indexes live in the migration lane: ``0024_catalog_cleanup`` builds them from
each catalog's declared search surface (:mod:`app.core.catalog_search_index`),
and this service derives its FTS expression from the very same surface — the
index and the query cannot drift. ``tests/test_catalog_search_index.py``
pins the surface snapshot and asserts the planner picks the index.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalog_search_index import CatalogSearchSurface
from app.models.biomarker_model import BiomarkerDefinition
from app.models.clinical_event import ClinicalEventType
from app.models.concept_model import Concept
from app.models.enums import ConceptKind, ConceptStatus
from app.models.fhir.allergy import AllergyCatalog
from app.models.fhir.medication import MedicationCatalog

DEFAULT_LIMIT = 20
DEFAULT_THRESHOLD = 0.2  # trigram similarity minimum; default pg_trgm is 0.3
MIN_QUERY_LEN = 2
RRF_K = 60  # standard reciprocal-rank-fusion constant


# ---------------------------------------------------------------------------
# Session / query normalisation helpers
# ---------------------------------------------------------------------------


async def _set_similarity_threshold(db: AsyncSession, threshold: float) -> None:
    """Lower the per-session trigram similarity threshold.

    PostgreSQL ``SET`` does not accept bind parameters, so the threshold
    is inlined into the SQL string. We validate the input is a finite
    float in ``[0, 1]`` before inlining — anything outside that range
    (or of the wrong type) falls back to the safe default.
    """
    safe_threshold = DEFAULT_THRESHOLD
    try:
        candidate = float(threshold)
    except (TypeError, ValueError):
        candidate = DEFAULT_THRESHOLD
    else:
        if 0.0 <= candidate <= 1.0:
            safe_threshold = candidate
    await db.execute(text(f"SET pg_trgm.similarity_threshold = {safe_threshold}"))


def _normalize(query: str | None) -> str | None:
    if not query:
        return None
    q = query.strip()
    if len(q) < MIN_QUERY_LEN:
        return None
    return q


# ---------------------------------------------------------------------------
# Per-catalog search specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CatalogSearchSpec:
    """Declarative spec for one catalog's hybrid search.

    Every field is derived from the catalog's
    :class:`~app.core.catalog_search_index.CatalogSearchSurface` (see
    :func:`_build_spec`): the FTS expression is the very string the migration
    indexed, so Postgres always recognises the index match.
    """

    # The registry type key ("biomarker", "medication", ...).
    type_name: str
    # ORM model class.
    model: type
    # Column used as the canonical label + primary trigram similarity target.
    label_column: str
    # Extra text columns searched via the ``%`` trigram operator. Must have
    # a trigram GIN index covering them.
    extra_trgm_columns: tuple[str, ...] = ()
    # Extra text columns searched via ``ILIKE '%q%'`` substring containment.
    extra_ilike_columns: tuple[str, ...] = ()
    # JSONB column (e.g. ``aliases``) searched via ``::text ILIKE``. Already
    # included in the FTS expression; this enables substring alias matches.
    alias_column: str | None = None
    # Text column from which to extract a ts_headline snippet (usually
    # ``description`` or ``info``).
    snippet_column: str | None = None
    # Raw SQL expression fed to ``to_tsvector('simple', <expr>)``. MUST match
    # the migration index expression verbatim.
    fts_expression: str = ""
    # Extra ``WHERE`` SQL (static, no user input). Used for status/deleted_at
    # filters on concepts.
    extra_where_sql: str = ""
    # Whether the model has soft-delete (deleted_at) — included automatically.
    soft_delete: bool = False

    def table_name(self) -> str:
        return self.model.__tablename__


# Search-only surfaces: taxonomy entities with a hybrid-search consumer but no
# catalog descriptor (no workspace type, no write contract). Everything else is
# declared on its descriptor — see :func:`catalog_search_surfaces`.
_SEARCH_ONLY_SURFACES: tuple[CatalogSearchSurface, ...] = (
    CatalogSearchSurface(
        catalog_type="clinical_event_type",
        table="clinical_event_types",
        fts_columns=("name", "slug", "description"),
        trigram_columns=("slug",),
    ),
)

# Lifecycle predicates the surface cannot express (a status vocabulary, not a
# searched column). Soft-deleted rows are filtered from ``soft_delete`` alone.
_LIFECYCLE_PREDICATES: dict[str, str] = {
    "concept": "AND t.status = 'active'",
}


def catalog_search_surfaces() -> tuple[CatalogSearchSurface, ...]:
    """Every indexed search surface: one per registered catalog, plus the
    search-only taxonomy surfaces."""
    from app.catalogs.registry import CatalogRegistry

    return (
        tuple(descriptor.search_surface for descriptor in CatalogRegistry.all())
        + _SEARCH_ONLY_SURFACES
    )


def _build_spec(
    surface: CatalogSearchSurface, model: type, *, soft_delete: bool
) -> _CatalogSearchSpec:
    """Derive one catalog's search spec from its surface.

    Match strategies follow from the surface: the label (first FTS column) is
    the trigram target, the extra trigram columns add per-column similarity,
    the remaining plain-text columns get the substring fallback, and the JSONB
    members are matched through their ``::text`` projection. The snippet column
    prefers the descriptive ``info`` text, else ``description``.
    """
    columns = surface.fts_columns
    label = surface.label_column
    json_columns = set(surface.json_columns)
    trigram_extras = tuple(column for column in surface.trigram_columns if column != label)
    ilike_columns = tuple(
        column
        for column in columns
        if column != label and column not in trigram_extras and column not in json_columns
    )
    alias_column = "aliases" if "aliases" in json_columns else None
    if "info" in columns:
        snippet_column = "info"
    elif "description" in columns:
        snippet_column = "description"
    else:
        snippet_column = None
    return _CatalogSearchSpec(
        type_name=surface.catalog_type,
        model=model,
        label_column=label,
        extra_trgm_columns=trigram_extras,
        extra_ilike_columns=ilike_columns,
        alias_column=alias_column,
        snippet_column=snippet_column,
        fts_expression=surface.fts_expression(),
        extra_where_sql=_LIFECYCLE_PREDICATES.get(surface.catalog_type, ""),
        soft_delete=soft_delete,
    )


def _specs_by_type() -> dict[str, _CatalogSearchSpec]:
    """Resolve each surface's ORM model and build its spec."""
    from app.catalogs.registry import CatalogRegistry
    from app.models.anatomy_model import AnatomyStructure
    from app.models.clinical_event import ClinicalEventType
    from app.models.fhir.vaccine import VaccineCatalog

    search_only_models = {
        "anatomy": (AnatomyStructure, False),
        "vaccine": (VaccineCatalog, False),
        "clinical_event_type": (ClinicalEventType, False),
    }
    out: dict[str, _CatalogSearchSpec] = {}
    for surface in catalog_search_surfaces():
        if surface.catalog_type in search_only_models:
            model, soft_delete = search_only_models[surface.catalog_type]
        else:
            descriptor = CatalogRegistry.get(surface.catalog_type)
            model = descriptor.model
            soft_delete = bool(getattr(descriptor.service, "soft_delete", False))
        out[surface.catalog_type] = _build_spec(surface, model, soft_delete=soft_delete)
    return out


# ---------------------------------------------------------------------------
# Hybrid SQL builder + executor
# ---------------------------------------------------------------------------


@dataclass
class _HybridHit:
    """One ranked row from a per-catalog hybrid search."""

    row_id: Any
    label: str
    score: float
    matched_on: list[str]
    snippet: str | None = None


def _build_hybrid_sql(spec: _CatalogSearchSpec) -> str:
    """Render the per-catalog hybrid SQL.

    Static parts (column/table names, the FTS expression) are inlined —
    they come from the dataclass definition, never from user input. Bind
    parameters (:q, :pattern, :tenant_id, :limit) carry every user value.
    """
    table = spec.table_name()
    label = spec.label_column
    fts = spec.fts_expression

    # Build the per-column match flags and OR clauses.
    # Always include the label column for trigram + ilike; aliases via ilike
    # on the ::text cast; FTS via the tsvector expression.
    match_flag_selects: list[str] = [
        f"CASE WHEN t.{label} % :q THEN 1 ELSE 0 END AS m_label_tri",
        f"CASE WHEN t.{label} ILIKE :pattern THEN 1 ELSE 0 END AS m_label_ilike",
    ]
    or_clauses: list[str] = [
        f"t.{label} % :q",
        f"t.{label} ILIKE :pattern",
    ]

    for _i, col in enumerate(spec.extra_trgm_columns):
        match_flag_selects.append(f"CASE WHEN t.{col} % :q THEN 1 ELSE 0 END AS m_tri_{col}")
        or_clauses.append(f"t.{col} % :q")
    for _i, col in enumerate(spec.extra_ilike_columns):
        match_flag_selects.append(
            f"CASE WHEN t.{col} ILIKE :pattern THEN 1 ELSE 0 END AS m_ilike_{col}"
        )
        or_clauses.append(f"t.{col} ILIKE :pattern")
    if spec.alias_column:
        ac = spec.alias_column
        match_flag_selects.append(
            f"CASE WHEN t.{ac}::text ILIKE :pattern THEN 1 ELSE 0 END AS m_alias"
        )
        or_clauses.append(f"t.{ac}::text ILIKE :pattern")
    # FTS — always included.
    match_flag_selects.append(
        "CASE WHEN to_tsvector('simple', " + fts + ") @@ (SELECT tsq FROM q_ts) "
        "THEN 1 ELSE 0 END AS m_fts"
    )
    or_clauses.append("to_tsvector('simple', " + fts + ") @@ (SELECT tsq FROM q_ts)")

    snippet_select = "NULL::text AS snippet"
    if spec.snippet_column:
        snippet_select = (
            "ts_headline('simple', coalesce(t."
            + spec.snippet_column
            + ", ''), (SELECT tsq FROM q_ts), "
            "'MaxWords=25, MinWords=5, StartSel=[, StopSel=]') AS snippet"
        )

    tenant_filter = "(t.tenant_id IS NULL OR t.tenant_id = :tenant_id)"
    extra_where = spec.extra_where_sql
    if spec.soft_delete:
        extra_where = f"{extra_where} AND t.deleted_at IS NULL"

    # Optional per-column match flags. A name-only surface (label column +
    # FTS, no extra columns) has none — emitting the join unconditionally
    # would leave a dangling comma and an invalid statement.
    extra_flag_names = [
        statement.split(" AS ")[-1]
        for statement in match_flag_selects
        if not statement.startswith("CASE WHEN t." + label)
        and not statement.startswith("CASE WHEN to_tsvector")
    ]
    extra_flags_sql = "".join(", r." + name for name in extra_flag_names)

    sql = f"""
    WITH q_ts AS (SELECT websearch_to_tsquery('simple', :q) AS tsq),
    matches AS (
        SELECT
            t.id AS row_id,
            t.{label} AS label,
            similarity(t.{label}, :q) AS tri_score,
            ts_rank_cd(to_tsvector('simple', {fts}), (SELECT tsq FROM q_ts)) AS fts_score,
            {snippet_select},
            {", ".join(match_flag_selects)}
        FROM {table} t
        WHERE {tenant_filter}
            {extra_where}
            AND ({" OR ".join(or_clauses)})
    ),
    ranked AS (
        SELECT m.*,
            RANK() OVER (ORDER BY m.tri_score DESC) AS tri_rank,
            RANK() OVER (ORDER BY m.fts_score DESC) AS fts_rank
        FROM matches m
    )
    SELECT
        r.row_id,
        r.label,
        r.snippet,
        r.m_label_tri, r.m_label_ilike, r.m_fts{extra_flags_sql},
        (CASE WHEN r.tri_score > 0 THEN 1.0 / ({RRF_K} + r.tri_rank) ELSE 0 END)
        + (CASE WHEN r.fts_score > 0 THEN 1.0 / ({RRF_K} + r.fts_rank) ELSE 0 END)
        AS rrf_score
    FROM ranked r
    ORDER BY rrf_score DESC, r.tri_score DESC, r.label ASC
    LIMIT :limit
    """
    return sql


_MATCHED_ON_LABEL_MAP = {
    "m_label_tri": "name",
    "m_label_ilike": "name",
}
_MATCHED_ON_EXTRA_TRI = "m_tri_"
_MATCHED_ON_EXTRA_ILIKE = "m_ilike_"
_MATCHED_ON_ALIAS = "alias"
_MATCHED_ON_FTS = "fts"


def _row_to_hit(row) -> _HybridHit:
    """Convert a raw SQL row to a :class:`_HybridHit`, computing matched_on."""
    matched_on: list[str] = []
    fts_matched = False
    alias_matched = False
    for key, val in row._mapping.items():
        if val != 1:
            continue
        if key == "m_label_tri" or key == "m_label_ilike":
            if "name" not in matched_on:
                matched_on.append("name")
        elif key == "m_fts":
            fts_matched = True
        elif key == "m_alias":
            alias_matched = True
        elif key.startswith(_MATCHED_ON_EXTRA_TRI):
            col = key[len(_MATCHED_ON_EXTRA_TRI) :]
            if col not in matched_on:
                matched_on.append(col)
        elif key.startswith(_MATCHED_ON_EXTRA_ILIKE):
            col = key[len(_MATCHED_ON_EXTRA_ILIKE) :]
            if col not in matched_on:
                matched_on.append(col)
    if alias_matched and "alias" not in matched_on:
        matched_on.append("alias")
    if fts_matched:
        # FTS hit means the query matched somewhere in the wider text surface
        # (description/indications/info/aliases). Flag it distinctly so the
        # LLM knows this was a "concept" match, not a lexical one.
        matched_on.append("text")
    return _HybridHit(
        row_id=row.row_id,
        label=row.label,
        score=float(row.rrf_score) if row.rrf_score is not None else 0.0,
        matched_on=matched_on or ["name"],
        snippet=row.snippet if hasattr(row, "snippet") else None,
    )


async def _hybrid_search_one(
    db: AsyncSession,
    spec: _CatalogSearchSpec,
    q: str,
    tenant_id: UUID | None,
    *,
    limit: int = DEFAULT_LIMIT,
    extra_where_sql: str = "",
    extra_params: dict | None = None,
) -> list[_HybridHit]:
    """Run the hybrid SQL for one catalog spec.

    Returns ranked :class:`_HybridHit` objects. ``extra_where_sql`` lets
    callers (e.g. concept kind filter) inject additional static predicates
    without forking the SQL template.
    """
    norm = _normalize(q)
    if norm is None:
        return []

    await _set_similarity_threshold(db, DEFAULT_THRESHOLD)

    # Allow caller-supplied extra static predicates (used for kind filter).
    effective_spec = spec
    if extra_where_sql:
        effective_spec = _CatalogSearchSpec(
            type_name=spec.type_name,
            model=spec.model,
            label_column=spec.label_column,
            extra_trgm_columns=spec.extra_trgm_columns,
            extra_ilike_columns=spec.extra_ilike_columns,
            alias_column=spec.alias_column,
            snippet_column=spec.snippet_column,
            fts_expression=spec.fts_expression,
            extra_where_sql=(spec.extra_where_sql + " " + extra_where_sql),
            soft_delete=spec.soft_delete,
        )

    sql = _build_hybrid_sql(effective_spec)
    params = {
        "q": norm,
        "pattern": f"%{norm}%",
        "tenant_id": tenant_id,
        "limit": limit,
    }
    if extra_params:
        params.update(extra_params)
    result = await db.execute(text(sql), params)
    return [_row_to_hit(row) for row in result.all()]


# ---------------------------------------------------------------------------
# ORM-row fetch in rank order (for the ORM-returning wrappers)
# ---------------------------------------------------------------------------


async def _fetch_orm_in_rank_order(
    db: AsyncSession,
    model: type,
    hits: list[_HybridHit],
) -> list:
    """Fetch ORM rows for the hit IDs, preserving the rank order of ``hits``."""
    if not hits:
        return []
    ids = [h.row_id for h in hits]
    result = await db.execute(select(model).where(model.id.in_(ids)))
    by_id = {row.id: row for row in result.scalars().all()}
    return [by_id[h.row_id] for h in hits if h.row_id in by_id]


# ---------------------------------------------------------------------------
# Public per-catalog search functions (backward-compatible signatures)
# ---------------------------------------------------------------------------


async def search_medications(
    db: AsyncSession,
    tenant_id: UUID,
    query: str | None,
    limit: int = DEFAULT_LIMIT,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[MedicationCatalog]:
    """Tenant-scoped hybrid search over the medication catalog.

    Returns ORM rows ranked by RRF over trigram + FTS. Ranking: trigram
    similarity on ``name`` (handles typos like "metfromin") + FTS over
    description/indications/contraindications for symptom-style queries.
    """
    specs = _specs_by_type()
    hits = await _hybrid_search_one(db, specs["medication"], query or "", tenant_id, limit=limit)
    return await _fetch_orm_in_rank_order(db, MedicationCatalog, hits)


async def search_biomarkers(
    db: AsyncSession,
    tenant_id: UUID,
    query: str | None,
    limit: int = DEFAULT_LIMIT,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[BiomarkerDefinition]:
    """Tenant-scoped hybrid search over biomarker definitions.

    Replaces the ``search_available_biomarkers`` chatbot tool that
    used unindexed POSIX regex (~*) with no tenant scoping. Now searches
    name/slug/code via trigram + description/info/aliases via FTS, so
    queries like "TSH" match the alias of "Thyroid Stimulating Hormone".
    """
    specs = _specs_by_type()
    hits = await _hybrid_search_one(db, specs["biomarker"], query or "", tenant_id, limit=limit)
    return await _fetch_orm_in_rank_order(db, BiomarkerDefinition, hits)


async def search_allergies(
    db: AsyncSession,
    tenant_id: UUID,
    query: str | None,
    limit: int = DEFAULT_LIMIT,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[AllergyCatalog]:
    """Tenant-scoped hybrid search over the allergy catalog."""
    specs = _specs_by_type()
    hits = await _hybrid_search_one(db, specs["allergy"], query or "", tenant_id, limit=limit)
    return await _fetch_orm_in_rank_order(db, AllergyCatalog, hits)


async def search_clinical_event_types(
    db: AsyncSession,
    tenant_id: UUID,
    query: str | None,
    limit: int = DEFAULT_LIMIT,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[ClinicalEventType]:
    """Tenant-scoped hybrid search over clinical event types."""
    specs = _specs_by_type()
    hits = await _hybrid_search_one(
        db, specs["clinical_event_type"], query or "", tenant_id, limit=limit
    )
    return await _fetch_orm_in_rank_order(db, ClinicalEventType, hits)


async def search_clinical_event_categories(
    db: AsyncSession,
    tenant_id: UUID,
    query: str | None,
    limit: int = DEFAULT_LIMIT,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[Concept]:
    """Tenant-scoped hybrid search over clinical event categories.

    Event categories now live in the unified ``concepts`` table
    (``kind=event_category``)."""
    return await search_concepts(db, tenant_id, query, kind=ConceptKind.EVENT_CATEGORY, limit=limit)


# ---------------------------------------------------------------------------
# Concepts (with kind filter)
# ---------------------------------------------------------------------------


async def search_concepts(
    db: AsyncSession,
    tenant_id: UUID,
    query: str | None,
    kind: ConceptKind | None = None,
    limit: int = DEFAULT_LIMIT,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[Concept]:
    """Tenant-scoped hybrid search over the unified concept table.

    Optionally filtered by ``kind`` (specialty, examination_category,
    biomarker_panel, …). The kind filter is implemented via a subquery on
    ``concept_kind_tags`` injected as an extra static predicate.
    """
    from app.services.catalog.concept_service import concepts_with_kind

    specs = _specs_by_type()
    spec = specs["concept"]

    extra_sql = ""
    extra_params: dict = {}
    if kind is not None:
        # Resolve kind member ids up front, then filter via an IN-list. This
        # keeps the hybrid SQL template free of JOINs and lets Postgres pick
        # the existing concept_kind_tags index.
        kind_ids_result = await db.execute(select(Concept.id).where(concepts_with_kind(kind)))
        kind_ids = [r[0] for r in kind_ids_result.all()]
        if not kind_ids:
            return []
        # Static predicate — ids come from a server-side pre-query, never user input.
        # Use = ANY() so asyncpg expands the list param properly.
        extra_sql = "AND t.id = ANY(:kind_ids)"
        extra_params["kind_ids"] = kind_ids

    norm = _normalize(query)
    if norm is None:
        # Empty/too-short query: list active concepts (alphabetical).
        stmt = select(Concept).where(
            or_(Concept.tenant_id.is_(None), Concept.tenant_id == tenant_id),
            Concept.status == ConceptStatus.ACTIVE,
            Concept.deleted_at.is_(None),
        )
        if kind is not None:
            stmt = stmt.where(concepts_with_kind(kind))
        stmt = stmt.order_by(Concept.display_order.asc(), Concept.name.asc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    hits = await _hybrid_search_one(
        db,
        spec,
        norm,
        tenant_id,
        limit=limit,
        extra_where_sql=extra_sql,
        extra_params=extra_params,
    )
    return await _fetch_orm_in_rank_order(db, Concept, hits)


# ---------------------------------------------------------------------------
# Unified dispatcher (Phase 4) — registry-driven cross-catalog search
# ---------------------------------------------------------------------------


async def _enrich_hit(
    db: AsyncSession,
    type_name: str,
    hit: _HybridHit,
    tenant_id: UUID | None,
) -> dict:
    """Resolve a hit to a rich payload via the catalog adapter's serialize().

    Falls back to ``{id, label}`` if the adapter can't load the row (deleted
    concurrently, etc.).
    """
    # Adapter-driven enrichment (reuses existing serialize logic incl. joins
    # like biomarker.preferred_unit). The adapter is a singleton — safe to
    # call repeatedly.
    try:
        from app.catalogs.registry import CatalogRegistry

        if CatalogRegistry.is_registered(type_name):
            descriptor = CatalogRegistry.get(type_name)
            row = await descriptor.service.get(db, tenant_id, hit.row_id)
            if row:
                row = dict(row)
                row.setdefault("id", str(hit.row_id))
                return row
    except Exception:  # pragma: no cover - defensive; enrichment must never kill a hit
        pass
    return {"id": str(hit.row_id), "label": hit.label}


async def search_catalogs(
    db: AsyncSession,
    tenant_id: UUID | None,
    query: str,
    *,
    types: list[str] | None = None,
    kind: ConceptKind | None = None,
    limit_per_type: int = 5,
    limit_total: int = 20,
    enrich: bool = True,
) -> list[dict]:
    """Hybrid cross-catalog search with global RRF ranking.

    Iterates the catalog registry (filtered by ``types``), runs the hybrid
    trigram+FTS query per catalog, fuses ALL hits GLOBALLY via the per-row
    RRF score already computed, and returns the top ``limit_total`` as
    enriched dicts.

    ``kind`` narrows the ``concept`` catalog to a single ``ConceptKind``
    (e.g. ``EVENT_CATEGORY`` for event-category concepts, ``SPECIALTY`` for
    specialties). It is silently ignored for non-concept catalogs (a kind
    filter is meaningless for biomarkers/anatomy/etc.) and when ``types``
    excludes ``concept``. This lets the same picker power examination-category,
    specialty, and event-category selection without a separate endpoint.

    Set ``enrich=False`` for the minimal ``{type, id, label}`` payload
    (used by the autocomplete-style global ``/search`` endpoint where the
    caller will not display description/score).

    Returns a flat list ordered by RRF score DESC. Each dict carries:
    - ``type`` / ``id`` / ``label`` — identity (always present)
    - ``matched_on`` — list of fields that matched
    - ``snippet`` — ts_headline excerpt (may be null)
    - ``score`` — RRF score (for debugging)
    - ``description`` / ``coding_system`` / ``code`` / ``scope`` / ... —
      catalog-specific enrichment (only when ``enrich=True``).
    """
    from app.catalogs.registry import CatalogRegistry
    from app.services.catalog.concept_service import concepts_with_kind

    norm = _normalize(query)
    if norm is None:
        return []

    specs = _specs_by_type()
    # Map: registry type → spec type. They are 1:1 today, but vaccines'
    # edge endpoint type differs from the catalog type name; the catalog
    # type name is the right key here.
    selected = types if types else list(specs.keys())

    # Resolve concept-kind member ids up front (one query) so the per-catalog
    # loop stays free of JOINs. ``kind`` is only applied on the concept spec;
    # other catalogs ignore it (documented). If the kind resolves to no
    # concepts, concept hits will be empty — handled naturally below.
    concept_kind_ids: list | None = None
    if kind is not None and "concept" in selected:
        kind_ids_result = await db.execute(select(Concept.id).where(concepts_with_kind(kind)))
        concept_kind_ids = [r[0] for r in kind_ids_result.all()]

    all_hits: list[tuple[str, _HybridHit]] = []
    for type_name in selected:
        # Prefer the spec (drives the hybrid SQL); fall back to the registry
        # adapter's older search() for catalogs without a spec (none today,
        # but defensive).
        spec = specs.get(type_name)
        if spec is not None:
            # Apply the kind filter only on the concept catalog.
            extra_where_sql = ""
            extra_params: dict | None = None
            if kind is not None and type_name == "concept" and concept_kind_ids is not None:
                if not concept_kind_ids:
                    # No concepts of this kind exist — skip concept search
                    # entirely (avoids ANY(:kind_ids) with an empty list,
                    # which asyncpg treats as match-nothing anyway).
                    continue
                extra_where_sql = "AND t.id = ANY(:kind_ids)"
                extra_params = {"kind_ids": concept_kind_ids}
            try:
                hits = await _hybrid_search_one(
                    db,
                    spec,
                    norm,
                    tenant_id,
                    limit=limit_per_type * 2,
                    extra_where_sql=extra_where_sql,
                    extra_params=extra_params,
                )
            except Exception as exc:  # pragma: no cover - defensive
                import logging

                logging.getLogger(__name__).warning("hybrid search '%s' failed: %s", type_name, exc)
                hits = []
        elif CatalogRegistry.is_registered(type_name):
            # Legacy adapter fallback (older search() contract).
            descriptor = CatalogRegistry.get(type_name)
            try:
                rows = await descriptor.service.search(db, tenant_id, norm, limit=limit_per_type)
            except Exception as exc:  # pragma: no cover
                import logging

                logging.getLogger(__name__).warning(
                    "adapter search '%s' failed: %s", type_name, exc
                )
                rows = []
            hits = [
                _HybridHit(
                    row_id=r["id"],
                    label=r.get("label", ""),
                    score=0.0,
                    matched_on=["name"],
                    snippet=None,
                )
                for r in rows
            ]
        else:
            continue

        for h in hits:
            all_hits.append((type_name, h))

    # Global RRF: each per-catalog hit already carries an RRF score from its
    # own ranking. To compare across catalogs we re-rank globally — sort by
    # score DESC (the per-catalog RRF already normalises the score scale to
    # ~[0, 2/RRF_K]), with name as a tiebreaker. The cross-catalog comparison
    # is approximate (per-catalog RRF scores aren't perfectly calibrated
    # against each other), but RRF's rank-based design makes it robust.
    all_hits.sort(key=lambda pair: (pair[1].score, pair[1].label), reverse=True)

    # Per-type floor: guarantee the top-1 hit from every catalog that matched
    # survives the global trim, so a single strongly-matching catalog can't
    # completely hide a smaller one. Without this, a query that returns many
    # biomarker hits would push allergy/anatomy hits off the result page even
    # though the user explicitly matched something in those catalogs.
    if limit_total and len(all_hits) > limit_total:
        kept = set()
        preserved: list[tuple[str, _HybridHit]] = []
        for type_name, hit in all_hits:
            if type_name not in kept:
                kept.add(type_name)
                preserved.append((type_name, hit))
        # Fill remaining slots from the globally-sorted list, skipping the
        # already-preserved top-1-per-type.
        preserved_ids = {id(h) for _, h in preserved}
        for pair in all_hits:
            if len(preserved) >= limit_total:
                break
            if id(pair[1]) not in preserved_ids:
                preserved.append(pair)
                preserved_ids.add(id(pair[1]))
        # Re-sort the final list by score so the LLM sees a ranked result.
        preserved.sort(key=lambda pair: (pair[1].score, pair[1].label), reverse=True)
        all_hits = preserved

    if not enrich:
        return [{"type": t, "id": str(h.row_id), "label": h.label} for t, h in all_hits]

    # Enriched payload — one adapter.get() per hit. Hits are bounded by
    # limit_total (default 20), so this is at most 20 extra queries — fine
    # at catalog scale; the alternative (single SELECT IN per catalog) would
    # bypass the adapter's serialization (biomarker's Unit join, concept's
    # parent_slug attachment, etc.).
    out: list[dict] = []
    for type_name, hit in all_hits:
        payload = await _enrich_hit(db, type_name, hit, tenant_id)
        payload["type"] = type_name
        payload["id"] = str(hit.row_id)
        payload["label"] = payload.get("label") or hit.label
        payload["matched_on"] = hit.matched_on
        payload["snippet"] = hit.snippet
        payload["score"] = hit.score
        out.append(payload)
    return out
