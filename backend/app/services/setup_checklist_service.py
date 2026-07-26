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

import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.converters import to_uuid
from app.core.errors import ValidationError
from app.models.ai_provider_model import AIModel, AIProviderModel, AITaskAssignment
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
from app.schemas.setup_checklist import (
    ExtensionCatalogResponse,
    ExtensionCatalogItem,
    ExtensionOption,
    SetupChecklistResponse,
    StepResult,
)
from app.schemas.user import TokenData
from app.services.access import check_patient_access
from app.services.fhir_extensions import SUPPORTED_PATIENT_EXTENSIONS

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
        manually_completed=False,
        optional=optional,
        payload_hint=payload_hint,
    )


# ---------------------------------------------------------------------------
# Manual-completion overrides
#
# Integrations are enabled by default; wizard steps are backend-derived from
# live data. But users sometimes want to dismiss a step the evaluator can't
# detect ("I configured AI my own way", "this patient genuinely has no
# allergies"). A manual override lets them mark any step complete; it is
# persisted per-user in ``UserModel.settings["setup.manual_complete"]`` keyed
# by scope (role vs per-entity) so it survives logout/reinstall and syncs
# across devices. ``completed`` remains the authoritative effective state —
# the override just ORs into it.
# ---------------------------------------------------------------------------

MANUAL_COMPLETE_KEY = "setup.manual_complete"


def _manual_scope_key(entity: Optional[str], entity_id: Optional[UUID | str]) -> str:
    """Storage namespace for manual overrides.

    Role steps → ``"role"``; entity steps → ``"<entity>:<entity_id>"`` so
    overrides don't leak between patients (a step marked done for patient A
    must not read as done for patient B).
    """
    if entity and entity_id:
        return f"{entity}:{entity_id}"
    return "role"


async def _load_manual_overrides(
    db: AsyncSession,
    user: TokenData,
    entity: Optional[str],
    entity_id: Optional[UUID | str],
) -> set[str]:
    """Return the set of step ids the user has manually marked complete."""
    if user.user_id is None:
        return set()
    result = await db.execute(
        select(UserModel.settings).where(UserModel.id == user.user_id)
    )
    raw = result.scalar_one_or_none() or {}
    scope = _manual_scope_key(entity, entity_id)
    bucket = (raw.get(MANUAL_COMPLETE_KEY) or {}).get(scope) or {}
    return {str(k) for k, v in bucket.items() if v}


def _apply_manual_overrides(steps: List[StepResult], overrides: set[str]) -> None:
    """Fold manual overrides into each step (mutates in place).

    A step is effectively complete when the evaluator said so OR the user
    manually overrode it. ``manually_completed`` is only flagged when the
    override is the reason the step is complete (not when the evaluator
    already agreed) so the UI can show an "undo" affordance precisely.
    """
    for step in steps:
        evaluator_completed = step.completed
        if step.id in overrides:
            step.manually_completed = True
            step.completed = True
        else:
            step.manually_completed = False
        # If the evaluator already detects completion, the manual flag is
        # meaningless for display — keep it False so the UI doesn't offer an
        # "undo" that would have no effect.
        if evaluator_completed:
            step.manually_completed = False


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
    """Single AI-config step rendered as a guided redirect sub-wizard.

    ``payload_hint.sub_steps`` carries per-sub-step completion + route so the
    frontend shows a guided 3-step checklist (provider → model → assignment)
    that opens the real AI config page at the right tab — NOT an inline
    duplicate of the settings forms.
    """
    has_provider = (await db.scalar(
        select(func.count(AIProviderModel.id)).where(
            AIProviderModel.tenant_id == user.tenant_id,
            AIProviderModel.is_active.is_(True),
            AIProviderModel.scope.in_([AIScope.USER, AIScope.TENANT]),
        )
    ) or 0) > 0
    if not has_provider:
        has_provider = await _has_ai_provider(db, user, scope, AIScope.SYSTEM)

    has_model = (await db.scalar(
        select(func.count(AIModel.id))
        .join(AIProviderModel, AIModel.provider_id == AIProviderModel.id)
        .where(
            AIModel.is_active.is_(True),
            AIProviderModel.tenant_id == user.tenant_id,
            AIProviderModel.scope.in_([AIScope.USER, AIScope.TENANT]),
        )
    ) or 0) > 0
    if not has_model:
        has_model = await _has_ai_model(db, user, scope, AIScope.SYSTEM)

    has_assignment = await _has_tenant_ai_assignment(db, user, scope)
    base = "/admin/tenant/ai-config"

    return _step(
        "tenant.ai_config",
        "setup.steps.tenant.ai_config",
        "external_config",
        completed=has_provider and has_model and has_assignment,
        optional=True,
        payload_hint={"sub_steps": [
            {"id": "provider", "done": has_provider, "route": f"{base}?tab=providers"},
            {"id": "model", "done": has_model, "route": f"{base}?tab=models"},
            {"id": "assignment", "done": has_assignment, "route": f"{base}?tab=tasks"},
        ]},
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
        payload_hint={"route": "/admin/tenant/users"},
    )


