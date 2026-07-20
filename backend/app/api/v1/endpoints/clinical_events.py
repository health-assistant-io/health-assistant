import datetime as _dt
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.biomarker_model import (
    BiomarkerDefinition,
    Unit,
)
from app.models.clinical_event import ClinicalEventType
from app.models.concept_model import ConceptEdge
from app.models.enums import (
    ClinicalEventStatus,
    ConceptRelationType,
    EdgeApprovalStatus,
    EdgeEndpointType,
)
from app.schemas.biomarker import BiomarkerResponse
from app.schemas.clinical_event import (
    BiomarkerCorrelationCreate,
    ClinicalEventCreate,
    ClinicalEventOccurrenceCreate,
    ClinicalEventResponse,
    ClinicalEventTypeCreate,
    ClinicalEventTypeResponse,
    ClinicalEventUpdate,
    EventAnatomyLinkCreate,
    EventExaminationLinkBase,
    EventObservationLinkBase,
)
from app.schemas.user import TokenData
from app.services import clinical_event_service as ce_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clinical-events", tags=["clinical-events"])


# ---------------------------------------------------------------------------
# Event-type catalog (read/create) — these are simple and stay inline.
# ---------------------------------------------------------------------------


@router.get("/types", response_model=List[ClinicalEventTypeResponse])
async def list_event_types(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ClinicalEventType)
        .where(
            or_(
                ClinicalEventType.tenant_id == current_user.tenant_id,
                ClinicalEventType.tenant_id.is_(None),
            )
        )
        .options(selectinload(ClinicalEventType.category_concept))
    )
    return result.scalars().all()


