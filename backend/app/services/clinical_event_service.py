"""Clinical Event service — the single chokepoint for clinical-event mutations.

Module-level async functions (same style as ``fhir_service``). The endpoint
(``api/v1/endpoints/clinical_events.py``) is a thin HTTP adapter; the FHIR
facade and the import path can also call these so that link-sync, soft-delete,
notification, and access semantics are consistent across every write surface.

Note on layering: this module imports the ``check_*_access`` helpers from
``app.api.v1.endpoints.utils``. Those helpers are shared domain-authorization
logic that currently lives under the endpoints package; the HTTPException they
raise on denial is allowed to propagate (FastAPI maps it). A future refactor
can relocate them to ``app/services/access.py``.
"""

import datetime as _dt
import logging
from datetime import timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.endpoints.utils import (
    check_event_access,
    check_examination_access,
    check_observation_access,
    check_patient_access,
)
from app.core.errors import DomainError
from app.models.biomarker_model import (
    BiomarkerDefinition,
    Unit,
)
from app.models.clinical_event import (
    ClinicalEvent,
    ClinicalEventOccurrence,
    ClinicalEventType,
    EventAnatomyLink,
    EventExaminationLink,
    EventObservationLink,
)
from app.models.concept_model import ConceptEdge
from app.models.enums import (
    ClinicalEventStatus,
    ConceptProvenance,
    ConceptRelationType,
    EdgeApprovalStatus,
    EdgeEndpointType,
    Role,
)
from app.models.fhir.patient import Observation, Patient
from app.schemas.clinical_event import (
    ClinicalEventCreate,
    ClinicalEventOccurrenceCreate,
    ClinicalEventUpdate,
)
from app.schemas.user import TokenData

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


async def emit_event_notification(
    event: "ClinicalEvent",
    action: str,
    current_user: TokenData,
) -> None:
    """Emit a clinical-event lifecycle notification (best-effort).

    Failures are logged and never abort the parent write. ``action`` is one of
    ``created`` | ``updated`` | ``resolved`` | ``deleted``. This is the single
    chokepoint for lifecycle notifications across REST, the FHIR facade, and
    the import path.
    """
    from app.models.enums import (
        NotificationCategory,
        NotificationSeverity,
        NotificationSource,
        NotificationType,
        RecipientKind,
    )
    from app.services.notification_service import emit

    severity = (
        NotificationSeverity.WARNING
        if action == "deleted"
        else NotificationSeverity.INFO
    )
    title_map = {
        "created": f"New clinical event: {event.title}",
        "updated": f"Clinical event updated: {event.title}",
        "resolved": f"Clinical event resolved: {event.title}",
        "deleted": f"Clinical event removed: {event.title}",
    }
    body_map = {
        "created": "A new clinical event was recorded.",
        "updated": "A clinical event was updated.",
        "resolved": "A clinical event was marked as resolved.",
        "deleted": "A clinical event was deleted.",
    }
    try:
        await emit(
            source=NotificationSource.CLINICAL,
            type=NotificationType.CLINICAL_EVENT,
            category=NotificationCategory.CLINICAL_EVENT,
            severity=severity,
            title=title_map.get(action, f"Clinical event: {event.title}"),
            body=body_map.get(action, ""),
            patient_id=event.patient_id,
            tenant_id=current_user.tenant_id,
            targets=[
                {"kind": RecipientKind.PATIENT.value, "id": str(event.patient_id)}
            ],
            payload={
                "event_id": str(event.id),
                "action": action,
                "actions": [
                    {
                        "id": "view",
                        "label": "View event",
                        "type": "link",
                        "url": "/events",
                        "style": "primary",
                    }
                ],
            },
            source_ref={"event_id": str(event.id), "action": action},
            sender_user_id=current_user.user_id,
            link_communication=True,
        )
    except Exception:
        logger.exception("Clinical-event notification emit failed (action=%s)", action)


