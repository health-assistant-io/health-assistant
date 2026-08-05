from typing import List
import logging
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update as sa_update, or_
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.models.biomarker_model import (
    BiomarkerAllowedState,
    BiomarkerDefinition,
    BiomarkerReferenceRange,
    BiomarkerState,
    Unit,
)
from app.models.fhir.patient import Observation
from app.schemas.biomarker import (
    AllowedStateSpec,
    BiomarkerCreate,
    BiomarkerStateResponse,
    BiomarkerUpdate,
    BiomarkerResponse,
    BiomarkerRemapRequest,
    BiomarkerReferenceRangeCreate,
    BiomarkerReferenceRangeUpdate,
    BiomarkerReferenceRangeResponse,
    UnitResponse,
    UnitCreate,
)
from app.services.concept_service import resolve_biomarker_class_concept
from app.catalogs.policy import DEFAULT_CATALOG_POLICY
from app.models.enums import BiomarkerValueType
from uuid import UUID

router = APIRouter(prefix="/biomarkers", tags=["biomarkers"])

logger = logging.getLogger(__name__)


def _tenant_scope(tenant_id):
    """Standard global+tenant read filter for biomarker definitions."""
    return or_(
        BiomarkerDefinition.tenant_id.is_(None),
        BiomarkerDefinition.tenant_id == tenant_id,
    )