@router.get("/types/{type_id}/biomarkers", response_model=List[BiomarkerResponse])
async def get_correlated_biomarkers(
    type_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all biomarkers correlated with this clinical event type.

    Resolved from the concept_edges graph (biomarker MONITORS clinical_event_type)
    — replaces the legacy BiomarkerEventCorrelation table (Phase 3).
    """
    edge_rows = (
        await db.execute(
            select(ConceptEdge.src_id).where(
                ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                ConceptEdge.dst_type == EdgeEndpointType.CLINICAL_EVENT_TYPE,
                ConceptEdge.dst_id == type_id,
                ConceptEdge.relation == ConceptRelationType.MONITORS,
                ConceptEdge.status == EdgeApprovalStatus.APPROVED,
            )
        )
    ).scalars().all()

    response = []
    if edge_rows:
        bio_rows = (
            await db.execute(
                select(BiomarkerDefinition, Unit.symbol.label("unit_symbol"))
                .outerjoin(
                    Unit, BiomarkerDefinition.preferred_unit_id == Unit.id
                )
                .where(BiomarkerDefinition.id.in_(edge_rows))
            )
        ).all()
        for bio, symbol in bio_rows:
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


@router.post("/types/{type_id}/biomarkers")
async def add_correlated_biomarker(
    type_id: UUID,
    payload: BiomarkerCorrelationCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bind a biomarker to an event type (idempotent on the pair).

    Previously correlations were seed-script-only; this lets an admin manage
    them via the API. The ClinicalEventEngine's ``recommended_biomarkers``
    insight reads from these correlations.
    """
    return await ce_service.add_correlated_biomarker(
        db,
        type_id,
        payload.biomarker_id,
        correlation_type=payload.correlation_type,
        description=payload.description,
    )


@router.delete("/types/{type_id}/biomarkers/{biomarker_id}")
async def remove_correlated_biomarker(
    type_id: UUID,
    biomarker_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a biomarker ↔ event-type correlation."""
    await ce_service.remove_correlated_biomarker(db, type_id, biomarker_id)
    return {"message": "Correlation removed"}


@router.post("/types", response_model=ClinicalEventTypeResponse)
async def create_event_type(
    type_in: ClinicalEventTypeCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Slug is globally unique (no tenant scoping) — check across all tenants.
    existing = await db.execute(
        select(ClinicalEventType).where(ClinicalEventType.slug == type_in.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Event type slug already exists")

    new_type = ClinicalEventType(
        **type_in.model_dump(), tenant_id=current_user.tenant_id
    )
    db.add(new_type)
    await db.commit()
    await db.refresh(new_type)
    return new_type


# ---------------------------------------------------------------------------
# Event-instance CRUD — thin HTTP adapters over ClinicalEventService.
# ---------------------------------------------------------------------------


@router.get("", response_model=List[ClinicalEventResponse])
async def list_events(
    patient_id: Optional[UUID] = None,
    examination_id: Optional[UUID] = None,
    status: Optional[ClinicalEventStatus] = None,
    active_on: Optional[_dt.date] = None,
    onset_on: Optional[_dt.date] = None,
    date_range: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List clinical events, tenant-scoped, paginated, soft-deletes excluded.

    Date filters:

    - ``active_on=YYYY-MM-DD`` — events active on that calendar day (state events
      with no resolved_date match if ``onset_date <= day``). Powers
      "what was happening to this patient on X?" queries.
    - ``onset_on=YYYY-MM-DD`` — events whose ``onset_date`` is on that day.
    - ``date_range=YYYY-MM-DD,YYYY-MM-DD`` — events whose interval overlaps
      the given range.
    """
    return await ce_service.list_events(
        db,
        current_user,
        patient_id=patient_id,
        examination_id=examination_id,
        status=status,
        active_on=active_on,
        onset_on=onset_on,
        date_range=date_range,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ClinicalEventResponse)
async def create_event(
    event_in: ClinicalEventCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ce_service.create_event(db, current_user, event_in)


@router.get("/{event_id}", response_model=ClinicalEventResponse)
async def get_event(
    event_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ce_service.get_event(db, event_id, current_user)


@router.put("/{event_id}", response_model=ClinicalEventResponse)
async def update_event(
    event_id: UUID,
    event_in: ClinicalEventUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ce_service.update_event(db, event_id, current_user, event_in)


@router.delete("/{event_id}")
async def delete_event(
    event_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete (tombstone) the event and emit the deletion notification."""
    await ce_service.soft_delete_event(db, event_id, current_user)
    return {"message": "Clinical event deleted successfully"}


@router.post("/{event_id}/link-examination", response_model=ClinicalEventResponse)
async def link_examination(
    event_id: UUID,
    link_in: EventExaminationLinkBase,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Link a single examination to an event (rejects duplicates)."""
    return await ce_service.link_examination(
        db, event_id, current_user, link_in.examination_id, link_in.reason
    )


@router.post("/{event_id}/link-observation", response_model=ClinicalEventResponse)
async def link_observation(
    event_id: UUID,
    link_in: EventObservationLinkBase,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Link a single observation to an event (rejects duplicates).

    Closes the asymmetry with ``link-examination``: previously observation
    links were only manageable through the create/update full-replace payload.
    """
    return await ce_service.link_observation(
        db, event_id, current_user, link_in.observation_id, link_in.notes
    )


@router.post("/{event_id}/occurrences", response_model=ClinicalEventResponse)
async def add_occurrence(
    event_id: UUID,
    occurrence_in: ClinicalEventOccurrenceCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Append a discrete occurrence (episode) to a health journey."""
    return await ce_service.add_occurrence(
        db, event_id, current_user, occurrence_in
    )


@router.delete(
    "/{event_id}/occurrences/{occurrence_id}", response_model=ClinicalEventResponse
)
async def delete_occurrence(
    event_id: UUID,
    occurrence_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a single occurrence from a journey."""
    return await ce_service.delete_occurrence(
        db, event_id, occurrence_id, current_user
    )


@router.post("/{event_id}/link-anatomy", response_model=ClinicalEventResponse)
async def link_anatomy(
    event_id: UUID,
    link_in: EventAnatomyLinkCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Link an anatomy site to an event (rejects duplicates).

    ``relation_type`` distinguishes ``primary_site`` / ``radiates_to`` /
    ``referred_to``. Promotes the previously-dead ``EventAnatomyLink`` table
    to the structured anatomy path (anatomy was tracked ad-hoc in
    ``event_metadata.body_part_id`` JSONB before).
    """
    return await ce_service.link_anatomy(
        db, event_id, current_user, link_in.anatomy_id, link_in.relation_type
    )


@router.delete(
    "/{event_id}/unlink-anatomy/{anatomy_id}", response_model=ClinicalEventResponse
)
async def unlink_anatomy(
    event_id: UUID,
    anatomy_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove an anatomy link from an event."""
    return await ce_service.unlink_anatomy(
        db, event_id, anatomy_id, current_user
    )


@router.get("/{event_id}/insights")
async def get_event_insights(
    event_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Type-driven journey insights: current phase, upcoming/overdue milestones,
    recommended biomarkers, and an overdue flag. Pure-computed — no persistence.

    This is the "behavior-driving types" surface (Phase 4a): a journey type's
    ``phases``/``milestones``/``default_duration_days`` JSONB templates drive
    computed guidance without engine code changes per type.
    """
    return await ce_service.get_insights(db, event_id, current_user)
