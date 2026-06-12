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
            "name": bio.name,
            "category": bio.category,
            "aliases": bio.aliases,
            "preferred_unit_id": bio.preferred_unit_id,
            "info": bio.info,
            "reference_range_min": bio.reference_range_min,
            "reference_range_max": bio.reference_range_max,
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
        preferred_unit_id=unit_id,
        tenant_id=current_user.tenant_id,
    )
    db.add(new_bio)
    try:
        await db.commit()
        await db.refresh(new_bio)
        return new_bio
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
        "name": bio.name,
        "category": bio.category,
        "aliases": bio.aliases,
        "preferred_unit_id": bio.preferred_unit_id,
        "info": bio.info,
        "reference_range_min": bio.reference_range_min,
        "reference_range_max": bio.reference_range_max,
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
        "name": bio.name,
        "category": bio.category,
        "aliases": bio.aliases,
        "preferred_unit_id": bio.preferred_unit_id,
        "info": bio.info,
        "reference_range_min": bio.reference_range_min,
        "reference_range_max": bio.reference_range_max,
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

    # Update fields
    update_data = biomarker_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_biomarker, key, value)

    try:
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
            "name": db_biomarker.name,
            "category": db_biomarker.category,
            "aliases": db_biomarker.aliases,
            "preferred_unit_id": db_biomarker.preferred_unit_id,
            "info": db_biomarker.info,
            "reference_range_min": db_biomarker.reference_range_min,
            "reference_range_max": db_biomarker.reference_range_max,
            "preferred_unit_symbol": symbol,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
