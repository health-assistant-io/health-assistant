"""Backend-derived setup-checklist service (in-app guided-setup wizard).

See ``dev/audits/setup-wizard-design.md`` §§D2/D5/D7. Two pluggable
registries drive the contract:

- ``ROLE_CHECKLISTS``  — ``Role`` → ``tuple`` of role step evaluators.
- ``ENTITY_CHECKLISTS``— ``entity`` ('patient', 'doctor', ...) → entity step
  evaluators.

Each evaluator is an ``async (db, user, scope) -> StepResult`` function.
Adding a step = one evaluator + one registry tuple entry — the service,
endpoint, schema, and (later) frontend are untouched.

Iteration 1 (this commit) ships a representative subset:

Role steps:
- ``user.preferences_language``  (USER) — tiered ``localization.language``
  override resolved via ``SettingsService.resolve_effective``.
- ``user.linked_self_patient``   (USER) — a Patient with ``user_id == self``.
- ``tenant.first_patient``       (ADMIN/MANAGER) — ≥1 patient in tenant.
- ``tenant.first_doctor``        (ADMIN/MANAGER) — ≥1 doctor in tenant.
- ``tenant.first_org``           (ADMIN/MANAGER) — ≥1 organization in tenant.
- ``tenant.ai_config``           (ADMIN/MANAGER) — a USER or TENANT scoped
  ``AITaskAssignment`` is active for the 'ocr' or any task in this tenant.
- ``tenant.member_invited``      (ADMIN/MANAGER) — ≥2 users in tenant.
- ``system.first_tenant``        (SYSTEM_ADMIN) — ≥1 tenant.
- ``system.catalog_seeded``      (SYSTEM_ADMIN) — ≥1 biomarker definition.
- ``system.ai_config``           (SYSTEM_ADMIN) — a SYSTEM-scoped active
  ``AITaskAssignment`` exists (any task_type).
- ``system.first_user``          (SYSTEM_ADMIN) — ≥2 users total.

Entity (patient) steps: demographics (birth_date / address / telecom /
emergency_contact), extensions (race_or_ethnicity / preferred_language /
insurance_provider), and patient-instance aggregations (active allergies,
active medications, active clinical events — the latter models "current
pains"). All aggregation steps are ``optional=True`` ("no allergies" is a
valid end-state; an optional ``dismissed_steps`` flag is a later iteration).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.converters import to_uuid
from app.core.errors import ValidationError
from app.models.ai_provider_model import AITaskAssignment
from app.models.biomarker_model import BiomarkerDefinition
from app.models.clinical_event import ClinicalEvent
from app.models.doctor_model import DoctorModel
from app.models.enums import AIScope, Role
from app.models.fhir.allergy import AllergyIntolerance
from app.models.fhir.medication import Medication, MedicationStatus
from app.models.fhir.organization import OrganizationModel
from app.models.fhir.patient import Patient
from app.models.tenant_model import TenantModel
from app.models.user_model import UserModel
from app.schemas.setup_checklist import SetupChecklistResponse, StepResult
from app.schemas.user import TokenData
from app.services.access import check_patient_access

logger = logging.getLogger(__name__)


StepEvaluator = Callable[
    [AsyncSession, TokenData, Dict[str, Any]], Awaitable[StepResult]
]


SUPPORTED_ENTITIES: Tuple[str, ...] = ("patient",)


def _step(
    step_id: str,
    title_i18n_key: str,
    kind: str,
    *,
    entity: Optional[str] = None,
    completed: bool = False,
    optional: bool = False,
    payload_hint: Optional[Dict[str, Any]] = None,
) -> StepResult:
    return StepResult(
        id=step_id,
        entity=entity,
        title_i18n_key=title_i18n_key,
        kind=kind,
        completed=completed,
        optional=optional,
        payload_hint=payload_hint,
    )


# ---------------------------------------------------------------------------
# Role-step evaluators
# ---------------------------------------------------------------------------


async def _user_preferences_language(db, user, scope) -> StepResult:
    from app.services.settings_service import SettingsService

    service = SettingsService(db)
    values, sources = await service.resolve_effective(
        user.user_id, user.tenant_id
    )
    value = values.get("localization.language")
    resolved_at_user = sources.get("localization.language") in ("user", "tenant", "system")
    completed = bool(value) and resolved_at_user and sources.get(
        "localization.language"
    ) != "default"
    return _step(
        "user.preferences_language",
        "setup.steps.user.preferences_language",
        "external_config",
        completed=completed,
        payload_hint={"route": "/settings/preferences"},
    )


async def _user_linked_self_patient(db, user, scope) -> StepResult:
    count = await db.scalar(
        select(func.count(Patient.id)).where(
            Patient.tenant_id == user.tenant_id,
            Patient.user_id == user.user_id,
            Patient.deleted_at.is_(None),
        )
    )
    return _step(
        "user.linked_self_patient",
        "setup.steps.user.linked_self_patient",
        "redirect",
        completed=(count or 0) > 0,
        payload_hint={"route": "/patients?new=patient"},
    )


async def _tenant_first_patient(db, user, scope) -> StepResult:
    count = await db.scalar(
        select(func.count(Patient.id)).where(
            Patient.tenant_id == user.tenant_id,
            Patient.deleted_at.is_(None),
        )
    )
    return _step(
        "tenant.first_patient",
        "setup.steps.tenant.first_patient",
        "redirect",
        completed=(count or 0) > 0,
        payload_hint={"route": "/patients?new=patient"},
    )


async def _tenant_first_doctor(db, user, scope) -> StepResult:
    count = await db.scalar(
        select(func.count(DoctorModel.id)).where(
            DoctorModel.tenant_id == user.tenant_id,
        )
    )
    return _step(
        "tenant.first_doctor",
        "setup.steps.tenant.first_doctor",
        "redirect",
        completed=(count or 0) > 0,
        payload_hint={"route": "/doctors?new=doctor"},
    )


async def _tenant_first_org(db, user, scope) -> StepResult:
    count = await db.scalar(
        select(func.count(OrganizationModel.id)).where(
            OrganizationModel.tenant_id == user.tenant_id,
            OrganizationModel.deleted_at.is_(None),
        )
    )
    return _step(
        "tenant.first_org",
        "setup.steps.tenant.first_org",
        "redirect",
        completed=(count or 0) > 0,
        payload_hint={"route": "/organizations?new"},
    )


async def _has_tenant_ai_assignment(db, user, scope) -> bool:
    count = await db.scalar(
        select(func.count(AITaskAssignment.id)).where(
            AITaskAssignment.tenant_id == user.tenant_id,
            AITaskAssignment.is_active.is_(True),
            AITaskAssignment.scope.in_([AIScope.USER, AIScope.TENANT, AIScope.SYSTEM]),
        )
    )
    return (count or 0) > 0


async def _tenant_ai_config(db, user, scope) -> StepResult:
    completed = await _has_tenant_ai_assignment(db, user, scope)
    # embrace assignable scope: also accept SYSTEM-scoped fallback assignments.
    return _step(
        "tenant.ai_config",
        "setup.steps.tenant.ai_config",
        "external_config",
        completed=completed,
        optional=True,
        payload_hint={"route": "/settings/ai"},
    )


async def _tenant_member_invited(db, user, scope) -> StepResult:
    count = await db.scalar(
        select(func.count(UserModel.id)).where(
            UserModel.tenant_id == user.tenant_id
        )
    )
    return _step(
        "tenant.member_invited",
        "setup.steps.tenant.member_invited",
        "redirect",
        completed=(count or 0) >= 2,
        optional=True,
        payload_hint={"route": "/settings/members"},
    )


async def _system_first_tenant(db, user, scope) -> StepResult:
    count = await db.scalar(select(func.count(TenantModel.id)))
    return _step(
        "system.first_tenant",
        "setup.steps.system.first_tenant",
        "derived",
        completed=(count or 0) > 0,
    )


async def _system_catalog_seeded(db, user, scope) -> StepResult:
    count = await db.scalar(select(func.count(BiomarkerDefinition.id)))
    return _step(
        "system.catalog_seeded",
        "setup.steps.system.catalog_seeded",
        "derived",
        completed=(count or 0) > 0,
    )


async def _system_ai_config(db, user, scope) -> StepResult:
    count = await db.scalar(
        select(func.count(AITaskAssignment.id)).where(
            AITaskAssignment.scope == AIScope.SYSTEM,
            AITaskAssignment.is_active.is_(True),
        )
    )
    return _step(
        "system.ai_config",
        "setup.steps.system.ai_config",
        "external_config",
        completed=(count or 0) > 0,
        optional=True,
        payload_hint={"route": "/admin/ai"},
    )


async def _system_first_user(db, user, scope) -> StepResult:
    count = await db.scalar(select(func.count(UserModel.id)))
    return _step(
        "system.first_user",
        "setup.steps.system.first_user",
        "derived",
        completed=(count or 0) >= 2,
    )


ROLE_CHECKLISTS: Dict[Role, Tuple[StepEvaluator, ...]] = {
    Role.SYSTEM_ADMIN: (
        _system_first_tenant,
        _system_catalog_seeded,
        _system_ai_config,
        _system_first_user,
    ),
    Role.ADMIN: (
        _tenant_first_org,
        _tenant_first_patient,
        _tenant_first_doctor,
        _tenant_ai_config,
        _tenant_member_invited,
    ),
    Role.MANAGER: (
        _tenant_first_org,
        _tenant_first_patient,
        _tenant_first_doctor,
        _tenant_ai_config,
    ),
    Role.USER: (
        _user_preferences_language,
        _user_linked_self_patient,
    ),
}


# ---------------------------------------------------------------------------
# Entity-step evaluators (patient)
# ---------------------------------------------------------------------------


async def _patient_birth_date(db, user, scope) -> StepResult:
    patient: Patient = scope["patient"]
    return _step(
        "patient.birth_date",
        "setup.steps.patient.birth_date",
        "inline_form",
        entity="patient",
        completed=patient.birth_date is not None,
    )


async def _patient_address(db, user, scope) -> StepResult:
    patient: Patient = scope["patient"]
    return _step(
        "patient.address",
        "setup.steps.patient.address",
        "inline_form",
        entity="patient",
        completed=bool(patient.address),
    )


async def _patient_telecom(db, user, scope) -> StepResult:
    patient: Patient = scope["patient"]
    return _step(
        "patient.telecom",
        "setup.steps.patient.telecom",
        "inline_form",
        entity="patient",
        completed=bool(patient.telecom),
    )


async def _patient_emergency_contact(db, user, scope) -> StepResult:
    patient: Patient = scope["patient"]
    return _step(
        "patient.emergency_contact",
        "setup.steps.patient.emergency_contact",
        "inline_form",
        entity="patient",
        completed=bool(patient.emergency_contact),
        optional=True,
    )


def _has_ext(patient: Patient, *keys: str) -> bool:
    if not patient.extensions:
        return False
    return any(patient.extensions.get(k) for k in keys)


async def _patient_race_or_ethnicity(db, user, scope) -> StepResult:
    patient: Patient = scope["patient"]
    return _step(
        "patient.race_or_ethnicity",
        "setup.steps.patient.race_or_ethnicity",
        "inline_form",
        entity="patient",
        completed=_has_ext(patient, "race", "ethnicity"),
        optional=True,
    )


async def _patient_preferred_language(db, user, scope) -> StepResult:
    patient: Patient = scope["patient"]
    return _step(
        "patient.preferred_language",
        "setup.steps.patient.preferred_language",
        "inline_form",
        entity="patient",
        completed=_has_ext(patient, "preferred_language"),
        optional=True,
    )


async def _patient_insurance_provider(db, user, scope) -> StepResult:
    patient: Patient = scope["patient"]
    return _step(
        "patient.insurance_provider",
        "setup.steps.patient.insurance_provider",
        "inline_form",
        entity="patient",
        completed=_has_ext(patient, "insurance_provider"),
        optional=True,
    )


async def _patient_allergies(db, user, scope) -> StepResult:
    patient: Patient = scope["patient"]
    count = await db.scalar(
        select(func.count(AllergyIntolerance.id)).where(
            AllergyIntolerance.patient_id == patient.id,
            AllergyIntolerance.tenant_id == user.tenant_id,
            AllergyIntolerance.deleted_at.is_(None),
        )
    )
    return _step(
        "patient.allergies",
        "setup.steps.patient.allergies",
        "redirect",
        entity="patient",
        completed=(count or 0) > 0,
        optional=True,
        payload_hint={"route": f"/patients/{patient.id}?section=allergies"},
    )


async def _patient_current_medications(db, user, scope) -> StepResult:
    patient: Patient = scope["patient"]
    count = await db.scalar(
        select(func.count(Medication.id)).where(
            Medication.patient_id == patient.id,
            Medication.tenant_id == user.tenant_id,
            Medication.status == MedicationStatus.ACTIVE,
            Medication.deleted_at.is_(None),
        )
    )
    return _step(
        "patient.current_medications",
        "setup.steps.patient.current_medications",
        "redirect",
        entity="patient",
        completed=(count or 0) > 0,
        optional=True,
        payload_hint={"route": f"/medications?patient={patient.id}"},
    )


async def _patient_current_events(db, user, scope) -> StepResult:
    """Current clinical events — incl. acute / chronic 'pains'.

    The 'current pains' requirement is modeled by existing active
    ``ClinicalEvent`` instances (e.g. a chronic-pain journey). There is no
    separate 'pains' table.
    """
    patient: Patient = scope["patient"]
    count = await db.scalar(
        select(func.count(ClinicalEvent.id)).where(
            ClinicalEvent.patient_id == patient.id,
            ClinicalEvent.tenant_id == user.tenant_id,
            ClinicalEvent.deleted_at.is_(None),
        )
    )
    return _step(
        "patient.current_events",
        "setup.steps.patient.current_events",
        "redirect",
        entity="patient",
        completed=(count or 0) > 0,
        optional=True,
        payload_hint={"route": f"/events?patient={patient.id}"},
    )


ENTITY_CHECKLISTS: Dict[str, Tuple[StepEvaluator, ...]] = {
    "patient": (
        _patient_birth_date,
        _patient_address,
        _patient_telecom,
        _patient_emergency_contact,
        _patient_race_or_ethnicity,
        _patient_preferred_language,
        _patient_insurance_provider,
        _patient_allergies,
        _patient_current_medications,
        _patient_current_events,
    ),
}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _role_value(user: TokenData) -> Role:
    """Normalize the JWT-string role into the ``Role`` enum."""
    try:
        return Role(user.role)
    except ValueError:
        return Role.USER


class SetupChecklistService:
    """Build the role + (optionally) entity setup checklist for a user."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_role_checklist(
        self, user: TokenData
    ) -> List[StepResult]:
        role = _role_value(user)
        evaluators = ROLE_CHECKLISTS.get(role, ())
        scope: Dict[str, Any] = {
            "tenant_id": user.tenant_id,
            "user_id": user.user_id,
        }
        steps: List[StepResult] = []
        for ev in evaluators:
            try:
                steps.append(await ev(self.db, user, scope))
            except Exception as exc:
                logger.warning(
                    "setup role evaluator %s raised: %s", ev.__name__, exc
                )
                steps.append(
                    _step(
                        getattr(ev, "__name__", "unknown"),
                        "setup.steps.error",
                        "derived",
                        completed=False,
                        optional=True,
                    )
                )
        return steps

    async def get_entity_checklist(
        self, user: TokenData, entity: str, entity_id: UUID | str
    ) -> List[StepResult]:
        if entity not in SUPPORTED_ENTITIES:
            raise ValidationError(f"Unsupported checklist entity: {entity}")
        entity_uuid = to_uuid(entity_id)
        if entity == "patient":
            patient = await check_patient_access(entity_uuid, user, self.db)
            scope: Dict[str, Any] = {
                "tenant_id": user.tenant_id,
                "user_id": user.user_id,
                "patient_id": patient.id,
                "patient": patient,
            }
        else:  # pragma: no cover — SUPPORTED_ENTITIES gates this
            raise ValidationError(f"Unsupported checklist entity: {entity}")

        evaluators = ENTITY_CHECKLISTS.get(entity, ())
        steps: List[StepResult] = []
        for ev in evaluators:
            try:
                steps.append(await ev(self.db, user, scope))
            except Exception as exc:
                logger.warning(
                    "setup entity evaluator %s raised: %s", ev.__name__, exc
                )
                steps.append(
                    _step(
                        getattr(ev, "__name__", "unknown"),
                        "setup.steps.error",
                        "derived",
                        entity=entity,
                        completed=False,
                        optional=True,
                    )
                )
        return steps

    async def get_checklist(
        self,
        user: TokenData,
        entity: Optional[str] = None,
        entity_id: Optional[UUID | str] = None,
    ) -> SetupChecklistResponse:
        role_steps = await self.get_role_checklist(user)
        entity_steps: List[StepResult] = []
        resolved_entity_id: Optional[UUID] = None
        if entity:
            if not entity_id:
                raise ValidationError(
                    "entity_id is required when entity is given"
                )
            entity_steps = await self.get_entity_checklist(
                user, entity, entity_id
            )
            resolved_entity_id = to_uuid(entity_id)

        all_steps = role_steps + entity_steps
        mandatory = [s for s in all_steps if not s.optional]
        completion = (
            (sum(1 for s in mandatory if s.completed) / len(mandatory))
            if mandatory
            else 1.0
        )
        return SetupChecklistResponse(
            role=user.role,
            entity=entity,
            entity_id=resolved_entity_id,
            steps=all_steps,
            completion=round(completion, 3),
        )