def _event_eager_loads():
    """The canonical eager-load chain for a fully-serialized ClinicalEvent.

    Centralized so ``list``/``get``/``create``/``update``/``link_*`` all return
    the same relationship depth — previously this chain was duplicated in 5
    places across the endpoint.
    """
    return (
        selectinload(ClinicalEvent.type_entity).selectinload(
            ClinicalEventType.category_concept
        ),
        selectinload(ClinicalEvent.examination_links).selectinload(
            EventExaminationLink.examination
        ),
        selectinload(ClinicalEvent.observation_links)
        .selectinload(EventObservationLink.observation)
        .selectinload(Observation.biomarker)
        .selectinload(BiomarkerDefinition.preferred_unit),
        selectinload(ClinicalEvent.occurrence_links),
        selectinload(ClinicalEvent.anatomy_links),
    )


async def _refetch_with_relations(db: AsyncSession, event_id: UUID) -> Dict[str, Any]:
    """Re-fetch an event with the full eager-load chain and serialize it."""
    result = await db.execute(
        select(ClinicalEvent)
        .where(ClinicalEvent.id == event_id)
        .options(*_event_eager_loads())
    )
    return result.scalar_one().to_dict()


async def list_events(
    db: AsyncSession,
    current_user: TokenData,
    *,
    patient_id: Optional[UUID] = None,
    examination_id: Optional[UUID] = None,
    status: Optional[ClinicalEventStatus] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List clinical events, tenant-scoped, soft-deletes excluded.

    USER role is restricted to their own patients. ``limit`` is clamped to
    ``MAX_LIMIT``; ``offset`` enables pagination.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)

    query = (
        select(ClinicalEvent)
        .where(
            ClinicalEvent.tenant_id == current_user.tenant_id,
            ClinicalEvent.deleted_at.is_(None),
        )
        .options(*_event_eager_loads())
    )

    if patient_id:
        await check_patient_access(patient_id, current_user, db)
        query = query.where(ClinicalEvent.patient_id == patient_id)
    elif current_user.role == Role.USER.value:
        # Force filter by user's patients.
        patient_ids_query = select(Patient.id).where(
            Patient.user_id == current_user.user_id
        )
        query = query.where(ClinicalEvent.patient_id.in_(patient_ids_query))

    if examination_id:
        query = query.where(
            ClinicalEvent.examination_links.any(
                EventExaminationLink.examination_id == examination_id
            )
        )

    if status:
        query = query.where(ClinicalEvent.status == status)

    query = (
        query.order_by(
            ClinicalEvent.onset_date.desc().nulls_last(),
            ClinicalEvent.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(query)
    events = result.scalars().unique().all()
    return [e.to_dict() for e in events]


async def get_event(
    db: AsyncSession, event_id: UUID, current_user: TokenData
) -> Dict[str, Any]:
    """Fetch a single event (access-checked) with full relationships."""
    await check_event_access(event_id, current_user, db)
    return await _refetch_with_relations(db, event_id)


async def create_event(
    db: AsyncSession, current_user: TokenData, payload: ClinicalEventCreate
) -> Dict[str, Any]:
    """Create an event + initial exam/observation links, notify, and re-fetch.

    Per-link access is checked; links the user can't access are skipped
    (logged) rather than aborting the whole create.
    """
    await check_patient_access(payload.patient_id, current_user, db)

    event_data = payload.model_dump(exclude={"examinations", "observations"})
    new_event = ClinicalEvent(
        **event_data,
        tenant_id=current_user.tenant_id,
        created_by=current_user.user_id,
    )
    db.add(new_event)
    await db.flush()  # assign id

    await _attach_examination_links(
        db, new_event.id, payload.examinations, current_user
    )
    await _attach_observation_links(
        db, new_event.id, payload.observations, current_user
    )

    await db.commit()
    await emit_event_notification(new_event, "created", current_user)
    return await _refetch_with_relations(db, new_event.id)


async def update_event(
    db: AsyncSession,
    event_id: UUID,
    current_user: TokenData,
    payload: ClinicalEventUpdate,
) -> Dict[str, Any]:
    """Partial update with full-replace link sync; notify on resolve/edit."""
    event = await check_event_access(event_id, current_user, db)

    will_resolve = _detect_resolve_transition(event, payload)

    update_data = payload.model_dump(
        exclude_unset=True, exclude={"examinations", "observations"}
    )
    for key, value in update_data.items():
        setattr(event, key, value)

    if payload.examinations is not None:
        await _sync_examination_links(db, event_id, payload.examinations, current_user)
    if payload.observations is not None:
        await _sync_observation_links(db, event_id, payload.observations, current_user)

    event.updated_by = current_user.user_id
    # Bump the VersionedMixin version so FHIR meta.versionId advances on each
    # PUT (previously the column was decorative — always 1).
    event.version = (event.version or 1) + 1
    await db.commit()
    await db.refresh(event)

    await emit_event_notification(
        event, "resolved" if will_resolve else "updated", current_user
    )
    return await _refetch_with_relations(db, event.id)


async def soft_delete_event(
    db: AsyncSession, event_id: UUID, current_user: TokenData
) -> None:
    """Tombstone an event (set ``deleted_at``) and emit the deletion notice."""
    event = await check_event_access(event_id, current_user, db)
    event.deleted_at = _dt.datetime.now(timezone.utc)
    event.updated_by = current_user.user_id
    await db.commit()
    await emit_event_notification(event, "deleted", current_user)


async def link_examination(
    db: AsyncSession,
    event_id: UUID,
    current_user: TokenData,
    examination_id: UUID,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a single examination link (rejects duplicates)."""
    from fastapi import HTTPException

    await check_event_access(event_id, current_user, db)
    await check_examination_access(examination_id, current_user, db)

    existing = await db.execute(
        select(EventExaminationLink).where(
            EventExaminationLink.event_id == event_id,
            EventExaminationLink.examination_id == examination_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Examination already linked to this event"
        )

    db.add(
        EventExaminationLink(
            event_id=event_id,
            examination_id=examination_id,
            reason=reason or "Associated visit",
        )
    )
    await db.commit()
    return await _refetch_with_relations(db, event_id)


async def link_observation(
    db: AsyncSession,
    event_id: UUID,
    current_user: TokenData,
    observation_id: UUID,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a single observation link (rejects duplicates).

    Closes the asymmetry with examinations: previously observation links were
    only manageable via the create/update full-replace payload.
    """
    from fastapi import HTTPException

    await check_event_access(event_id, current_user, db)
    await check_observation_access(observation_id, current_user, db)

    existing = await db.execute(
        select(EventObservationLink).where(
            EventObservationLink.event_id == event_id,
            EventObservationLink.observation_id == observation_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Observation already linked to this event"
        )

    db.add(
        EventObservationLink(
            event_id=event_id, observation_id=observation_id, notes=notes
        )
    )
    await db.commit()
    return await _refetch_with_relations(db, event_id)


# ---------------------------------------------------------------------------
# Occurrences (Phase 3a)
# ---------------------------------------------------------------------------


async def add_occurrence(
    db: AsyncSession,
    event_id: UUID,
    current_user: TokenData,
    payload: ClinicalEventOccurrenceCreate,
) -> Dict[str, Any]:
    """Append a discrete occurrence to a journey and re-fetch the event."""
    await check_event_access(event_id, current_user, db)

    occurrence = ClinicalEventOccurrence(
        event_id=event_id,
        occurred_at=payload.occurred_at,
        title=payload.title,
        severity=payload.severity,
        intensity=payload.intensity,
        notes=payload.notes,
        anatomy_id=payload.anatomy_id,
        metadata_=payload.metadata or {},
    )
    db.add(occurrence)
    await db.commit()
    return await _refetch_with_relations(db, event_id)


async def delete_occurrence(
    db: AsyncSession,
    event_id: UUID,
    occurrence_id: UUID,
    current_user: TokenData,
) -> Dict[str, Any]:
    """Remove a single occurrence from a journey (access-checked)."""
    from fastapi import HTTPException

    await check_event_access(event_id, current_user, db)

    result = await db.execute(
        select(ClinicalEventOccurrence).where(
            ClinicalEventOccurrence.id == occurrence_id,
            ClinicalEventOccurrence.event_id == event_id,
        )
    )
    occurrence = result.scalar_one_or_none()
    if not occurrence:
        raise HTTPException(status_code=404, detail="Occurrence not found")

    await db.delete(occurrence)
    await db.commit()
    return await _refetch_with_relations(db, event_id)


# ---------------------------------------------------------------------------
# Anatomy links (Phase 3b) — promote EventAnatomyLink from dead code
# ---------------------------------------------------------------------------


async def link_anatomy(
    db: AsyncSession,
    event_id: UUID,
    current_user: TokenData,
    anatomy_id: UUID,
    relation_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Link an anatomy site to an event (rejects duplicates).

    ``relation_type`` distinguishes ``primary_site`` / ``radiates_to`` /
    ``referred_to``. The (event_id, anatomy_id) pair is unique.
    """
    from fastapi import HTTPException

    await check_event_access(event_id, current_user, db)

    # Anatomy existence (any tenant — anatomy is reference data).
    from app.models.anatomy_model import AnatomyStructure

    anatomy = (
        await db.execute(
            select(AnatomyStructure).where(AnatomyStructure.id == anatomy_id)
        )
    ).scalar_one_or_none()
    if not anatomy:
        raise HTTPException(status_code=404, detail="Anatomy structure not found")

    existing = await db.execute(
        select(EventAnatomyLink).where(
            EventAnatomyLink.event_id == event_id,
            EventAnatomyLink.anatomy_id == anatomy_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Anatomy already linked to this event"
        )

    db.add(
        EventAnatomyLink(
            event_id=event_id,
            anatomy_id=anatomy_id,
            relation_type=relation_type or "primary_site",
        )
    )
    await db.commit()
    return await _refetch_with_relations(db, event_id)


async def unlink_anatomy(
    db: AsyncSession,
    event_id: UUID,
    anatomy_id: UUID,
    current_user: TokenData,
) -> Dict[str, Any]:
    """Remove an anatomy link from an event (access-checked)."""
    from fastapi import HTTPException

    await check_event_access(event_id, current_user, db)

    result = await db.execute(
        select(EventAnatomyLink).where(
            EventAnatomyLink.event_id == event_id,
            EventAnatomyLink.anatomy_id == anatomy_id,
        )
    )
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Anatomy link not found")

    await db.delete(link)
    await db.commit()
    return await _refetch_with_relations(db, event_id)


# ---------------------------------------------------------------------------
# Journey insights (Phase 4a) — type-driven behavior via ClinicalEventEngine
# ---------------------------------------------------------------------------


async def get_insights(
    db: AsyncSession,
    event_id: UUID,
    current_user: TokenData,
) -> Dict[str, Any]:
    """Compute type-driven journey insights for an event.

    Returns the current phase, upcoming/overdue milestones, recommended
    biomarkers (resolved from ``BiomarkerEventCorrelation``), and an overdue
    flag. Pure-computed — no persistence.
    """
    from app.services.clinical_event_engine import compute_insights

    event = await check_event_access(event_id, current_user, db)

    # Resolve recommended biomarkers + the type template explicitly so the
    # (sync) engine never triggers a lazy load on event.type_entity.
    recommended: List[Dict[str, Any]] = []
    type_template = None
    if event.type_id:
        type_template = (
            await db.execute(
                select(ClinicalEventType).where(ClinicalEventType.id == event.type_id)
            )
        ).scalar_one_or_none()

        # Resolve recommended biomarkers from the concept_edges graph
        # (biomarker MONITORS clinical_event_type). Replaces the legacy
        # BiomarkerEventCorrelation table — Phase 3 consolidation.
        mon_edges = (
            (
                await db.execute(
                    select(ConceptEdge).where(
                        ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                        ConceptEdge.dst_type == EdgeEndpointType.CLINICAL_EVENT_TYPE,
                        ConceptEdge.dst_id == event.type_id,
                        ConceptEdge.relation == ConceptRelationType.MONITORS,
                        ConceptEdge.status == EdgeApprovalStatus.APPROVED,
                    )
                )
            )
            .scalars()
            .all()
        )
        corr_type_by_bio = {
            e.src_id: (e.properties or {}).get("correlation_type") for e in mon_edges
        }
        bio_ids = list(corr_type_by_bio.keys())

        rows: list = []
        if bio_ids:
            rows = (
                await db.execute(
                    select(BiomarkerDefinition, Unit.symbol.label("unit_symbol"))
                    .outerjoin(Unit, BiomarkerDefinition.preferred_unit_id == Unit.id)
                    .where(BiomarkerDefinition.id.in_(bio_ids))
                )
            ).all()
        for bio, symbol in rows:
            recommended.append(
                {
                    "id": str(bio.id),
                    "slug": bio.slug,
                    "name": bio.name,
                    "preferred_unit_symbol": symbol,
                    "correlation_type": corr_type_by_bio.get(bio.id),
                }
            )

    return compute_insights(
        event, type_template=type_template, recommended_biomarkers=recommended
    ).to_dict()


# ---------------------------------------------------------------------------
# Biomarker ↔ event-type correlations (Phase 4b)
# ---------------------------------------------------------------------------


async def add_correlated_biomarker(
    db: AsyncSession,
    event_type_id: UUID,
    biomarker_id: UUID,
    *,
    correlation_type: str = "monitoring",
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Link a biomarker to this event type via a concept_edge (MONITORS).

    Idempotent on the (type, biomarker) pair — re-adding updates the
    ``correlation_type``/``description`` (stored on the edge's ``properties``
    JSONB) in place rather than 409-ing. Validates that both the event type and
    biomarker exist. Replaces the legacy BiomarkerEventCorrelation table.
    """
    from fastapi import HTTPException

    etype = (
        await db.execute(
            select(ClinicalEventType).where(ClinicalEventType.id == event_type_id)
        )
    ).scalar_one_or_none()
    if not etype:
        raise HTTPException(status_code=404, detail="Clinical event type not found")

    bio = (
        await db.execute(
            select(BiomarkerDefinition).where(BiomarkerDefinition.id == biomarker_id)
        )
    ).scalar_one_or_none()
    if not bio:
        raise HTTPException(status_code=404, detail="Biomarker not found")

    edge = (
        await db.execute(
            select(ConceptEdge).where(
                ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                ConceptEdge.src_id == biomarker_id,
                ConceptEdge.dst_type == EdgeEndpointType.CLINICAL_EVENT_TYPE,
                ConceptEdge.dst_id == event_type_id,
                ConceptEdge.relation == ConceptRelationType.MONITORS,
            )
        )
    ).scalar_one_or_none()
    props = {"correlation_type": correlation_type, "description": description}
    if edge:
        edge.properties = props
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(edge, "properties")
        await db.commit()
        await db.refresh(edge)
        return _correlation_to_dict(edge, bio)

    edge = ConceptEdge(
        src_type=EdgeEndpointType.BIOMARKER,
        src_id=biomarker_id,
        dst_type=EdgeEndpointType.CLINICAL_EVENT_TYPE,
        dst_id=event_type_id,
        relation=ConceptRelationType.MONITORS,
        tenant_id=None,
        source=ConceptProvenance.MANUAL,
        status=EdgeApprovalStatus.APPROVED,
        properties=props,
    )
    db.add(edge)
    await db.commit()
    await db.refresh(edge)
    return _correlation_to_dict(edge, bio)


async def remove_correlated_biomarker(
    db: AsyncSession,
    event_type_id: UUID,
    biomarker_id: UUID,
) -> None:
    """Delete the biomarker↔event-type MONITORS edge (404 if not found)."""
    from fastapi import HTTPException

    edge = (
        await db.execute(
            select(ConceptEdge).where(
                ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
                ConceptEdge.src_id == biomarker_id,
                ConceptEdge.dst_type == EdgeEndpointType.CLINICAL_EVENT_TYPE,
                ConceptEdge.dst_id == event_type_id,
                ConceptEdge.relation == ConceptRelationType.MONITORS,
            )
        )
    ).scalar_one_or_none()
    if not edge:
        raise HTTPException(status_code=404, detail="Correlation not found")
    await db.delete(edge)
    await db.commit()


def _correlation_to_dict(edge: ConceptEdge, bio: BiomarkerDefinition) -> Dict[str, Any]:
    props = edge.properties or {}
    return {
        "id": str(edge.id),
        "event_type_id": str(edge.dst_id),
        "biomarker_id": str(edge.src_id),
        "correlation_type": props.get("correlation_type"),
        "description": props.get("description"),
        "biomarker": {
            "id": str(bio.id),
            "slug": bio.slug,
            "name": bio.name,
        },
    }


# ---------------------------------------------------------------------------
# Link sync helpers
# ---------------------------------------------------------------------------


def _detect_resolve_transition(
    event: ClinicalEvent, payload: ClinicalEventUpdate
) -> bool:
    """True iff this update flips status to RESOLVED from a non-resolved state."""
    status_dump = payload.model_dump(exclude_unset=True)
    return (
        "status" in status_dump
        and payload.status == ClinicalEventStatus.RESOLVED
        and event.status != ClinicalEventStatus.RESOLVED
    )


async def _attach_examination_links(
    db: AsyncSession,
    event_id: UUID,
    links: Optional[List[Any]],
    current_user: TokenData,
) -> None:
    """Add initial examination links at create time (best-effort per link).

    Per-link access is checked; failures are logged and skipped rather than
    aborting the create.
    """
    from fastapi import HTTPException

    if not links:
        return
    for link in links:
        try:
            await check_examination_access(link.examination_id, current_user, db)
            db.add(
                EventExaminationLink(
                    event_id=event_id,
                    examination_id=link.examination_id,
                    reason=link.reason or "Initial association",
                )
            )
        except (HTTPException, DomainError):
            logger.warning(
                "Skipping examination link %s: access denied or not found",
                link.examination_id,
            )


async def _attach_observation_links(
    db: AsyncSession,
    event_id: UUID,
    links: Optional[List[Any]],
    current_user: TokenData,
) -> None:
    """Add initial observation links at create time (best-effort per link)."""
    from fastapi import HTTPException

    if not links:
        return
    for link in links:
        try:
            await check_observation_access(link.observation_id, current_user, db)
            db.add(
                EventObservationLink(
                    event_id=event_id,
                    observation_id=link.observation_id,
                    notes=link.notes,
                )
            )
        except (HTTPException, DomainError):
            logger.warning(
                "Skipping observation link %s: access denied or not found",
                link.observation_id,
            )


async def _sync_examination_links(
    db: AsyncSession,
    event_id: UUID,
    new_links: List[Any],
    current_user: TokenData,
) -> None:
    """Full-replace sync: remove links not in the new list, add/update the rest.

    Reuses the loaded relationship if present, otherwise loads it.
    """
    from fastapi import HTTPException

    event = (
        await db.execute(
            select(ClinicalEvent)
            .where(ClinicalEvent.id == event_id)
            .options(selectinload(ClinicalEvent.examination_links))
        )
    ).scalar_one()

    current = {link.examination_id: link for link in event.examination_links}
    new_ids = {link.examination_id for link in new_links}

    for exam_id in list(current.keys()):
        if exam_id not in new_ids:
            await db.delete(current[exam_id])

    for link in new_links:
        if link.examination_id in current:
            current[link.examination_id].reason = link.reason
            continue
        try:
            await check_examination_access(link.examination_id, current_user, db)
            db.add(
                EventExaminationLink(
                    event_id=event_id,
                    examination_id=link.examination_id,
                    reason=link.reason or "Associated visit",
                )
            )
        except (HTTPException, DomainError):
            logger.warning(
                "Skipping examination link %s: access denied or not found",
                link.examination_id,
            )


async def _sync_observation_links(
    db: AsyncSession,
    event_id: UUID,
    new_links: List[Any],
    current_user: TokenData,
) -> None:
    """Full-replace sync for observation links (mirrors examination sync)."""
    from fastapi import HTTPException

    event = (
        await db.execute(
            select(ClinicalEvent)
            .where(ClinicalEvent.id == event_id)
            .options(selectinload(ClinicalEvent.observation_links))
        )
    ).scalar_one()

    current = {link.observation_id: link for link in event.observation_links}
    new_ids = {link.observation_id for link in new_links}

    for obs_id in list(current.keys()):
        if obs_id not in new_ids:
            await db.delete(current[obs_id])

    for link in new_links:
        if link.observation_id in current:
            current[link.observation_id].notes = link.notes
            continue
        try:
            await check_observation_access(link.observation_id, current_user, db)
            db.add(
                EventObservationLink(
                    event_id=event_id,
                    observation_id=link.observation_id,
                    notes=link.notes,
                )
            )
        except (HTTPException, DomainError):
            logger.warning(
                "Skipping observation link %s: access denied or not found",
                link.observation_id,
            )
