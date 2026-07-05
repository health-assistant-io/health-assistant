from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_
from typing import List, Optional
from uuid import UUID
from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.user import TokenData
from app.models.clinical_event import (
    ClinicalEvent,
    ClinicalEventCategory,
    ClinicalEventType,
    EventExaminationLink,
    EventObservationLink,
)
from app.models.fhir.patient import Observation
from app.models.biomarker_model import (
    BiomarkerDefinition,
    BiomarkerEventCorrelation,
    Unit,
)
from app.schemas.biomarker import BiomarkerResponse
from app.models.enums import ClinicalEventStatus
from app.schemas.clinical_event import (
    ClinicalEventCreate,
    ClinicalEventUpdate,
    ClinicalEventResponse,
    ClinicalEventCategoryCreate,
    ClinicalEventCategoryResponse,
    ClinicalEventTypeCreate,
    ClinicalEventTypeResponse,
    EventExaminationLinkBase,
)

from app.models.fhir.patient import Patient
from app.models.examination_model import ExaminationModel
from app.schemas.clinical_event import (
    ClinicalEventCreate,
    ClinicalEventUpdate,
    ClinicalEventResponse,
    ClinicalEventTypeCreate,
    ClinicalEventTypeResponse,
    EventExaminationLinkBase,
)
from sqlalchemy.orm import selectinload
from app.api.v1.endpoints.utils import (
    check_patient_access,
    check_event_access,
    check_examination_access,
    check_observation_access,
)
from app.models.enums import Role
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clinical-events", tags=["clinical-events"])


@router.get("/categories", response_model=List[ClinicalEventCategoryResponse])
async def list_event_categories(
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ClinicalEventCategory).where(
            or_(
                ClinicalEventCategory.tenant_id == current_user.tenant_id,
                ClinicalEventCategory.tenant_id.is_(None),
            )
        )
    )
    return result.scalars().all()


