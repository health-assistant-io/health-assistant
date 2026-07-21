"""Apply integration-sourced catalog proposals.

Closes the gap left by ``ConceptProvenance.INTEGRATION`` (declared in the
enum but unwritten before workstream F). Providers opt in via
:meth:`integrations.sdk.base.BaseHealthProvider.supports_catalog_proposals`
+ :meth:`pull_catalog_proposals`; the engine's ``run_sync`` calls
:func:`apply_proposal` on each item.

Routing by ``kind``:

- ``biomarker`` → writes a :class:`~app.models.biomarker_model.BiomarkerDefinition`
  with ``scope``/``tenant_id``/``created_by`` stamped by
  :class:`~app.catalogs.policy.CatalogWritePolicy`, plus a
  ``meta_data["_provenance"] = "integration"`` tag (the model has no
  dedicated provenance column). Idempotent on ``slug``.
- ``medication`` → :func:`app.services.medication_service.create_catalog_medication`.
  Idempotent on ``(tenant_id, name)`.
- ``concept`` → :meth:`app.services.concept_service.ConceptService.create_concept`.
  Idempotent on ``slug``.
- ``edge`` → :meth:`~app.services.concept_service.ConceptService.create_edge`
  with ``source=ConceptProvenance.INTEGRATION, status=APPROVED``.
  Idempotent on ``(src, dst, relation)``.

Per-proposal failures are logged and never raised — the engine wraps the
call in a try/except per item so one bad proposal can't abort the sync.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogs.policy import DEFAULT_CATALOG_POLICY
from app.models.biomarker_model import BiomarkerDefinition, Unit
from app.models.enums import (
    ConceptKind,
    ConceptProvenance,
    ConceptRelationType,
    EdgeApprovalStatus,
    EdgeEndpointType,
)
from app.models.fhir.medication import MedicationCatalog
from app.schemas.biomarker import BiomarkerCreate, sanitize_slug
from app.schemas.medication import MedicationCatalogCreate
from app.services.concept_service import (
    ConceptService,
    resolve_biomarker_class_concept,
    resolve_concept_by_slug,
)
from integrations.sdk.catalog import CatalogProposal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class ApplyResult:
    """Outcome of applying one :class:`CatalogProposal`.

    ``created`` is ``True`` when a new row was inserted, ``False`` when the
    proposal was a no-op (idempotent re-application of an existing entry).
    ``entity_id`` is the row's primary key (regardless of created/updated).
    """

    kind: str
    created: bool
    entity_id: Optional[UUID]
    slug: Optional[str] = None
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


async def apply_proposal(
    db: AsyncSession,
    actor: Any,
    integration: Any,
    proposal: CatalogProposal,
) -> ApplyResult:
    """Apply one catalog proposal.

    Routes by ``proposal.kind`` to the matching service-layer write path.
    Provenance is implicit — every write goes through ``actor`` (the
    integration owner's :class:`~app.schemas.user.TokenData`, resolved via
    :func:`app.services.integration_actor.resolve_integration_actor`), so
    ``AuditMixin.created_by`` records the owning user and ``scope`` is
    derived from their role.

    Args:
        db: Active session. Caller commits.
        actor: TokenData for the integration's owning user.
        integration: The :class:`~app.models.user_integration.UserIntegration`
            sourcing the proposal — used for logging only.
        proposal: The :class:`CatalogProposal` to apply.

    Raises:
        ValueError: proposal payload is missing required fields or
            references a non-existent endpoint (for ``edge``).
        PermissionError: the actor's role can't perform the write
            (e.g. a USER-role integration owner can't create concepts or
            edges). The engine logs and continues per-item.
    """
    if proposal.kind == "biomarker":
        return await _apply_biomarker_proposal(db, actor, proposal)
    if proposal.kind == "medication":
        return await _apply_medication_proposal(db, actor, proposal)
    if proposal.kind == "concept":
        return await _apply_concept_proposal(db, actor, proposal)
    if proposal.kind == "edge":
        return await _apply_edge_proposal(db, actor, proposal)
    raise ValueError(
        f"CatalogProposal kind {proposal.kind!r} is not one of "
        "'biomarker', 'medication', 'concept', 'edge'."
    )


# ---------------------------------------------------------------------------
# biomarker router
# ---------------------------------------------------------------------------


async def _apply_biomarker_proposal(
    db: AsyncSession,
    actor: Any,
    proposal: CatalogProposal,
) -> ApplyResult:
    """Apply a ``kind="biomarker"`` proposal.

    Mirrors the ``POST /api/v1/biomarkers/`` endpoint logic exactly:
    validates via :class:`BiomarkerCreate`, resolves the preferred unit
    symbol, resolves the ``biomarker_class`` concept from the legacy
    ``category`` string, stamps scope via
    :meth:`CatalogWritePolicy.assign_create_scope`. Idempotent on ``slug``.
    """
    payload = dict(proposal.payload)

    name = payload.get("name")
    if not name or not str(name).strip():
        raise ValueError("biomarker proposal payload requires a non-empty 'name'")

    raw_slug = payload.get("slug") or str(name)
    slug = sanitize_slug(raw_slug)

    biomarker_payload = {
        "name": str(name).strip(),
        "slug": slug,
        "coding_system": payload.get("coding_system") or "loinc",
        "code": payload.get("code") or None,
        "category": payload.get("category") or None,
        "aliases": list(payload.get("aliases") or []),
        "info": payload.get("info") or None,
        "reference_range_min": payload.get("reference_range_min"),
        "reference_range_max": payload.get("reference_range_max"),
        "is_telemetry": bool(payload.get("is_telemetry") or False),
        "preferred_unit_symbol": payload.get("preferred_unit_symbol") or None,
    }
    # Validate the payload shape — guards against provider typos reaching the
    # ORM layer. Raises pydantic ValidationError on bad shapes, which the
    # engine wraps in a try/except per item.
    BiomarkerCreate(**biomarker_payload)

    existing = await _find_biomarker_by_slug(db, slug)
    if existing is not None:
        return ApplyResult(
            kind="biomarker",
            created=False,
            entity_id=existing.id,
            slug=existing.slug,
            detail="biomarker with this slug already exists",
        )

    preferred_unit_id = await _resolve_unit_id(
        db, biomarker_payload["preferred_unit_symbol"]
    )
    class_concept_id = await resolve_biomarker_class_concept(
        db, biomarker_payload["category"], tenant_id=actor.tenant_id
    )

    new_bio = BiomarkerDefinition(
        slug=slug,
        name=biomarker_payload["name"],
        coding_system=biomarker_payload["coding_system"],
        code=biomarker_payload["code"],
        class_concept_id=class_concept_id,
        aliases=biomarker_payload["aliases"],
        info=biomarker_payload["info"],
        reference_range_min=biomarker_payload["reference_range_min"],
        reference_range_max=biomarker_payload["reference_range_max"],
        is_telemetry=biomarker_payload["is_telemetry"],
        preferred_unit_id=preferred_unit_id,
        meta_data={"_provenance": "integration"},
    )
    DEFAULT_CATALOG_POLICY.assign_create_scope(
        actor.role, new_bio, actor.tenant_id, actor.user_id
    )
    db.add(new_bio)
    try:
        await db.flush()
    except IntegrityError as exc:
        # Race: another sync inserted the same slug between our SELECT and
        # INSERT. Treat as idempotent no-op (re-fetch).
        await db.rollback()
        logger.info(
            "biomarker proposal slug=%s raced another writer — re-fetching", slug
        )
        raced = await _find_biomarker_by_slug(db, slug)
        if raced is not None:
            return ApplyResult(
                kind="biomarker",
                created=False,
                entity_id=raced.id,
                slug=raced.slug,
                detail="biomarker created concurrently by another writer",
            )
        raise ValueError(
            f"biomarker proposal slug={slug!r} failed integrity check: {exc}"
        ) from exc

    return ApplyResult(
        kind="biomarker",
        created=True,
        entity_id=new_bio.id,
        slug=new_bio.slug,
    )


async def _find_biomarker_by_slug(
    db: AsyncSession, slug: str
) -> Optional[BiomarkerDefinition]:
    result = await db.execute(
        select(BiomarkerDefinition).where(BiomarkerDefinition.slug == slug)
    )
    return result.scalar_one_or_none()


async def _resolve_unit_id(
    db: AsyncSession, symbol: Optional[str]
) -> Optional[UUID]:
    if not symbol:
        return None
    result = await db.execute(select(Unit).where(Unit.symbol == symbol))
    unit = result.scalar_one_or_none()
    return unit.id if unit else None


# ---------------------------------------------------------------------------
# medication router (F.2)
# ---------------------------------------------------------------------------


async def _apply_medication_proposal(
    db: AsyncSession,
    actor: Any,
    proposal: CatalogProposal,
) -> ApplyResult:
    """Apply a ``kind="medication"`` proposal.

    Validates via :class:`MedicationCatalogCreate`, then routes through
    :func:`app.services.medication_service.create_catalog_medication`
    (which stamps scope via
    :meth:`CatalogWritePolicy.assign_create_scope` and FHIR-validates the
    row). Idempotent on ``(tenant_id, name)`` — re-proposing the same
    medication in the same tenant returns the existing row without
    duplicating.
    """
    payload = dict(proposal.payload)
    name = payload.get("name")
    if not name or not str(name).strip():
        raise ValueError(
            "medication proposal payload requires a non-empty 'name'"
        )

    MedicationCatalogCreate(**payload)

    existing = await _find_medication_by_name(db, actor.tenant_id, str(name))
    if existing is not None:
        return ApplyResult(
            kind="medication",
            created=False,
            entity_id=existing.id,
            detail="medication with this name already exists in tenant",
        )

    # ``create_catalog_medication`` calls ``db.commit()`` itself — inconsistent
    # with the other routers that just flush. Match its contract by giving it
    # an isolated session and re-raising the (already-stamped) row to the
    # caller. Idempotent on race via the pre-check above.
    from app.services.medication_service import create_catalog_medication

    new_entry = await create_catalog_medication(
        db, actor, MedicationCatalogCreate(**payload)
    )
    return ApplyResult(
        kind="medication",
        created=True,
        entity_id=new_entry.id,
        detail=new_entry.name,
    )


async def _find_medication_by_name(
    db: AsyncSession, tenant_id: Optional[UUID], name: str
) -> Optional[MedicationCatalog]:
    """Lookup matching the same tenant-scope read path used by the catalog
    service: SYSTEM rows (``tenant_id IS NULL``) are visible to all
    tenants; TENANT rows are scoped to the actor's tenant."""
    stmt = select(MedicationCatalog).where(
        MedicationCatalog.name == name,
        (
            MedicationCatalog.tenant_id.is_(None)
            if tenant_id is None
            else (
                MedicationCatalog.tenant_id.in_([None, tenant_id])
            )
        ),
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# concept router (F.2)
# ---------------------------------------------------------------------------


async def _apply_concept_proposal(
    db: AsyncSession,
    actor: Any,
    proposal: CatalogProposal,
) -> ApplyResult:
    """Apply a ``kind="concept"`` proposal.

    Routes through :meth:`ConceptService.create_concept`, which stamps
    scope from the actor's role + tenant_id and emits a best-effort audit
    row. Idempotent on ``slug`` — re-proposing returns the existing row.
    """
    payload = dict(proposal.payload)
    slug = payload.get("slug")
    name = payload.get("name")
    kind_value = payload.get("kind")
    if not slug or not str(slug).strip():
        raise ValueError("concept proposal payload requires a non-empty 'slug'")
    if not name or not str(name).strip():
        raise ValueError("concept proposal payload requires a non-empty 'name'")
    if not kind_value:
        raise ValueError(
            "concept proposal payload requires a 'kind' "
            "(a ConceptKind value, e.g. 'disease')"
        )

    try:
        kind_enum = ConceptKind(str(kind_value).lower())
    except ValueError as exc:
        valid = ", ".join(k.value for k in ConceptKind)
        raise ValueError(
            f"concept proposal kind {kind_value!r} is not a valid "
            f"ConceptKind (valid: {valid})"
        ) from exc

    svc = ConceptService(db)
    existing_id = await resolve_concept_by_slug(
        db, str(slug), kind=kind_enum, tenant_id=actor.tenant_id
    )
    if existing_id is not None:
        return ApplyResult(
            kind="concept",
            created=False,
            entity_id=existing_id,
            slug=str(slug),
            detail="concept with this slug already exists",
        )

    try:
        concept = await svc.create_concept(
            slug=str(slug).strip(),
            name=str(name).strip(),
            tenant_id=actor.tenant_id,
            role=actor.role,
            kind=kind_enum,
            description=payload.get("description"),
            coding_system=payload.get("coding_system"),
            code=payload.get("code"),
            aliases=payload.get("aliases") or [],
            created_by=actor.user_id,
            actor=actor,
        )
    except ValueError as exc:
        # Race: another sync inserted the same slug between our lookup and
        # create. Treat as idempotent.
        if "already exists" in str(exc):
            await db.rollback()
            raced_id = await resolve_concept_by_slug(
                db, str(slug), kind=kind_enum, tenant_id=actor.tenant_id
            )
            if raced_id is not None:
                return ApplyResult(
                    kind="concept",
                    created=False,
                    entity_id=raced_id,
                    slug=str(slug),
                    detail="concept created concurrently by another writer",
                )
        raise
    await db.flush()
    return ApplyResult(
        kind="concept",
        created=True,
        entity_id=concept.id,
        slug=concept.slug,
    )


# ---------------------------------------------------------------------------
# edge router (F.2)
# ---------------------------------------------------------------------------


async def _apply_edge_proposal(
    db: AsyncSession,
    actor: Any,
    proposal: CatalogProposal,
) -> ApplyResult:
    """Apply a ``kind="edge"`` proposal.

    Routes through :meth:`ConceptService.create_edge` with
    ``source=ConceptProvenance.INTEGRATION, status=APPROVED`` — the only
    catalog write path today that actually stamps the integration
    provenance (``ConceptEdge`` is the only model with a dedicated
    ``source`` column). Idempotent on the natural key
    ``(src_type, src_id, dst_type, dst_id, relation, tenant_id)``.
    """
    payload = dict(proposal.payload)
    required = ("src_type", "src_id", "dst_type", "dst_id", "relation")
    missing = [k for k in required if not payload.get(k)]
    if missing:
        raise ValueError(
            f"edge proposal payload missing required field(s): {missing}"
        )

    try:
        src_type = EdgeEndpointType(str(payload["src_type"]).lower())
        dst_type = EdgeEndpointType(str(payload["dst_type"]).lower())
        relation = ConceptRelationType(str(payload["relation"]).upper())
    except ValueError as exc:
        raise ValueError(
            f"edge proposal endpoint/relation not a valid enum value: {exc}"
        ) from exc

    try:
        src_id = UUID(str(payload["src_id"]))
        dst_id = UUID(str(payload["dst_id"]))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"edge proposal src_id / dst_id must be valid UUIDs: {exc}"
        ) from exc

    svc = ConceptService(db)
    existing = await _find_edge(
        db,
        tenant_id=actor.tenant_id,
        src_type=src_type,
        src_id=src_id,
        dst_type=dst_type,
        dst_id=dst_id,
        relation=relation,
    )
    if existing is not None:
        return ApplyResult(
            kind="edge",
            created=False,
            entity_id=existing.id,
            detail="edge with these endpoints + relation already exists",
        )

    try:
        edge = await svc.create_edge(
            src_type=src_type,
            src_id=src_id,
            dst_type=dst_type,
            dst_id=dst_id,
            relation=relation,
            tenant_id=actor.tenant_id,
            role=actor.role,
            properties=payload.get("properties"),
            evidence=payload.get("evidence"),
            source=ConceptProvenance.INTEGRATION,
            status=EdgeApprovalStatus.APPROVED,
            created_by=actor.user_id,
        )
    except ValueError as exc:
        if "already exists" in str(exc):
            # Race; re-fetch.
            await db.rollback()
            raced = await _find_edge(
                db,
                tenant_id=actor.tenant_id,
                src_type=src_type,
                src_id=src_id,
                dst_type=dst_type,
                dst_id=dst_id,
                relation=relation,
            )
            if raced is not None:
                return ApplyResult(
                    kind="edge",
                    created=False,
                    entity_id=raced.id,
                    detail="edge created concurrently by another writer",
                )
        raise
    return ApplyResult(
        kind="edge",
        created=True,
        entity_id=edge.id,
        detail=f"{src_type.value}:{src_id} -[{relation.value}]-> {dst_type.value}:{dst_id}",
    )


async def _find_edge(
    db: AsyncSession,
    *,
    tenant_id: Optional[UUID],
    src_type: EdgeEndpointType,
    src_id: UUID,
    dst_type: EdgeEndpointType,
    dst_id: UUID,
    relation: ConceptRelationType,
):
    """Lookup an edge by its natural key within the actor's tenant scope.

    ``ConceptEdge`` doesn't have a uniqueness constraint today, so we
    treat the *first* match as the idempotent anchor — providers proposing
    the same edge twice will get the first one back.
    """
    from app.models.concept_model import ConceptEdge

    stmt = select(ConceptEdge).where(
        ConceptEdge.src_type == src_type,
        ConceptEdge.src_id == src_id,
        ConceptEdge.dst_type == dst_type,
        ConceptEdge.dst_id == dst_id,
        ConceptEdge.relation == relation,
    )
    if tenant_id is None:
        stmt = stmt.where(ConceptEdge.tenant_id.is_(None))
    else:
        stmt = stmt.where(
            (ConceptEdge.tenant_id == tenant_id)
            | (ConceptEdge.tenant_id.is_(None))
        )
    result = await db.execute(stmt)
    return result.scalars().first()


__all__ = [
    "ApplyResult",
    "apply_proposal",
]