async def _system_first_tenant(db, user, scope) -> StepResult:
    count = await db.scalar(select(func.count(TenantModel.id)))
    return _step(
        "system.first_tenant",
        "setup.steps.system.first_tenant",
        "redirect",
        completed=(count or 0) > 0,
        payload_hint={"route": "/admin/system/tenants"},
    )


async def _system_catalog_seeded(db, user, scope) -> StepResult:
    count = await db.scalar(select(func.count(BiomarkerDefinition.id)))
    return _step(
        "system.catalog_seeded",
        "setup.steps.system.catalog_seeded",
        "derived",
        completed=(count or 0) > 0,
    )


async def _has_ai_provider(db, user, scope, ai_scope) -> bool:
    count = await db.scalar(
        select(func.count(AIProviderModel.id)).where(
            AIProviderModel.scope == ai_scope,
            AIProviderModel.is_active.is_(True),
        )
    )
    return (count or 0) > 0


async def _has_ai_model(db, user, scope, ai_scope) -> bool:
    count = await db.scalar(
        select(func.count(AIModel.id))
        .join(AIProviderModel, AIModel.provider_id == AIProviderModel.id)
        .where(
            AIModel.is_active.is_(True),
            AIProviderModel.scope == ai_scope,
            AIProviderModel.is_active.is_(True),
        )
    )
    return (count or 0) > 0


async def _system_ai_config(db, user, scope) -> StepResult:
    """Single AI-config step rendered as a guided redirect sub-wizard (system scope)."""
    has_provider = await _has_ai_provider(db, user, scope, AIScope.SYSTEM)
    has_model = await _has_ai_model(db, user, scope, AIScope.SYSTEM)
    count = await db.scalar(
        select(func.count(AITaskAssignment.id)).where(
            AITaskAssignment.scope == AIScope.SYSTEM,
            AITaskAssignment.is_active.is_(True),
        )
    )
    has_assignment = (count or 0) > 0
    base = "/admin/system/ai-config"

    return _step(
        "system.ai_config",
        "setup.steps.system.ai_config",
        "external_config",
        completed=has_provider and has_model and has_assignment,
        optional=True,
        payload_hint={"sub_steps": [
            {"id": "provider", "done": has_provider, "route": f"{base}?tab=providers"},
            {"id": "model", "done": has_model, "route": f"{base}?tab=models"},
            {"id": "assignment", "done": has_assignment, "route": f"{base}?tab=tasks"},
        ]},
    )


async def _system_first_user(db, user, scope) -> StepResult:
    count = await db.scalar(select(func.count(UserModel.id)))
    return _step(
        "system.first_user",
        "setup.steps.system.first_user",
        "redirect",
        completed=(count or 0) >= 2,
        payload_hint={"route": "/admin/system/users"},
    )


async def _system_integrations_review(db, user, scope) -> StepResult:
    """Prompt the SYSTEM_ADMIN to review the integrations catalog.

    Integrations are enabled by default (see ``system_integration_service``);
    this step is the "have you reviewed what's available?" nudge. It flips to
    complete once the admin has interacted with the admin console at least
    once — i.e. any ``SystemIntegration`` row exists (an enable or disable
    toggle writes a row). The step is optional so it never blocks the
    completion ring; the admin can also manually mark it done.
    """
    from app.models.system_integration import SystemIntegration

    count = await db.scalar(select(func.count(SystemIntegration.domain)))
    return _step(
        "system.integrations_review",
        "setup.steps.system.integrations_review",
        "redirect",
        completed=(count or 0) > 0,
        optional=True,
        payload_hint={"route": "/admin/system/integrations"},
    )