@router.post("/categories", response_model=ClinicalEventCategoryResponse)
async def create_event_category(
    category_in: ClinicalEventCategoryCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(ClinicalEventCategory).where(
            ClinicalEventCategory.slug == category_in.slug
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Category slug already exists")

    new_category = ClinicalEventCategory(
        **category_in.model_dump(), tenant_id=current_user.tenant_id
    )
    db.add(new_category)
    await db.commit()
    await db.refresh(new_category)
    return new_category


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
        .options(selectinload(ClinicalEventType.category_entity))
    )
    return result.scalars().all()


@router.get("/types/{type_id}/biomarkers", response_model=List[BiomarkerResponse])
async def get_correlated_biomarkers(
    type_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all biomarkers correlated with this clinical event type"""
    stmt = (
        select(BiomarkerDefinition, Unit.symbol.label("unit_symbol"))
        .join(
            BiomarkerEventCorrelation,
            BiomarkerEventCorrelation.biomarker_id == BiomarkerDefinition.id,
        )
        .outerjoin(Unit, BiomarkerDefinition.preferred_unit_id == Unit.id)
        .where(BiomarkerEventCorrelation.event_type_id == type_id)
    )
    result = await db.execute(stmt)
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


@router.post("/types", response_model=ClinicalEventTypeResponse)
async def create_event_type(
    type_in: ClinicalEventTypeCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Check if slug exists for this tenant or globally
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


@router.get("", response_model=List[ClinicalEventResponse])
async def list_events(
    patient_id: Optional[UUID] = None,
    examination_id: Optional[UUID] = None,
    status: Optional[ClinicalEventStatus] = None,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(ClinicalEvent)
        .where(ClinicalEvent.tenant_id == current_user.tenant_id)
        .options(
            selectinload(ClinicalEvent.type_entity).selectinload(
                ClinicalEventType.category_entity
            ),
            selectinload(ClinicalEvent.examination_links).selectinload(
                EventExaminationLink.examination
            ),
            selectinload(ClinicalEvent.observation_links).selectinload(
                EventObservationLink.observation
            ).selectinload(Observation.biomarker).selectinload(
                BiomarkerDefinition.preferred_unit
            ),
        )
    )

    if patient_id:
        await check_patient_access(patient_id, current_user, db)
        query = query.where(ClinicalEvent.patient_id == patient_id)
    elif current_user.role == Role.USER.value:
        # Force filter by user's patients
        patient_ids_query = select(Patient.id).where(Patient.user_id == current_user.user_id)
        query = query.where(ClinicalEvent.patient_id.in_(patient_ids_query))

    if examination_id:
        query = query.where(
            ClinicalEvent.examination_links.any(
                EventExaminationLink.examination_id == examination_id
            )
        )

    if status:
        query = query.where(ClinicalEvent.status == status)

    query = query.order_by(
        ClinicalEvent.onset_date.desc().nulls_last(), ClinicalEvent.created_at.desc()
    )

    result = await db.execute(query)
    events = result.scalars().unique().all()

    # Map to dict to include calculated fields in to_dict() if needed,
    # but ClinicalEventResponse from_attributes=True should handle most.
    # We use to_dict() for complexity handling in model.
    return [e.to_dict() for e in events]


@router.post("", response_model=ClinicalEventResponse)
async def create_event(
    event_in: ClinicalEventCreate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify patient exists and user has access
    await check_patient_access(event_in.patient_id, current_user, db)

    # Create event
    event_data = event_in.model_dump(exclude={"examinations", "observations"})
    new_event = ClinicalEvent(
        **event_data, tenant_id=current_user.tenant_id, created_by=current_user.user_id
    )
    db.add(new_event)
    await db.flush()  # Get ID

    # Handle initial examination links
    if event_in.examinations:
        for exam_link in event_in.examinations:
            try:
                # Verify examination belongs to tenant and user has access
                await check_examination_access(exam_link.examination_id, current_user, db)
                link = EventExaminationLink(
                    event_id=new_event.id,
                    examination_id=exam_link.examination_id,
                    reason=exam_link.reason or "Initial association",
                )
                db.add(link)
            except HTTPException:
                logger.warning(f"Skipping examination link {exam_link.examination_id}: access denied or not found")
                continue

    # Handle initial observation links
    if event_in.observations:
        for obs_link in event_in.observations:
            try:
                # Verify observation belongs to tenant and user has access
                await check_observation_access(obs_link.observation_id, current_user, db)
                link = EventObservationLink(
                    event_id=new_event.id,
                    observation_id=obs_link.observation_id,
                    notes=obs_link.notes,
                )
                db.add(link)
            except HTTPException:
                logger.warning(f"Skipping observation link {obs_link.observation_id}: access denied or not found")
                continue

    await db.commit()

    # Notify the care team (patient's user + assigned doctors) about the new event.
    try:
        from app.models.enums import (
            NotificationCategory,
            NotificationSeverity,
            NotificationSource,
            NotificationType,
            RecipientKind,
        )
        from app.services.notification_service import emit

        await emit(
            source=NotificationSource.CLINICAL,
            type=NotificationType.CLINICAL_EVENT,
            category=NotificationCategory.CLINICAL_EVENT,
            severity=NotificationSeverity.INFO,
            title=f"New clinical event: {new_event.title}",
            body="A new clinical event was recorded.",
            patient_id=new_event.patient_id,
            tenant_id=current_user.tenant_id,
            targets=[{"kind": RecipientKind.PATIENT.value, "id": str(new_event.patient_id)}],
            payload={
                "event_id": str(new_event.id),
                "actions": [
                    {
                        "id": "view",
                        "label": "View event",
                        "type": "link",
                        "url": f"/events",
                        "style": "primary",
                    }
                ],
            },
            source_ref={"event_id": str(new_event.id)},
            sender_user_id=current_user.user_id,
            link_communication=True,
        )
    except Exception:
        logger.exception("Clinical-event notification emit failed")

    # Re-fetch with relationships
    result = await db.execute(
        select(ClinicalEvent)
        .where(ClinicalEvent.id == new_event.id)
        .options(
            selectinload(ClinicalEvent.type_entity).selectinload(
                ClinicalEventType.category_entity
            ),
            selectinload(ClinicalEvent.examination_links).selectinload(
                EventExaminationLink.examination
            ),
            selectinload(ClinicalEvent.observation_links).selectinload(
                EventObservationLink.observation
            ).selectinload(Observation.biomarker).selectinload(
                BiomarkerDefinition.preferred_unit
            ),
        )
    )
    return result.scalar_one().to_dict()


@router.get("/{event_id}", response_model=ClinicalEventResponse)
async def get_event(
    event_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_event_access(event_id, current_user, db)
    result = await db.execute(
        select(ClinicalEvent)
        .where(
            and_(
                ClinicalEvent.id == event_id,
                ClinicalEvent.tenant_id == current_user.tenant_id,
            )
        )
        .options(
            selectinload(ClinicalEvent.type_entity).selectinload(
                ClinicalEventType.category_entity
            ),
            selectinload(ClinicalEvent.examination_links).selectinload(
                EventExaminationLink.examination
            ),
            selectinload(ClinicalEvent.observation_links).selectinload(
                EventObservationLink.observation
            ).selectinload(Observation.biomarker).selectinload(
                BiomarkerDefinition.preferred_unit
            ),
        )
    )
    event = result.scalar_one_or_none()
    return event.to_dict()


@router.put("/{event_id}", response_model=ClinicalEventResponse)
async def update_event(
    event_id: UUID,
    event_in: ClinicalEventUpdate,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    event = await check_event_access(event_id, current_user, db)

    update_data = event_in.model_dump(exclude_unset=True, exclude={"examinations", "observations"})
    for key, value in update_data.items():
        setattr(event, key, value)

    # Sync examinations if provided
    if event_in.examinations is not None:
        # Load links for sync
        res = await db.execute(
            select(ClinicalEvent)
            .where(ClinicalEvent.id == event_id)
            .options(selectinload(ClinicalEvent.examination_links))
        )
        event = res.scalar_one()
        
        # Current links
        current_links = {link.examination_id: link for link in event.examination_links}
        new_exam_ids = {exam_link.examination_id for exam_link in event_in.examinations}

        # Remove links not in new list
        for exam_id in list(current_links.keys()):
            if exam_id not in new_exam_ids:
                await db.delete(current_links[exam_id])

        # Add or update links
        for exam_link in event_in.examinations:
            if exam_link.examination_id in current_links:
                current_links[exam_link.examination_id].reason = exam_link.reason
            else:
                try:
                    # Verify examination belongs to tenant and user has access
                    await check_examination_access(exam_link.examination_id, current_user, db)
                    new_link = EventExaminationLink(
                        event_id=event.id,
                        examination_id=exam_link.examination_id,
                        reason=exam_link.reason or "Associated visit",
                    )
                    db.add(new_link)
                except HTTPException:
                    continue

    # Sync observations if provided
    if event_in.observations is not None:
        # Load links for sync
        res = await db.execute(
            select(ClinicalEvent)
            .where(ClinicalEvent.id == event_id)
            .options(selectinload(ClinicalEvent.observation_links))
        )
        event = res.scalar_one()
        
        # Current links
        current_obs_links = {link.observation_id: link for link in event.observation_links}
        new_obs_ids = {obs_link.observation_id for obs_link in event_in.observations}

        # Remove links not in new list
        for obs_id in list(current_obs_links.keys()):
            if obs_id not in new_obs_ids:
                await db.delete(current_obs_links[obs_id])

        # Add or update links
        for obs_link in event_in.observations:
            if obs_link.observation_id in current_obs_links:
                current_obs_links[obs_link.observation_id].notes = obs_link.notes
            else:
                try:
                    # Verify observation belongs to tenant and user has access
                    await check_observation_access(obs_link.observation_id, current_user, db)
                    new_link = EventObservationLink(
                        event_id=event.id,
                        observation_id=obs_link.observation_id,
                        notes=obs_link.notes,
                    )
                    db.add(new_link)
                except HTTPException:
                    continue

    event.updated_by = current_user.user_id

    await db.commit()
    await db.refresh(event)

    # Re-fetch with relationships
    result = await db.execute(
        select(ClinicalEvent)
        .where(ClinicalEvent.id == event.id)
        .options(
            selectinload(ClinicalEvent.type_entity).selectinload(
                ClinicalEventType.category_entity
            ),
            selectinload(ClinicalEvent.examination_links).selectinload(
                EventExaminationLink.examination
            ),
            selectinload(ClinicalEvent.observation_links).selectinload(
                EventObservationLink.observation
            ).selectinload(Observation.biomarker).selectinload(
                BiomarkerDefinition.preferred_unit
            ),
        )
    )
    return result.scalar_one().to_dict()


@router.delete("/{event_id}")
async def delete_event(
    event_id: UUID,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    event = await check_event_access(event_id, current_user, db)

    await db.delete(event)
    await db.commit()
    return {"message": "Clinical event deleted successfully"}


@router.post("/{event_id}/link-examination", response_model=ClinicalEventResponse)
async def link_examination(
    event_id: UUID,
    link_in: EventExaminationLinkBase,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify event and examination belong to tenant and user has access
    await check_event_access(event_id, current_user, db)
    await check_examination_access(link_in.examination_id, current_user, db)

    # Check if link already exists
    existing_link = await db.execute(
        select(EventExaminationLink).where(
            and_(
                EventExaminationLink.event_id == event_id,
                EventExaminationLink.examination_id == link_in.examination_id,
            )
        )
    )
    if existing_link.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Examination already linked to this event"
        )

    new_link = EventExaminationLink(
        event_id=event_id, examination_id=link_in.examination_id, reason=link_in.reason
    )
    db.add(new_link)
    await db.commit()

    # Re-fetch event with relationships
    result = await db.execute(
        select(ClinicalEvent)
        .where(ClinicalEvent.id == event_id)
        .options(
            selectinload(ClinicalEvent.type_entity).selectinload(
                ClinicalEventType.category_entity
            ),
            selectinload(ClinicalEvent.examination_links).selectinload(
                EventExaminationLink.examination
            ),
            selectinload(ClinicalEvent.observation_links).selectinload(
                EventObservationLink.observation
            ).selectinload(Observation.biomarker).selectinload(
                BiomarkerDefinition.preferred_unit
            ),
        )
    )
    return result.scalar_one().to_dict()