async def _reload_biomarker(db: AsyncSession, bio_id) -> BiomarkerDefinition:
    """Reload a BiomarkerDefinition with all selectin relationships populated.

    After ``db.commit`` + ``db.refresh``, accessing lazy relationships
    (``allowed_states``, ``reference_ranges``, ``preferred_unit``,
    ``class_concept``) triggers a greenlet error in async context. This
    helper re-fetches the row with the chains eager-loaded so
    :func:`_serialize_biomarker` can read them safely.

    ``populate_existing()`` is critical: without it, SQLAlchemy's identity
    map returns the already-cached BiomarkerDefinition with stale
    ``allowed_states`` (the post-commit relationship state isn't refreshed
    by a plain ``selectinload`` when the parent is already in the map).
    """
    return (
        (
            await db.execute(
                select(BiomarkerDefinition)
                .options(
                    selectinload(BiomarkerDefinition.allowed_states).selectinload(
                        BiomarkerAllowedState.state
                    ),
                    selectinload(BiomarkerDefinition.reference_ranges),
                    selectinload(BiomarkerDefinition.preferred_unit),
                    selectinload(BiomarkerDefinition.class_concept),
                )
                .where(BiomarkerDefinition.id == bio_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalar_one()
    )


def _serialize_allowed_states(bio: BiomarkerDefinition) -> List[dict]:
    """Resolve a STATE biomarker's ``allowed_states`` join rows into the
    ``BiomarkerAllowedStateResponse`` payload shape.

    Assumes ``bio.allowed_states`` is loaded (``selectin``) and each row's
    ``state`` is also loaded (``selectin``). Returns ``[]`` for QUANTITY
    biomarkers (none configured).
    """
    out = []
    for allowed in bio.allowed_states or []:
        state = allowed.state
        if state is None:
            continue
        out.append(
            {
                "state_id": state.id,
                "state_slug": state.slug,
                "code": state.code,
                "system": state.system,
                "display": state.display,
                "is_normal": allowed.is_normal,
                "sort_order": allowed.sort_order,
            }
        )
    out.sort(key=lambda d: d["sort_order"])
    return out


def _serialize_biomarker(bio: BiomarkerDefinition, symbol) -> dict:
    """Build the standard ``BiomarkerResponse`` dict for a definition.

    Centralizes the response shape so list/get/create/update/retry-migration
    endpoints agree. Emits the new ``value_type`` / ``supports_multi_state``
    fields and the resolved ``allowed_states`` list (empty for QUANTITY).
    """
    return {
        "id": bio.id,
        "slug": bio.slug,
        "coding_system": bio.coding_system,
        "code": bio.code,
        "name": bio.name,
        "category": bio.category,
        "aliases": bio.aliases,
        "preferred_unit_id": bio.preferred_unit_id,
        "info": bio.info,
        "reference_range_min": bio.reference_range_min,
        "reference_range_max": bio.reference_range_max,
        "is_telemetry": bio.is_telemetry,
        "value_type": bio.value_type,
        "supports_multi_state": bio.supports_multi_state,
        "allowed_states": _serialize_allowed_states(bio),
        "meta_data": bio.meta_data,
        "preferred_unit_symbol": symbol,
        "reference_ranges": getattr(bio, "reference_ranges", None) or [],
    }


async def _resolve_state_slugs(
    db: AsyncSession, specs: List[AllowedStateSpec]
) -> List[BiomarkerAllowedState]:
    """Resolve a list of ``AllowedStateSpec`` (slug-keyed input) to
    ``BiomarkerAllowedState`` ORM rows ready to attach to a definition.

    Raises ``HTTPException(400)`` if any slug doesn't resolve to a state row.
    """
    if not specs:
        return []
    slugs = [s.state_slug for s in specs]
    rows = (
        (
            await db.execute(
                select(BiomarkerState).where(BiomarkerState.slug.in_(slugs))
            )
        )
        .scalars()
        .all()
    )
    by_slug = {r.slug: r for r in rows}
    missing = [s for s in slugs if s not in by_slug]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown biomarker_state slug(s): {missing}",
        )
    out = []
    for spec in specs:
        state = by_slug[spec.state_slug]
        out.append(
            BiomarkerAllowedState(
                state_id=state.id,
                is_normal=spec.is_normal,
                sort_order=spec.sort_order,
            )
        )
    return out


# TODO: Add endpoint /api/v1/biomarkers/correlated for querying by organ/symptom (from DEVELOPMENT_PLAN.md)
# TODO: Add endpoints to retrieve correlated biomarkers for a given clinical event (from DEVELOPMENT_PLAN.md)


@router.get("/", response_model=List[BiomarkerResponse])
async def get_biomarkers(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get all biomarker definitions visible to the caller (global + tenant)."""
    result = await db.execute(
        select(BiomarkerDefinition, Unit.symbol.label("unit_symbol"))
        .outerjoin(Unit, BiomarkerDefinition.preferred_unit_id == Unit.id)
        .where(_tenant_scope(current_user.tenant_id))
        .order_by(BiomarkerDefinition.name)
    )
    rows = result.all()

    response = [_serialize_biomarker(bio, symbol) for bio, symbol in rows]
    return response


@router.get("/units", response_model=List[UnitResponse])
async def get_units(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get all units"""
    result = await db.execute(select(Unit))
    return result.scalars().all()


@router.post("/units", response_model=UnitResponse)
async def create_unit(
    unit_in: UnitCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create a new unit"""
    # Check if symbol exists
    result = await db.execute(select(Unit).where(Unit.symbol == unit_in.symbol))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Unit symbol already exists")

    new_unit = Unit(
        symbol=unit_in.symbol,
        name=unit_in.name,
        quantity_type=unit_in.quantity_type,
    )
    db.add(new_unit)
    try:
        await db.commit()
        await db.refresh(new_unit)
        return new_unit
    except Exception:
        await db.rollback()
        logger.exception("biomarker operation failed")
        raise HTTPException(
            status_code=400,
            detail="Request could not be completed (see server log).",
        )


@router.get("/states", response_model=List[BiomarkerStateResponse])
async def list_biomarker_states(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """List the universal ``biomarker_states`` catalog.

    Public (any authenticated user) — the catalog is universal clinical
    vocabulary (no tenant scoping). Powers the STATE-biomarker admin UI
    (allowed_states picker) and any consumer that needs to resolve a
    state code to its display label.
    """
    result = await db.execute(
        select(BiomarkerState).order_by(
            BiomarkerState.sort_order.asc(), BiomarkerState.display.asc()
        )
    )
    return result.scalars().all()


@router.post("/", response_model=BiomarkerResponse)
async def create_biomarker(
    biomarker: BiomarkerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create a new biomarker definition (scope derived from role)."""
    # Find unit
    unit_id = biomarker.preferred_unit_id
    if not unit_id and biomarker.preferred_unit_symbol:
        u_result = await db.execute(
            select(Unit).where(Unit.symbol == biomarker.preferred_unit_symbol)
        )
        unit = u_result.scalar_one_or_none()
        if unit:
            unit_id = unit.id

    # Resolve legacy ``category`` string to a ``biomarker_class`` concept ID.
    # An explicit ``class_concept_id`` wins; otherwise we best-effort resolve
    # the legacy ``category`` slug.
    class_concept_id = biomarker.class_concept_id
    if class_concept_id is None:
        class_concept_id = await resolve_biomarker_class_concept(
            db, biomarker.category, tenant_id=current_user.tenant_id
        )

    new_bio = BiomarkerDefinition(
        slug=biomarker.slug,
        name=biomarker.name,
        class_concept_id=class_concept_id,
        aliases=biomarker.aliases,
        info=biomarker.info,
        reference_range_min=biomarker.reference_range_min,
        reference_range_max=biomarker.reference_range_max,
        is_telemetry=biomarker.is_telemetry,
        preferred_unit_id=unit_id,
        # State-biomarker discriminator (plan state-biomarkers-2026-08-05).
        value_type=biomarker.value_type,
        supports_multi_state=biomarker.supports_multi_state or False,
    )
    DEFAULT_CATALOG_POLICY.assign_create_scope(
        current_user.role, new_bio, current_user.tenant_id, current_user.user_id
    )
    db.add(new_bio)
    await db.flush()  # need new_bio.id for the allowed_states FK

    # STATE biomarkers: resolve the allowed_states slug list once, then attach
    # the join rows directly (assigning to ``new_bio.allowed_states`` would
    # trigger a lazy load of the empty collection — greenlet error in async).
    if biomarker.value_type == BiomarkerValueType.STATE:
        for row in await _resolve_state_slugs(db, biomarker.allowed_states):
            row.biomarker_id = new_bio.id
            db.add(row)

    try:
        await db.commit()
        # Reload with eager-loaded relationships (allowed_states, etc.) —
        # ``db.refresh`` doesn't repopulate selectin chains and accessing
        # them in async context triggers a greenlet error.
        new_bio = await _reload_biomarker(db, new_bio.id)

        # Get unit symbol
        symbol = None
        if new_bio.preferred_unit_id:
            u_res = await db.execute(
                select(Unit.symbol).where(Unit.id == new_bio.preferred_unit_id)
            )
            symbol = u_res.scalar_one_or_none()

        return _serialize_biomarker(new_bio, symbol)
    except Exception:
        await db.rollback()
        logger.exception("biomarker operation failed")
        raise HTTPException(
            status_code=400,
            detail="Request could not be completed (see server log).",
        )


@router.delete("/{biomarker_id}")
async def delete_biomarker(
    biomarker_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Delete a biomarker definition. Global rows require SYSTEM_ADMIN."""
    result = await db.execute(
        select(BiomarkerDefinition).where(
            BiomarkerDefinition.id == biomarker_id,
            _tenant_scope(current_user.tenant_id),
        )
    )
    db_biomarker = result.scalar_one_or_none()

    if not db_biomarker:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    DEFAULT_CATALOG_POLICY.check_modify(
        current_user.role,
        db_biomarker.scope,
        item_created_by=db_biomarker.created_by,
        actor_user_id=current_user.user_id,
    )

    await db.delete(db_biomarker)
    try:
        await db.commit()
        return {"status": "success", "message": "Biomarker deleted"}
    except Exception:
        await db.rollback()
        logger.exception("biomarker operation failed")
        raise HTTPException(
            status_code=400,
            detail="Request could not be completed (see server log).",
        )


@router.post("/bulk-delete")
async def bulk_delete_biomarkers(
    biomarker_ids: List[UUID] = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Bulk delete biomarker definitions (tenant-scoped; ADMIN/MANAGER+).

    Only the caller's own tenant rows are ever touched — system rows are never
    bulk-deleted (use the single-item endpoint as SYSTEM_ADMIN for that). A USER
    may only bulk-delete their own user-scope rows."""
    from app.models.enums import CatalogScope

    try:
        # SYSTEM_ADMIN/ADMIN/MANAGER: any tenant row. USER: only own user-scope.
        if DEFAULT_CATALOG_POLICY.create_scope(current_user.role) in (
            CatalogScope.SYSTEM,
            CatalogScope.TENANT,
        ):
            await db.execute(
                delete(BiomarkerDefinition).where(
                    BiomarkerDefinition.id.in_(biomarker_ids),
                    BiomarkerDefinition.tenant_id == current_user.tenant_id,
                )
            )
        else:
            await db.execute(
                delete(BiomarkerDefinition).where(
                    BiomarkerDefinition.id.in_(biomarker_ids),
                    BiomarkerDefinition.tenant_id == current_user.tenant_id,
                    BiomarkerDefinition.scope == CatalogScope.USER,
                    BiomarkerDefinition.created_by == current_user.user_id,
                )
            )
        await db.commit()
        return {
            "status": "success",
            "message": f"{len(biomarker_ids)} biomarkers deleted",
        }
    except Exception:
        await db.rollback()
        logger.exception("biomarker operation failed")
        raise HTTPException(
            status_code=400,
            detail="Request could not be completed (see server log).",
        )


@router.get("/slug/{slug}")
async def get_biomarker_by_slug(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get a single biomarker definition by its slug"""
    result = await db.execute(
        select(BiomarkerDefinition, Unit.symbol.label("unit_symbol"))
        .outerjoin(Unit, BiomarkerDefinition.preferred_unit_id == Unit.id)
        .where(BiomarkerDefinition.slug == slug, _tenant_scope(current_user.tenant_id))
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    bio, symbol = row
    return _serialize_biomarker(bio, symbol)


@router.get("/{biomarker_id}", response_model=BiomarkerResponse)
async def get_biomarker_by_id(
    biomarker_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get a single biomarker definition by its ID"""
    result = await db.execute(
        select(BiomarkerDefinition, Unit.symbol.label("unit_symbol"))
        .outerjoin(Unit, BiomarkerDefinition.preferred_unit_id == Unit.id)
        .where(
            BiomarkerDefinition.id == biomarker_id,
            _tenant_scope(current_user.tenant_id),
        )
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    bio, symbol = row
    return _serialize_biomarker(bio, symbol)


@router.post("/{biomarker_id}/retry-migration", response_model=BiomarkerResponse)
async def retry_biomarker_migration(
    biomarker_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Retry a stuck or failed biomarker data migration"""
    result = await db.execute(
        select(BiomarkerDefinition).where(BiomarkerDefinition.id == biomarker_id)
    )
    db_biomarker = result.scalar_one_or_none()

    if not db_biomarker:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    meta = dict(db_biomarker.meta_data or {})

    # We only allow retrying if it actually was marked as in progress or failed
    if meta.get("migration_status") not in ["failed", "in_progress"]:
        raise HTTPException(
            status_code=400, detail="No active or failed migration to retry"
        )

    meta["migration_status"] = "in_progress"
    meta["migration_progress"] = 0
    if "migration_error" in meta:
        del meta["migration_error"]

    db_biomarker.meta_data = meta

    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(db_biomarker, "meta_data")

    # Trigger celery task again using current is_telemetry state
    from app.workers.tasks import migrate_biomarker_data

    migrate_biomarker_data.delay(
        str(db_biomarker.id),
        str(current_user.tenant_id),
        bool(db_biomarker.is_telemetry),
    )

    try:
        await db.commit()
        db_biomarker = await _reload_biomarker(db, db_biomarker.id)
    except Exception:
        await db.rollback()
        logger.exception("biomarker operation failed")
        raise HTTPException(
            status_code=400,
            detail="Request could not be completed (see server log).",
        )

    # Return with symbol
    u_res = await db.execute(
        select(Unit.symbol).where(Unit.id == db_biomarker.preferred_unit_id)
    )
    symbol = u_res.scalar_one_or_none()

    return _serialize_biomarker(db_biomarker, symbol)


@router.post("/{biomarker_id}/remap")
async def remap_observations(
    biomarker_id: UUID,
    payload: BiomarkerRemapRequest,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Relink unmapped observations to a biomarker definition.

    Matches observations whose stored ``code.text`` equals ``source_name``
    (case-insensitive) and which currently have no (or stale) ``biomarker_id``.
    Optionally scoped to a single patient. This lets users take raw, unmapped
    lab results visible in a biomarker list and attach them to an existing or
    newly-created definition so they chart under the canonical identity.
    """
    # Verify the target definition exists
    target_res = await db.execute(
        select(BiomarkerDefinition).where(BiomarkerDefinition.id == biomarker_id)
    )
    if not target_res.scalar_one_or_none():
        raise HTTPException(
            status_code=404, detail="Target biomarker definition not found"
        )

    # Build the match conditions: same tenant, code.text matches source_name,
    # biomarker_id is null (genuinely unmapped), optional patient scope.
    conditions = [
        Observation.tenant_id == current_user.tenant_id,
        Observation.biomarker_id.is_(None),
        Observation.code["text"].astext.ilike(payload.source_name),
    ]
    if payload.patient_id:
        conditions.append(
            Observation.subject["reference"].astext == f"Patient/{payload.patient_id}"
        )

    result = await db.execute(
        sa_update(Observation).where(*conditions).values(biomarker_id=biomarker_id)
    )
    updated = result.rowcount or 0
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("biomarker operation failed")
        raise HTTPException(
            status_code=400,
            detail="Request could not be completed (see server log).",
        )

    return {
        "status": "success",
        "biomarker_id": str(biomarker_id),
        "observations_remapped": updated,
    }


@router.patch("/{biomarker_id}", response_model=BiomarkerResponse)
async def update_biomarker(
    biomarker_id: UUID,
    biomarker_update: BiomarkerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Update a biomarker definition. Global rows require SYSTEM_ADMIN."""
    result = await db.execute(
        select(BiomarkerDefinition).where(
            BiomarkerDefinition.id == biomarker_id,
            _tenant_scope(current_user.tenant_id),
        )
    )
    db_biomarker = result.scalar_one_or_none()

    if not db_biomarker:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    DEFAULT_CATALOG_POLICY.check_modify(
        current_user.role,
        db_biomarker.scope,
        item_created_by=db_biomarker.created_by,
        actor_user_id=current_user.user_id,
    )

    old_is_telemetry = db_biomarker.is_telemetry

    # Extract ``allowed_states`` BEFORE ``model_dump`` flattens the nested
    # AllowedStateSpec instances to dicts — we want the Pydantic models so
    # ``_resolve_state_slugs`` can read ``.state_slug`` cleanly.
    new_allowed_states = (
        list(biomarker_update.allowed_states)
        if biomarker_update.allowed_states is not None
        else None
    )

    # Update fields
    update_data = biomarker_update.model_dump(exclude_unset=True)

    # ``allowed_states`` is handled specially below (replace the join set),
    # and ``value_type`` is rejected by the Pydantic schema (cannot be flipped
    # without dropping + recreating the definition — would strand observations).
    update_data.pop("allowed_states", None)

    new_is_telemetry = update_data.get("is_telemetry")
    needs_migration = (
        new_is_telemetry is not None and old_is_telemetry != new_is_telemetry
    )

    # Hard guard (plan state-biomarkers Step 6/11): STATE biomarkers cannot
    # be telemetry — ``telemetry_data.value`` is Float NOT NULL. The Pydantic
    # schema catches the simultaneous case; this catches the toggle-on-an-
    # already-STATE-biomarker case.
    if (
        needs_migration
        and new_is_telemetry is True
        and db_biomarker.value_type == BiomarkerValueType.STATE
    ):
        raise HTTPException(
            status_code=400,
            detail="STATE biomarkers cannot be telemetry (categorical values "
            "have nowhere to go on telemetry_data.value Float NOT NULL).",
        )

    # ``class_concept_id`` (the FK) is authoritative. ``category`` is a
    # read-only property — if a caller sends only the legacy string, resolve
    # it to a concept ID (best-effort) and drop the property name.
    if "class_concept_id" not in update_data and "category" in update_data:
        class_concept_id = await resolve_biomarker_class_concept(
            db, update_data.pop("category"), tenant_id=current_user.tenant_id
        )
        update_data["class_concept_id"] = class_concept_id
    elif "category" in update_data:
        # An explicit ``class_concept_id`` was provided — drop the legacy
        # property name so it doesn't trip ``setattr`` below.
        update_data.pop("category")

    for key, value in update_data.items():
        setattr(db_biomarker, key, value)

    # STATE biomarkers: replace the allowed_states join set atomically when
    # the caller supplied one. QUANTITY biomarkers reject this at the schema
    # layer (BiomarkerUpdate.value_type can't be flipped, and QUANTITY rows
    # have no allowed_states to replace).
    if new_allowed_states is not None:
        if db_biomarker.value_type != BiomarkerValueType.STATE:
            raise HTTPException(
                status_code=400,
                detail="allowed_states can only be set on STATE biomarkers",
            )
        # Delete existing join rows, then insert the new set. Direct row
        # insertion avoids lazy-loading ``db_biomarker.allowed_states`` (a
        # greenlet error in async context).
        await db.execute(
            delete(BiomarkerAllowedState).where(
                BiomarkerAllowedState.biomarker_id == db_biomarker.id
            )
        )
        for row in await _resolve_state_slugs(db, new_allowed_states):
            row.biomarker_id = db_biomarker.id
            db.add(row)

    try:
        if needs_migration:
            # We set the initial state to in_progress
            meta = dict(db_biomarker.meta_data or {})
            meta["migration_status"] = "in_progress"
            meta["migration_progress"] = 0
            if "migration_error" in meta:
                del meta["migration_error"]
            db_biomarker.meta_data = meta

            # Need to flagged the JSONB column as modified
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(db_biomarker, "meta_data")

            # Trigger celery task
            from app.workers.tasks import migrate_biomarker_data

            migrate_biomarker_data.delay(
                str(db_biomarker.id),
                str(current_user.tenant_id),
                bool(new_is_telemetry),
            )

        await db.commit()
        # Reload with eager-loaded relationships (allowed_states, etc.) —
        # ``db.refresh`` doesn't repopulate selectin chains and accessing
        # them in async context triggers a greenlet error.
        db_biomarker = await _reload_biomarker(db, db_biomarker.id)

        # Return with symbol
        u_res = await db.execute(
            select(Unit.symbol).where(Unit.id == db_biomarker.preferred_unit_id)
        )
        symbol = u_res.scalar_one_or_none()

        return _serialize_biomarker(db_biomarker, symbol)
    except Exception:
        await db.rollback()
        logger.exception("biomarker operation failed")
        raise HTTPException(
            status_code=400,
            detail="Request could not be completed (see server log).",
        )


# ---------------------------------------------------------------------------
# Stratified reference ranges (audit B9 / F3)
#
# Nested under the parent biomarker. Access is INHERITED from the parent:
#   - read  → parent visible to the caller (global + tenant);
#   - write → caller may modify the parent (``DEFAULT_CATALOG_POLICY.check_modify``).
# A stratified row only makes sense alongside its biomarker, so there is no
# top-level ``/reference-ranges`` collection — always
# ``/biomarkers/{biomarker_id}/reference-ranges``.
# ---------------------------------------------------------------------------


async def _load_parent_biomarker(
    biomarker_id: UUID, current_user: TokenData, db: AsyncSession
) -> BiomarkerDefinition:
    """Load a biomarker visible to the caller (global + tenant) or 404."""
    result = await db.execute(
        select(BiomarkerDefinition).where(
            BiomarkerDefinition.id == biomarker_id,
            _tenant_scope(current_user.tenant_id),
        )
    )
    bio = result.scalar_one_or_none()
    if bio is None:
        raise HTTPException(status_code=404, detail="Biomarker not found")
    return bio


@router.get(
    "/{biomarker_id}/reference-ranges",
    response_model=List[BiomarkerReferenceRangeResponse],
)
async def list_reference_ranges(
    biomarker_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """List all stratified reference ranges for a biomarker (most-specific last)."""
    await _load_parent_biomarker(biomarker_id, current_user, db)
    result = await db.execute(
        select(BiomarkerReferenceRange)
        .where(BiomarkerReferenceRange.biomarker_id == biomarker_id)
        .order_by(
            BiomarkerReferenceRange.sex.asc(),
            BiomarkerReferenceRange.age_min.asc(),
            BiomarkerReferenceRange.unit_id.asc(),
        )
    )
    return result.scalars().all()


@router.post(
    "/{biomarker_id}/reference-ranges",
    response_model=BiomarkerReferenceRangeResponse,
    status_code=201,
)
async def create_reference_range(
    biomarker_id: UUID,
    payload: BiomarkerReferenceRangeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Add a stratified reference range. Write access inherited from the parent."""
    bio = await _load_parent_biomarker(biomarker_id, current_user, db)
    DEFAULT_CATALOG_POLICY.check_modify(
        current_user.role,
        bio.scope,
        item_created_by=bio.created_by,
        actor_user_id=current_user.user_id,
    )
    new_range = BiomarkerReferenceRange(
        biomarker_id=biomarker_id,
        sex=payload.sex,
        age_min=payload.age_min,
        age_max=payload.age_max,
        unit_id=payload.unit_id,
        low=payload.low,
        high=payload.high,
        text=payload.text,
        applies_to=payload.applies_to,
        created_by=current_user.user_id,
        updated_by=current_user.user_id,
    )
    db.add(new_range)
    try:
        await db.commit()
        await db.refresh(new_range)
        return new_range
    except Exception:
        await db.rollback()
        logger.exception("reference-range create failed")
        raise HTTPException(
            status_code=400,
            detail="Request could not be completed (see server log).",
        )


@router.put(
    "/{biomarker_id}/reference-ranges/{range_id}",
    response_model=BiomarkerReferenceRangeResponse,
)
async def update_reference_range(
    biomarker_id: UUID,
    range_id: UUID,
    payload: BiomarkerReferenceRangeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Update a stratified reference range. Write access inherited from the parent."""
    bio = await _load_parent_biomarker(biomarker_id, current_user, db)
    DEFAULT_CATALOG_POLICY.check_modify(
        current_user.role,
        bio.scope,
        item_created_by=bio.created_by,
        actor_user_id=current_user.user_id,
    )
    result = await db.execute(
        select(BiomarkerReferenceRange).where(
            BiomarkerReferenceRange.id == range_id,
            BiomarkerReferenceRange.biomarker_id == biomarker_id,
        )
    )
    db_range = result.scalar_one_or_none()
    if db_range is None:
        raise HTTPException(status_code=404, detail="Reference range not found")

    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(db_range, key, value)
    db_range.updated_by = current_user.user_id
    try:
        await db.commit()
        await db.refresh(db_range)
        return db_range
    except Exception:
        await db.rollback()
        logger.exception("reference-range update failed")
        raise HTTPException(
            status_code=400,
            detail="Request could not be completed (see server log).",
        )


@router.delete("/{biomarker_id}/reference-ranges/{range_id}")
async def delete_reference_range(
    biomarker_id: UUID,
    range_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Delete a stratified reference range. Write access inherited from the parent."""
    bio = await _load_parent_biomarker(biomarker_id, current_user, db)
    DEFAULT_CATALOG_POLICY.check_modify(
        current_user.role,
        bio.scope,
        item_created_by=bio.created_by,
        actor_user_id=current_user.user_id,
    )
    result = await db.execute(
        select(BiomarkerReferenceRange).where(
            BiomarkerReferenceRange.id == range_id,
            BiomarkerReferenceRange.biomarker_id == biomarker_id,
        )
    )
    db_range = result.scalar_one_or_none()
    if db_range is None:
        raise HTTPException(status_code=404, detail="Reference range not found")
    await db.delete(db_range)
    try:
        await db.commit()
        return {"status": "success", "message": "Reference range deleted"}
    except Exception:
        await db.rollback()
        logger.exception("reference-range delete failed")
        raise HTTPException(
            status_code=400,
            detail="Request could not be completed (see server log).",
        )
