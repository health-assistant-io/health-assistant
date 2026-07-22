"""Tenant/patient access-check helpers (audit C2).

Canonical resource-access checks shared by the router layer AND the service
layer. Each helper: (1) str→UUID coerces, (2) selects by id AND tenant
(cross-tenant → 404), (3) for the ``USER`` role verifies the resource belongs
to a patient owned by ``current_user.user_id`` (403 otherwise), (4) returns
the model instance.

These raise domain exceptions (``NotFoundError`` / ``AuthorizationError`` /
``ValidationError`` from :mod:`app.core.errors`), NOT ``HTTPException``. The
global ``DomainError`` handler in ``main.py`` maps each to its HTTP status,
so the same helper is callable from a FastAPI endpoint, a Celery task, an
importer, or the FHIR facade without coupling to Starlette/HTTPException.

This module lives under ``app/services`` (not ``app/api``) precisely so the
service layer can import it without a layer inversion — the audit (C2) found
``clinical_event_service`` importing these from ``app.api.v1.endpoints.utils``,
which coupled it to the router and made it unusable from Celery/import/facade.
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.models.clinical_event import ClinicalEvent
from app.models.enums import Role
from app.models.examination_model import ExaminationModel
from app.models.fhir.allergy import AllergyIntolerance
from app.models.fhir.medication import Medication
from app.models.fhir.patient import Observation, Patient
from app.models.fhir.vaccine import PatientImmunization
from app.schemas.user import TokenData


async def check_patient_access(
    patient_id: str | UUID, current_user: TokenData, db: AsyncSession
):
    """Verify if the current user has access to the specified patient"""
    if isinstance(patient_id, str):
        try:
            patient_id = UUID(patient_id)
        except ValueError:
            raise ValidationError("Invalid patient ID format")

    result = await db.execute(
        select(Patient).where(
            Patient.id == patient_id, Patient.tenant_id == current_user.tenant_id
        )
    )
    patient = result.scalar_one_or_none()

    if not patient:
        raise NotFoundError("Patient not found")

    # Check access for standard users
    if current_user.role == Role.USER.value:
        # Access denied if patient is not assigned to this user
        if not patient.user_id or str(patient.user_id) != str(current_user.user_id):
            raise AuthorizationError("Access denied to this patient's data")

    return patient


async def check_examination_access(
    examination_id: str | UUID, current_user: TokenData, db: AsyncSession
):
    """Verify if the current user has access to the specified examination"""
    if isinstance(examination_id, str):
        try:
            examination_id = UUID(examination_id)
        except ValueError:
            raise ValidationError("Invalid examination ID format")

    result = await db.execute(
        select(ExaminationModel).where(
            ExaminationModel.id == examination_id,
            ExaminationModel.tenant_id == current_user.tenant_id,
        )
    )
    examination = result.scalar_one_or_none()

    if not examination:
        raise NotFoundError("Examination not found")

    # This will check if the user has access to the patient linked to this examination
    await check_patient_access(examination.patient_id, current_user, db)

    return examination


async def check_medication_access(
    medication_id: str | UUID, current_user: TokenData, db: AsyncSession
):
    """Verify if the current user has access to the specified medication record"""
    if isinstance(medication_id, str):
        try:
            medication_id = UUID(medication_id)
        except ValueError:
            raise ValidationError("Invalid medication ID format")

    result = await db.execute(
        select(Medication).where(
            Medication.id == medication_id,
            Medication.tenant_id == current_user.tenant_id,
            Medication.deleted_at.is_(None),
        )
    )
    medication = result.scalar_one_or_none()

    if not medication:
        raise NotFoundError("Medication record not found")

    # This will check if the user has access to the patient linked to this medication
    await check_patient_access(medication.patient_id, current_user, db)

    return medication


async def check_immunization_access(
    immunization_id: str | UUID, current_user: TokenData, db: AsyncSession
):
    """Verify access to a patient-immunization record (Phase 5).

    Selects by id + tenant (cross-tenant → 404), then delegates to
    ``check_patient_access`` for the USER own-patient gate."""
    if isinstance(immunization_id, str):
        try:
            immunization_id = UUID(immunization_id)
        except ValueError:
            raise ValidationError("Invalid immunization ID format")

    result = await db.execute(
        select(PatientImmunization).where(
            PatientImmunization.id == immunization_id,
            PatientImmunization.tenant_id == current_user.tenant_id,
            PatientImmunization.deleted_at.is_(None),
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise NotFoundError("Immunization record not found")
    await check_patient_access(record.patient_id, current_user, db)
    return record


async def check_event_access(
    event_id: str | UUID, current_user: TokenData, db: AsyncSession
):
    """Verify if the current user has access to the specified clinical event"""
    if isinstance(event_id, str):
        try:
            event_id = UUID(event_id)
        except ValueError:
            raise ValidationError("Invalid event ID format")

    result = await db.execute(
        select(ClinicalEvent).where(
            ClinicalEvent.id == event_id,
            ClinicalEvent.tenant_id == current_user.tenant_id,
            ClinicalEvent.deleted_at.is_(None),
        )
    )
    event = result.scalar_one_or_none()

    if not event:
        raise NotFoundError("Clinical event not found")

    # This will check if the user has access to the patient linked to this event
    await check_patient_access(event.patient_id, current_user, db)

    return event


async def check_allergy_access(
    allergy_id: str | UUID, current_user: TokenData, db: AsyncSession
):
    """Verify if the current user has access to the specified allergy record"""
    if isinstance(allergy_id, str):
        try:
            allergy_id = UUID(allergy_id)
        except ValueError:
            raise ValidationError("Invalid allergy ID format")

    result = await db.execute(
        select(AllergyIntolerance).where(
            AllergyIntolerance.id == allergy_id,
            AllergyIntolerance.tenant_id == current_user.tenant_id,
            AllergyIntolerance.deleted_at.is_(None),
        )
    )
    allergy = result.scalar_one_or_none()

    if not allergy:
        raise NotFoundError("Allergy record not found")

    # This will check if the user has access to the patient linked to this allergy
    await check_patient_access(allergy.patient_id, current_user, db)

    return allergy


async def check_observation_access(
    observation_id: str | UUID, current_user: TokenData, db: AsyncSession
):
    """Verify if the current user has access to the specified observation record"""
    if isinstance(observation_id, str):
        try:
            observation_id = UUID(observation_id)
        except ValueError:
            raise ValidationError("Invalid observation ID format")

    result = await db.execute(
        select(Observation).where(
            Observation.id == observation_id,
            Observation.tenant_id == current_user.tenant_id,
        )
    )
    observation = result.scalar_one_or_none()

    if not observation:
        raise NotFoundError("Observation record not found")

    # Prefer the maintained relational patient_id (audit B3); fall back to the
    # FHIR subject reference for legacy rows.
    patient_id = observation.patient_id or (
        (observation.subject or {}).get("reference", "").split("/")[-1] or None
    )
    if not patient_id:
        raise ValidationError("Observation does not have a linked patient")

    await check_patient_access(patient_id, current_user, db)

    return observation
