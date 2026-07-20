"""Examination service — write-side chokepoint for the FHIR ``Encounter``
equivalent (the app calls it an Examination).

Workstream E.1 of the integrations follow-ups pass
(plan: dev/plans/integrations-sdk-followups-2026-07-21.md).

Before this module existed, the canonical write path was inlined in the
``POST /examinations`` endpoint at
``backend/app/api/v1/endpoints/examinations.py`` — patient validation,
category resolution, heuristic dedup, ORM construction, doctor linking,
commit, and relationship reload all lived in the route handler. That
meant integrations (the bridge provider, the future
``supports_examinations`` SDK hook) had no clean call site and either
bypassed dedup entirely (losing data integrity) or duplicated the logic
(the bridge did the latter, and its copy is stale — uses the wrong
``category_id`` column name).

This module exposes a single function — :func:`create_examination` — that
owns the full write pipeline. The endpoint becomes a thin HTTP adapter;
integrations (after E.3) call the same function via the engine's
``run_sync`` wiring.

Dedup contract:

1. **Integration dedup (precise, opt-in via kwargs).** When **both**
   ``source_integration_id`` and ``external_id`` are supplied, the
   service looks up an existing exam by the exact upstream key
   ``(tenant_id, patient_id, source_integration_id, external_id)`` and
   returns it as-is if found. The partial unique index
   ``uq_examination_integration_dedup`` (added in migration
   ``e1x2a3m4i5n6``) catches the race window at the DB layer.

2. **Heuristic dedup (fuzzy, default-on for UI callers).** When
   ``auto_extract_metadata`` is False (i.e. not a bulk-placeholder
   upload), the service looks up an existing exam matching
   ``(tenant_id, patient_id, examination_date, category_concept_id,
   notes)`` and returns it as-is if found. This is the original
   endpoint's "catch accidental re-submission" behavior, preserved
   unchanged.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import NotFoundError
from app.models.doctor_model import DoctorModel
from app.models.examination_model import ExaminationModel
from app.models.fhir.patient import Patient
from app.schemas.examination import ExaminationCreate
from app.schemas.user import TokenData
from app.services.access import check_patient_access

# NOTE: ``app.ai.pipeline.service.MedicalProcessingService`` is imported
# lazily inside :func:`create_examination` to avoid a circular import
# (``app.ai.pipeline.service`` imports ``app.workers.task_logger`` which
# imports ``app.workers.ai_tasks`` which imports
# ``app.ai.pipeline.service``). Top-level imports of any of those modules
# from a service module (which is itself imported early) trip the cycle.
# The endpoint module (``app.api.v1.endpoints.examinations``) can get away
# with a top-level import because route modules load later in the app
# lifespan.

logger = logging.getLogger(__name__)


async def create_examination(
    db: AsyncSession,
    current_user: TokenData,
    payload: ExaminationCreate,
    *,
    source_integration_id: Optional[UUID] = None,
    external_id: Optional[str] = None,
) -> ExaminationModel:
    """Create an examination with patient validation, category resolution,
    dedup, and doctor linking.

    Integration-sourced dedup (workstream E.1): when **both**
    ``source_integration_id`` and ``external_id`` are supplied (either as
    explicit kwargs or via the payload's optional fields), the service
    looks up an existing exam with that key for the same patient + tenant
    and returns it as-is rather than creating a duplicate.

    UI heuristic dedup (preserved from the original endpoint behavior):
    when ``auto_extract_metadata`` is False and no integration dedup
    applied, the service looks up an existing exam by
    ``(tenant_id, patient_id, examination_date, category_concept_id,
    notes)`` and returns it as-is if found. Bulk-placeholder uploads (the
    ``auto_extract_metadata=True`` case) bypass this check because the
    upload flow intentionally creates multiple exams in a batch.

    Returns the reloaded ORM row with ``doctors`` / ``organization`` /
    ``category_concept`` eager-loaded so callers (endpoint or engine) get
    a consistent shape — matches the previous endpoint behavior.
    """
    if payload.patient_id is not None:
        await check_patient_access(payload.patient_id, current_user, db)

    # Validate patient exists — the original endpoint did this before the
    # service-layer refactor; preserved as a defensive NotFoundError so a
    # bogus patient_id surfaces clearly rather than failing on the FK
    # constraint at commit time.
    if payload.patient_id is not None:
        await _validate_patient_exists(db, payload.patient_id)

    # Resolve category: explicit id wins; otherwise resolve from the
    # ``category`` text field via the medical-processing service.
    category_concept_id = payload.category_concept_id
    if not category_concept_id and payload.category:
        # Lazy import — see module-level comment about the circular-import
        # hazard with ``app.ai.pipeline.service``.
        from app.ai.pipeline.service import MedicalProcessingService

        processing_service = MedicalProcessingService(db)
        category_entity = await processing_service.resolve_category(
            payload.category, current_user.tenant_id
        )
        category_concept_id = category_entity.id

    # ``source_integration_id`` and ``external_id`` may come from either
    # the explicit kwargs (engine path) or the payload itself (legacy
    # callers that set them on the wire). Kwarg wins when both are present.
    effective_source = source_integration_id or payload.source_integration_id
    effective_external = external_id or payload.external_id

    # ---- Integration dedup (precise) -------------------------------------
    if effective_source is not None and effective_external is not None:
        existing = await _find_by_integration_key(
            db,
            tenant_id=current_user.tenant_id,
            patient_id=payload.patient_id,
            source_integration_id=effective_source,
            external_id=effective_external,
        )
        if existing is not None:
            logger.info(
                "create_examination: returning existing exam %s (dedup hit "
                "on source_integration_id=%s external_id=%r)",
                existing.id, effective_source, effective_external,
            )
            return await _reload_with_relationships(db, existing.id)

    # ---- Heuristic UI dedup (fuzzy) --------------------------------------
    # Only for **pure UI callers** — defined as "no integration provenance
    # at all". An integration-sourced exam (even one missing external_id
    # because the upstream system has no stable ids) must not accidentally
    # match against unrelated UI rows that happen to share date + category
    # + notes. ``auto_extract_metadata=True`` further bypasses this for the
    # bulk-placeholder upload flow (multiple placeholder exams per batch).
    elif (
        effective_source is None
        and effective_external is None
        and not payload.auto_extract_metadata
    ):
        heuristic_match = await _find_by_heuristic(
            db,
            tenant_id=current_user.tenant_id,
            patient_id=payload.patient_id,
            examination_date=payload.examination_date,
            category_concept_id=category_concept_id,
            notes=payload.notes,
        )
        if heuristic_match is not None:
            logger.info(
                "Duplicate examination detected for patient %s, returning "
                "existing record %s.",
                payload.patient_id, heuristic_match.id,
            )
            return heuristic_match

    # ---- Create -----------------------------------------------------------
    examination = ExaminationModel(
        patient_id=payload.patient_id,
        examination_date=payload.examination_date,
        notes=payload.notes,
        patient_notes=payload.patient_notes,
        category_concept_id=category_concept_id,
        organization_id=payload.organization_id,
        auto_extract_metadata=payload.auto_extract_metadata,
        tenant_id=current_user.tenant_id,
        created_by=current_user.user_id,
        source_integration_id=effective_source,
        external_id=effective_external,
    )

    if payload.doctor_ids:
        result = await db.execute(
            select(DoctorModel).where(
                DoctorModel.id.in_(payload.doctor_ids),
                DoctorModel.tenant_id == current_user.tenant_id,
            )
        )
        examination.doctors = list(result.scalars().all())

    db.add(examination)
    await db.commit()

    return await _reload_with_relationships(db, examination.id)


async def _validate_patient_exists(db: AsyncSession, patient_id: UUID) -> None:
    """Raises NotFoundError if the patient row is missing.

    The original endpoint returned a 404 inline; the service raises a
    domain exception that the global handler maps to 404. Same outcome,
    cleaner separation.
    """
    result = await db.execute(select(Patient).where(Patient.id == patient_id))
    if result.scalar_one_or_none() is None:
        raise NotFoundError(
            f"Patient with ID {patient_id} not found. Please create the "
            "patient first or use a valid patient ID."
        )


async def _find_by_integration_key(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    patient_id: Optional[UUID],
    source_integration_id: UUID,
    external_id: str,
) -> Optional[ExaminationModel]:
    """Look up an existing integration-sourced exam by exact dedup key.

    The partial unique index ``uq_examination_integration_dedup`` makes
    this lookup fast; in the race window between the SELECT and the
    subsequent INSERT, the index also catches duplicates at the DB layer.
    """
    stmt = select(ExaminationModel).where(
        ExaminationModel.tenant_id == tenant_id,
        ExaminationModel.patient_id == patient_id,
        ExaminationModel.source_integration_id == source_integration_id,
        ExaminationModel.external_id == external_id,
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def _find_by_heuristic(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    patient_id: Optional[UUID],
    examination_date,
    category_concept_id,
    notes,
) -> Optional[ExaminationModel]:
    """The original endpoint's UI anti-re-submission check.

    Matches on ``(tenant_id, patient_id, examination_date,
    category_concept_id, notes)`` — same predicate the endpoint used
    pre-refactor. Returned as-is (no relationships eager-loaded); the
    endpoint's response_model will trigger lazy loads if needed.
    """
    stmt = select(ExaminationModel).where(
        ExaminationModel.tenant_id == tenant_id,
        ExaminationModel.patient_id == patient_id,
        ExaminationModel.examination_date == examination_date,
        ExaminationModel.category_concept_id == category_concept_id,
        ExaminationModel.notes == notes,
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def _reload_with_relationships(
    db: AsyncSession, examination_id: UUID
) -> ExaminationModel:
    """Reload with the relationships the response_model expects.

    Mirrors the original endpoint's reload block (doctors / organization /
    category_concept) so the refactor is behavior-preserving for response
    shape.
    """
    result = await db.execute(
        select(ExaminationModel)
        .where(ExaminationModel.id == examination_id)
        .options(
            selectinload(ExaminationModel.doctors),
            selectinload(ExaminationModel.organization),
            selectinload(ExaminationModel.category_concept),
        )
    )
    return result.scalar_one()
