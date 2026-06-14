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
            import logging
            from app.models.fhir.patient import Observation, Patient
            from app.models.telemetry_model import TelemetryDataModel
            
            logger = logging.getLogger(__name__)
            
            if new_is_telemetry is True:
                # Migrate FHIR -> Telemetry
                obs_res = await db.execute(
                    select(Observation).where(Observation.biomarker_id == db_biomarker.id)
                )
                observations = obs_res.scalars().all()
                
                if observations:
                    telemetry_records = []
                    for obs in observations:
                        slug = db_biomarker.slug.lower() if db_biomarker.slug else ""
                        val = getattr(obs, "normalized_value", None) or getattr(obs, "raw_value", None) or (obs.value_quantity.get("value") if getattr(obs, "value_quantity", None) else None)
                        
                        hr = val if slug == "8867-4" or "heart-rate" in slug else None
                        steps = val if slug == "41950-7" or "steps" in slug else None
                        cal = val if "calories" in slug else None
                        
                        data_payload = {}
                        if not hr and not steps and not cal:
                            data_payload[slug] = val
                            data_payload[f"{slug}_unit"] = obs.value_quantity.get("unit", "") if getattr(obs, "value_quantity", None) else ""

                        telemetry_records.append(TelemetryDataModel(
                            tenant_id=obs.tenant_id,
                            device_id="fhir_migration",
                            timestamp=obs.effective_datetime,
                            heart_rate=hr,
                            steps=steps,
                            calories=cal,
                            data=data_payload if data_payload else None
                        ))
                    
                    db.add_all(telemetry_records)
                    await db.execute(delete(Observation).where(Observation.biomarker_id == db_biomarker.id))
                    
            else:
                # Migrate Telemetry -> FHIR
                slug = db_biomarker.slug.lower() if db_biomarker.slug else ""
                
                # We need to find telemetry records matching this slug
                # This could be tricky because we have to check columns or JSONB data
                stmt = select(TelemetryDataModel).where(TelemetryDataModel.tenant_id == current_user.tenant_id)
                if slug == "8867-4" or "heart-rate" in slug:
                    stmt = stmt.where(TelemetryDataModel.heart_rate.is_not(None))
                elif slug == "41950-7" or "steps" in slug:
                    stmt = stmt.where(TelemetryDataModel.steps.is_not(None))
                elif "calories" in slug:
                    stmt = stmt.where(TelemetryDataModel.calories.is_not(None))
                else:
                    stmt = stmt.where(TelemetryDataModel.data.has_key(slug))
                    
                tel_res = await db.execute(stmt)
                telemetry_records = tel_res.scalars().all()
                
                if telemetry_records:
                    # Get unit symbol
                    u_res = await db.execute(select(Unit.symbol).where(Unit.id == db_biomarker.preferred_unit_id))
                    symbol = u_res.scalar_one_or_none() or ""
                    
                    # Find a patient to attach these to
                    p_res = await db.execute(select(Patient.id).where(Patient.tenant_id == current_user.tenant_id).limit(1))
                    patient_id = p_res.scalar_one_or_none()
                    
                    if patient_id:
                        fhir_records = []
                        for tr in telemetry_records:
                            if slug == "8867-4" or "heart-rate" in slug:
                                val = tr.heart_rate
                                tr.heart_rate = None
                            elif slug == "41950-7" or "steps" in slug:
                                val = tr.steps
                                tr.steps = None
                            elif "calories" in slug:
                                val = tr.calories
                                tr.calories = None
                            else:
                                val = tr.data.get(slug) if tr.data else None
                                if tr.data and slug in tr.data:
                                    del tr.data[slug]
                                    # SQLAlchemy JSONB mutation tracking might need this:
                                    from sqlalchemy.orm.attributes import flag_modified
                                    flag_modified(tr, "data")
                                
                            if val is not None:
                                obs = Observation(
                                    tenant_id=tr.tenant_id,
                                    subject={"reference": f"Patient/{patient_id}"},
                                    status="final",
                                    code={
                                        "coding": [{
                                            "system": db_biomarker.coding_system.value if db_biomarker.coding_system else "http://loinc.org",
                                            "code": db_biomarker.code or db_biomarker.slug,
                                            "display": db_biomarker.name
                                        }],
                                        "text": db_biomarker.name
                                    },
                                    effective_datetime=tr.timestamp,
                                    value_quantity={
                                        "value": float(val) if val is not None else None,
                                        "unit": symbol
                                    },
                                    raw_value=float(val) if val is not None else None,
                                    normalized_value=float(val) if val is not None else None,
                                    biomarker_id=db_biomarker.id
                                )
                                fhir_records.append(obs)
                            
                            # Determine if the record is completely empty and should be deleted
                            is_empty = (
                                tr.heart_rate is None and
                                tr.steps is None and
                                tr.calories is None and
                                (tr.data is None or len(tr.data) == 0)
                            )
                            if is_empty:
                                await db.delete(tr)
                            
                        if fhir_records:
                            db.add_all(fhir_records)
                    else:
                        logger.warning(f"Could not migrate telemetry to FHIR for {db_biomarker.slug} - no patient found in tenant")

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
            "preferred_unit_symbol": symbol,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
