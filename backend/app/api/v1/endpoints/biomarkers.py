import logging
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import delete, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogs.policy import DEFAULT_CATALOG_POLICY, CatalogUniqueViolation
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.biomarker_model import (
    BiomarkerDefinition,
    BiomarkerReferenceRange,
    BiomarkerState,
    Unit,
)
from app.models.enums import Role
from app.models.fhir.patient import Observation
from app.schemas.biomarker import (
    BiomarkerCreate,
    BiomarkerReferenceRangeCreate,
    BiomarkerReferenceRangeResponse,
    BiomarkerReferenceRangeUpdate,
    BiomarkerRemapRequest,
    BiomarkerResponse,
    BiomarkerStateResponse,
    BiomarkerUpdate,
    UnitCreate,
    UnitResponse,
)
from app.schemas.user import TokenData
from app.services.catalog import catalog_write_service as w
from app.services.seed.provenance import mark_user_modified_for_model

router = APIRouter(prefix="/biomarkers", tags=["biomarkers"])

logger = logging.getLogger(__name__)


def _tenant_scope(tenant_id):
    """Standard global+tenant read filter for biomarker definitions."""
    return or_(
        BiomarkerDefinition.tenant_id.is_(None),
        BiomarkerDefinition.tenant_id == tenant_id,
    )


def _serialize_biomarker(bio: BiomarkerDefinition, viewer_tenant_id: UUID | None) -> dict:
    """The unified biomarker response shape (single source:
    :func:`catalog_write_service.serialize_biomarker`); ``viewer_tenant_id``
    drives the class-concept display projection."""
    return w.serialize_biomarker(bio, viewer_tenant_id=viewer_tenant_id)


# TODO: Add endpoint /api/v1/biomarkers/correlated for querying by organ/symptom (internal development plan)  # noqa: E501 -- long template/message string; reflow when touched
# TODO: Add endpoints to retrieve correlated biomarkers for a given clinical event (internal development plan)  # noqa: E501 -- long template/message string; reflow when touched


