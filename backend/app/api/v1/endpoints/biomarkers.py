from typing import List, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.models.biomarker_model import (
    BiomarkerDefinition,
    BiomarkerGroup,
    BiomarkerGroupMember,
    Unit,
)
from app.schemas.biomarker import (
    BiomarkerCreate,
    BiomarkerUpdate,
    BiomarkerResponse,
    UnitResponse,
    UnitCreate,
)
from uuid import UUID

router = APIRouter(prefix="/biomarkers", tags=["biomarkers"])

# TODO: Add endpoint /api/v1/biomarkers/correlated for querying by organ/symptom (from DEVELOPMENT_PLAN.md)
# TODO: Add endpoints to retrieve correlated biomarkers for a given clinical event (from DEVELOPMENT_PLAN.md)


@router.get("/", response_model=List[BiomarkerResponse])
async def get_biomarkers(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get all biomarker definitions"""
    result = await db.execute(
        select(BiomarkerDefinition, Unit.symbol.label("unit_symbol"))
        .outerjoin(Unit, BiomarkerDefinition.preferred_unit_id == Unit.id)
        .order_by(BiomarkerDefinition.name)
    )
    rows = result.all()

    response = []
    for bio, symbol in rows:
        bio_dict = {
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
            "meta_data": bio.meta_data,
            "preferred_unit_symbol": symbol,
        }
        response.append(bio_dict)

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
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/groups")
async def get_groups(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Get biomarker groups and their members"""
    result = await db.execute(select(BiomarkerGroup))
    groups = result.scalars().all()

    response = []
    for g in groups:
        mem_result = await db.execute(
            select(BiomarkerDefinition)
            .join(BiomarkerGroupMember)
            .where(BiomarkerGroupMember.group_id == str(g.id))
            .order_by(BiomarkerGroupMember.display_order)
        )
        members = mem_result.scalars().all()
        response.append(
            {
                "id": g.id,
                "name": g.name,
                "type": g.type,
                "members": [
                    {
                        "id": m.id,
                        "slug": m.slug,
                        "name": m.name,
                        "category": m.category,
                        "aliases": m.aliases,
                        "info": m.info,
                    }
                    for m in members
                ],
            }
        )
    return response


@router.post("/", response_model=BiomarkerResponse)
async def create_biomarker(
    biomarker: BiomarkerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Create a new custom biomarker definition"""
    # Find unit
    unit_id = biomarker.preferred_unit_id
    if not unit_id and biomarker.preferred_unit_symbol:
        u_result = await db.execute(
            select(Unit).where(Unit.symbol == biomarker.preferred_unit_symbol)
        )
        unit = u_result.scalar_one_or_none()
        if unit:
            unit_id = unit.id

    new_bio = BiomarkerDefinition(
        slug=biomarker.slug,
        name=biomarker.name,
        category=biomarker.category,
        aliases=biomarker.aliases,
        info=biomarker.info,
        reference_range_min=biomarker.reference_range_min,
        reference_range_max=biomarker.reference_range_max,
        is_telemetry=biomarker.is_telemetry,
        preferred_unit_id=unit_id,
        tenant_id=current_user.tenant_id,
    )
    db.add(new_bio)
    try:
        await db.commit()
        await db.refresh(new_bio)
        
        # Get unit symbol
        symbol = None
        if new_bio.preferred_unit_id:
            u_res = await db.execute(select(Unit.symbol).where(Unit.id == new_bio.preferred_unit_id))
            symbol = u_res.scalar_one_or_none()
            
        return {
            "id": new_bio.id,
            "slug": new_bio.slug,
            "coding_system": new_bio.coding_system,
            "code": new_bio.code,
            "name": new_bio.name,
            "category": new_bio.category,
            "aliases": new_bio.aliases,
            "preferred_unit_id": new_bio.preferred_unit_id,
            "info": new_bio.info,
            "reference_range_min": new_bio.reference_range_min,
            "reference_range_max": new_bio.reference_range_max,
            "is_telemetry": new_bio.is_telemetry,
            "meta_data": new_bio.meta_data,
            "preferred_unit_symbol": symbol,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{biomarker_id}")
async def delete_biomarker(
    biomarker_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Delete a biomarker definition"""
    result = await db.execute(
        select(BiomarkerDefinition).where(BiomarkerDefinition.id == biomarker_id)
    )
    db_biomarker = result.scalar_one_or_none()

    if not db_biomarker:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    await db.delete(db_biomarker)
    try:
        await db.commit()
        return {"status": "success", "message": "Biomarker deleted"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/bulk-delete")
async def bulk_delete_biomarkers(
    biomarker_ids: List[UUID] = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Bulk delete biomarker definitions"""
    try:
        await db.execute(
            delete(BiomarkerDefinition).where(BiomarkerDefinition.id.in_(biomarker_ids))
        )
        await db.commit()
        return {
            "status": "success",
            "message": f"{len(biomarker_ids)} biomarkers deleted",
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


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
        .where(BiomarkerDefinition.slug == slug)
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    bio, symbol = row
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
        "meta_data": bio.meta_data,
        "preferred_unit_symbol": symbol,
    }


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
        .where(BiomarkerDefinition.id == biomarker_id)
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    bio, symbol = row
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
        "meta_data": bio.meta_data,
        "preferred_unit_symbol": symbol,
    }


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
        raise HTTPException(status_code=400, detail="No active or failed migration to retry")
        
    meta["migration_status"] = "in_progress"
    meta["migration_progress"] = 0
    if "migration_error" in meta:
        del meta["migration_error"]
        
    db_biomarker.meta_data = meta
    
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(db_biomarker, "meta_data")

    # Trigger celery task again using current is_telemetry state
    from app.workers.tasks import migrate_biomarker_data
    migrate_biomarker_data.delay(str(db_biomarker.id), str(current_user.tenant_id), bool(db_biomarker.is_telemetry))

    try:
        await db.commit()
        await db.refresh(db_biomarker)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    # Return with symbol
    u_res = await db.execute(
        select(Unit.symbol).where(Unit.id == db_biomarker.preferred_unit_id)
    )
    symbol = u_res.scalar_one_or_none()

    return {
        "id": db_biomarker.id,
        "slug": db_biomarker.slug,
        "coding_system": db_biomarker.coding_system,
        "code": db_biomarker.code,
        "name": db_biomarker.name,
        "category": db_biomarker.category,
        "aliases": db_biomarker.aliases,
        "preferred_unit_id": db_biomarker.preferred_unit_id,
        "info": db_biomarker.info,
        "reference_range_min": db_biomarker.reference_range_min,
        "reference_range_max": db_biomarker.reference_range_max,
        "is_telemetry": db_biomarker.is_telemetry,
        "meta_data": db_biomarker.meta_data,
        "preferred_unit_symbol": symbol,
    }

@router.patch("/{biomarker_id}", response_model=BiomarkerResponse)
async def update_biomarker(
    biomarker_id: UUID,
    biomarker_update: BiomarkerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Update a biomarker definition"""
    result = await db.execute(
        select(BiomarkerDefinition).where(BiomarkerDefinition.id == biomarker_id)
    )
    db_biomarker = result.scalar_one_or_none()

    if not db_biomarker:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    old_is_telemetry = db_biomarker.is_telemetry
    
    # Update fields
    update_data = biomarker_update.model_dump(exclude_unset=True)
    
    new_is_telemetry = update_data.get("is_telemetry")
    needs_migration = new_is_telemetry is not None and old_is_telemetry != new_is_telemetry
    
    for key, value in update_data.items():
        setattr(db_biomarker, key, value)

    try:
        if needs_migration:
            # We set the initial state to in_progress
            meta = dict(db_biomarker.meta_data or {})
            meta["migration_status"] = "in_progress"
            meta["migration_progress"] = 0
            if "migration_error" in meta:
                del meta["migration_error"]
            db_biomarker.meta_data = meta
            
            # Need to flag the JSONB column as modified
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(db_biomarker, "meta_data")
            
            # Trigger celery task
            from app.workers.tasks import migrate_biomarker_data
            migrate_biomarker_data.delay(str(db_biomarker.id), str(current_user.tenant_id), bool(new_is_telemetry))

        await db.commit()
        await db.refresh(db_biomarker)

        # Return with symbol
        u_res = await db.execute(
            select(Unit.symbol).where(Unit.id == db_biomarker.preferred_unit_id)
        )
        symbol = u_res.scalar_one_or_none()

        return {
            "id": db_biomarker.id,
            "slug": db_biomarker.slug,
            "coding_system": db_biomarker.coding_system,
            "code": db_biomarker.code,
            "name": db_biomarker.name,
            "category": db_biomarker.category,
            "aliases": db_biomarker.aliases,
            "preferred_unit_id": db_biomarker.preferred_unit_id,
            "info": db_biomarker.info,
            "reference_range_min": db_biomarker.reference_range_min,
            "reference_range_max": db_biomarker.reference_range_max,
            "is_telemetry": db_biomarker.is_telemetry,
            "meta_data": db_biomarker.meta_data,
            "preferred_unit_symbol": symbol,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