ROLE_CHECKLISTS: Dict[Role, Tuple[StepEvaluator, ...]] = {
    Role.SYSTEM_ADMIN: (
        _system_first_tenant,
        _system_catalog_seeded,
        _tenant_first_patient,
        _system_ai_config,
        _system_first_user,
        _system_integrations_review,
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
# Extension catalog (drives the wizard's extension-section inputs)
# ---------------------------------------------------------------------------

_SEEDS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "seeds"
_OMB_SEED_CACHE: Optional[Dict[str, Any]] = None


def _load_omb_seed() -> Dict[str, Any]:
    """Load + cache the OMB race/ethnicity/language picklist seed."""
    global _OMB_SEED_CACHE
    if _OMB_SEED_CACHE is not None:
        return _OMB_SEED_CACHE
    seed_path = _SEEDS_DIR / "omb_race_ethnicity.json"
    try:
        with open(seed_path, "r", encoding="utf-8") as fh:
            _OMB_SEED_CACHE = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("could not load OMB seed %s: %s", seed_path, exc)
        _OMB_SEED_CACHE = {}
    return _OMB_SEED_CACHE


# Map each supported patient extension key to a value_type the client renders.
# ``omb_category`` → dropdown (CDC OMB codes); ``code`` → dropdown (languages);
# ``string`` → free text.
_EXTENSION_VALUE_TYPE: Dict[str, str] = {
    "race": "omb_category",
    "ethnicity": "omb_category",
    "preferred_language": "code",
    "insurance_provider": "string",
}


def _extension_options(key: str) -> Optional[List[ExtensionOption]]:
    seed = _load_omb_seed()
    if key in ("race", "ethnicity"):
        seed_field = "ethnicities" if key == "ethnicity" else "races"
        raw = seed.get(seed_field, [])
        return [ExtensionOption(code=o["code"], display=o["display"]) for o in raw]
    if key == "preferred_language":
        raw = seed.get("languages", [])
        return [ExtensionOption(code=o["code"], display=o["display"]) for o in raw]
    return None


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

        # Fold per-user manual-completion overrides into the effective state.
        overrides = await _load_manual_overrides(
            self.db, user, entity, resolved_entity_id
        )
        _apply_manual_overrides(all_steps, overrides)

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

    async def set_manual_complete(
        self,
        user: TokenData,
        step_id: str,
        completed: bool,
        entity: Optional[str] = None,
        entity_id: Optional[UUID | str] = None,
    ) -> StepResult:
        """Persist (or clear) a manual-completion override for one step.

        Returns the updated :class:`StepResult` (re-evaluated + override
        applied) so the caller can echo it back to the client without a
        second round-trip. Raises ``ValidationError`` if the step id is not
        part of the caller's current checklist (defends against arbitrary
        storage writes for step ids that don't belong to this role/entity).
        """
        resolved_entity_id: Optional[UUID] = None
        if entity:
            if not entity_id:
                raise ValidationError(
                    "entity_id is required when entity is given"
                )
            resolved_entity_id = to_uuid(entity_id)

        # Validate the step id belongs to this caller's scope before writing.
        checklist = await self.get_checklist(
            user, entity=entity, entity_id=resolved_entity_id
        )
        if not any(s.id == step_id for s in checklist.steps):
            raise ValidationError(
                f"Unknown step id '{step_id}' for this checklist scope."
            )

        # Persist into UserModel.settings["setup.manual_complete"][scope].
        result = await self.db.execute(
            select(UserModel).where(UserModel.id == user.user_id)
        )
        user_model = result.scalar_one_or_none()
        if user_model is None:
            raise ValidationError("User not found.")

        raw_settings = dict(user_model.settings or {})
        bucket = dict(raw_settings.get(MANUAL_COMPLETE_KEY) or {})
        scope = _manual_scope_key(entity, resolved_entity_id)
        scoped = dict(bucket.get(scope) or {})
        if completed:
            scoped[step_id] = True
        else:
            scoped.pop(step_id, None)

        if scoped:
            bucket[scope] = scoped
        else:
            bucket.pop(scope, None)

        if bucket:
            raw_settings[MANUAL_COMPLETE_KEY] = bucket
        else:
            raw_settings.pop(MANUAL_COMPLETE_KEY, None)

        user_model.settings = raw_settings
        flag_modified(user_model, "settings")
        await self.db.commit()

        # Re-evaluate so the returned step reflects the freshly-persisted
        # override folded onto the latest evaluator state.
        refreshed = await self.get_checklist(
            user, entity=entity, entity_id=resolved_entity_id
        )
        return next(s for s in refreshed.steps if s.id == step_id)

    async def get_extension_catalog(
        self, entity: str = "patient"
    ) -> ExtensionCatalogResponse:
        """Return the supported-extension catalog for an entity.

        Today only ``patient`` is supported. The catalog is derived from
        ``SUPPORTED_PATIENT_EXTENSIONS`` (the single authority) + the OMB
        race/ethnicity/language seed for picklist options — so the client
        never hardcodes keys or CDC codes.
        """
        if entity != "patient":
            raise ValidationError(
                f"Unsupported catalog entity: {entity} (only 'patient' is supported)"
            )
        items: List[ExtensionCatalogItem] = []
        for ext in SUPPORTED_PATIENT_EXTENSIONS:
            items.append(
                ExtensionCatalogItem(
                    key=ext.key,
                    title_i18n_key=ext.title_i18n_key,
                    value_type=_EXTENSION_VALUE_TYPE.get(ext.key, "string"),
                    cardinality=ext.cardinality,
                    options=_extension_options(ext.key),
                )
            )
        return ExtensionCatalogResponse(entity="patient", extensions=items)