@router.get("/", response_model=list[BiomarkerResponse])
async def get_biomarkers(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get all biomarker definitions visible to the caller (global + tenant)."""
    result = await db.execute(
        select(BiomarkerDefinition)
        .where(_tenant_scope(current_user.tenant_id))
        .order_by(BiomarkerDefinition.name)
    )
    rows = result.scalars().all()

    return [w.serialize_biomarker(bio, viewer_tenant_id=current_user.tenant_id) for bio in rows]


@router.get("/units", response_model=list[UnitResponse])
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
    await db.flush()
    # Human write path (ADR 0056 freeze): a user-created unit is never
    # auto-updated by a later pack that targets the same symbol.
    await mark_user_modified_for_model(db, Unit, new_unit.id)
    try:
        await db.commit()
        await db.refresh(new_unit)
        return new_unit
    except Exception:
        await db.rollback()
        logger.exception("biomarker operation failed")
        raise HTTPException(  # noqa: B904 -- bare raise; add explicit chaining when touched
            status_code=400,
            detail="Request could not be completed (see server log).",
        )


@router.get("/states", response_model=list[BiomarkerStateResponse])
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


@router.post("/", response_model=BiomarkerResponse, status_code=201)
async def create_biomarker(
    biomarker: BiomarkerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create a biomarker definition (scope derived from role).

    Thin entry point onto the unified write contract — the same service the
    generic ``POST /catalogs/biomarker`` route calls (unit symbol → id,
    category/class slug → class concept, reference_ranges, allowed_states).
    """
    try:
        return await w.create_biomarker(db, current_user, biomarker)
    except CatalogUniqueViolation:
        raise  # global handler → 409
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.delete("/{biomarker_id}")
async def delete_biomarker(
    biomarker_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Delete a biomarker definition. Global rows require SYSTEM_ADMIN."""
    deleted = await w.delete_biomarker(db, current_user, biomarker_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Biomarker not found")
    return {"status": "success", "message": "biomarker item deleted"}


@router.post("/bulk-delete")
async def bulk_delete_biomarkers(
    biomarker_ids: list[UUID] = Body(..., embed=True),
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
        raise HTTPException(  # noqa: B904 -- bare raise; add explicit chaining when touched
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
        select(BiomarkerDefinition).where(
            BiomarkerDefinition.slug == slug, _tenant_scope(current_user.tenant_id)
        )
    )
    bio = result.scalar_one_or_none()

    if not bio:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    return _serialize_biomarker(bio, current_user.tenant_id)


@router.get("/{biomarker_id}", response_model=BiomarkerResponse)
async def get_biomarker_by_id(
    biomarker_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get a single biomarker definition by its ID"""
    result = await db.execute(
        select(BiomarkerDefinition).where(
            BiomarkerDefinition.id == biomarker_id,
            _tenant_scope(current_user.tenant_id),
        )
    )
    bio = result.scalar_one_or_none()

    if not bio:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    return _serialize_biomarker(bio, current_user.tenant_id)


@router.post("/{biomarker_id}/retry-migration", response_model=BiomarkerResponse)
async def retry_biomarker_migration(
    biomarker_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Retry a stuck or failed biomarker data migration"""
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

    meta = dict(db_biomarker.meta_data or {})

    # We only allow retrying if it actually was marked as in progress or failed
    if meta.get("migration_status") not in ["failed", "in_progress"]:
        raise HTTPException(status_code=400, detail="No active or failed migration to retry")

    meta["migration_status"] = "in_progress"
    meta["migration_progress"] = 0
    meta.pop("migration_error", None)

    db_biomarker.meta_data = meta

    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(db_biomarker, "meta_data")

    try:
        await db.commit()
        db_biomarker = await w.reload_biomarker(db, db_biomarker.id)
    except Exception:
        await db.rollback()
        logger.exception("biomarker operation failed")
        raise HTTPException(  # noqa: B904 -- bare raise; add explicit chaining when touched
            status_code=400,
            detail="Request could not be completed (see server log).",
        )

    # Trigger celery task again using current is_telemetry state
    from app.workers.tasks import migrate_biomarker_data

    migrate_biomarker_data.delay(
        str(db_biomarker.id),
        str(current_user.tenant_id),
        bool(db_biomarker.is_telemetry),
    )

    return _serialize_biomarker(db_biomarker, current_user.tenant_id)


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
    Optionally scoped to a single patient. This is a bulk catalog write over
    the whole tenant, so it requires MANAGER or above (content management,
    not a patient-level action).
    """
    if current_user.role not in (
        Role.MANAGER.value,
        Role.ADMIN.value,
        Role.SYSTEM_ADMIN.value,
    ):
        raise HTTPException(
            status_code=403,
            detail="Only MANAGER or ADMIN may remap observations.",
        )

    # Verify the target definition is visible in the caller's tenant.
    target_res = await db.execute(
        select(BiomarkerDefinition).where(
            BiomarkerDefinition.id == biomarker_id,
            _tenant_scope(current_user.tenant_id),
        )
    )
    db_target = target_res.scalar_one_or_none()
    if not db_target:
        raise HTTPException(status_code=404, detail="Target biomarker definition not found")
    DEFAULT_CATALOG_POLICY.check_modify(
        current_user.role,
        db_target.scope,
        item_created_by=db_target.created_by,
        actor_user_id=current_user.user_id,
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
        raise HTTPException(  # noqa: B904 -- bare raise; add explicit chaining when touched
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
    """Update a biomarker definition. Global rows require SYSTEM_ADMIN.

    Thin entry point onto the unified write contract — the same service the
    generic ``PUT /catalogs/biomarker/{id}`` route calls.
    """
    try:
        result = await w.update_biomarker(db, current_user, biomarker_id, biomarker_update)
    except CatalogUniqueViolation:
        raise  # global handler → 409
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Biomarker not found")
    return result


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
    response_model=list[BiomarkerReferenceRangeResponse],
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
        raise HTTPException(  # noqa: B904 -- bare raise; add explicit chaining when touched
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
        raise HTTPException(  # noqa: B904 -- bare raise; add explicit chaining when touched
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
        raise HTTPException(  # noqa: B904 -- bare raise; add explicit chaining when touched
            status_code=400,
            detail="Request could not be completed (see server log).",
        )
