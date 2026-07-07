"""Resource registry for the FHIR R4 facade.

Each FHIR resource type exposed by the facade registers a :class:`ResourceEntry`
here. The registry is consumed by:

* :mod:`app.services.fhir_facade_service` — to build the CapabilityStatement
* :mod:`app.facade.search` — to dispatch search/read by resource type
* :mod:`app.api.v1.endpoints.fhir_r4` — to wire up the HTTP routes

Adding a new resource to the facade = register here + ensure the model has
``to_fhir_dict()`` + a converter exists in ``fhir_converter``. Typically <50 LOC.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Type


@dataclass
class ResourceEntry:
    """Registration record for one FHIR resource type."""

    resource_type: str
    model: Type[Any]
    to_fhir_dict_attr: str = "to_fhir_dict"
    fhir_to_orm_fn: Optional[Callable] = None
    search_params: FrozenSet[str] = field(default_factory=frozenset)
    interactions: FrozenSet[str] = field(
        default_factory=lambda: frozenset(
            {"read", "search-type", "create", "update", "delete"}
        )
    )
    versioned: bool = True
    soft_delete: bool = True
    # The reference path under ``/fhir/R4/`` (e.g. ``/Condition``).
    path: Optional[str] = None
    # The tenant scoping strategy: 'tenant_id' (default) or 'none' (e.g. for
    # global catalog resources like Medication). 'patient' for patient-scoped only.
    tenant_scope: str = "tenant_id"
    # Optional additional filter to apply on every search (e.g. MedicationRequest
    # only sees rows where intent='order'). Use a SQLAlchemy lambda.
    search_filter: Optional[Callable] = None
    # Optional per-search-param predicate builder for resource-specific params
    # that the generic dispatcher in ``facade.crud._build_resource_filter``
    # cannot infer (typically because they require a join/EXISTS against another
    # table). Signature: ``fn(model, key, value) -> predicate | None``. The
    # dispatcher consults this *before* the generic builder; a ``None`` return
    # falls through to the generic logic. This keeps model-specific knowledge in
    # the registry (where the model mapping already lives) instead of polluting
    # the generic crud layer.
    param_filter: Optional[Callable] = None

    @property
    def route_path(self) -> str:
        return self.path or f"/{self.resource_type}"


class _ResourceRegistry:
    """Mutable registry, accessed via :data:`RESOURCE_REGISTRY`."""

    def __init__(self) -> None:
        self._entries: Dict[str, ResourceEntry] = {}

    def register(self, entry: ResourceEntry) -> ResourceEntry:
        if entry.resource_type in self._entries:
            raise ValueError(f"{entry.resource_type} already registered")
        self._entries[entry.resource_type] = entry
        return entry

    def get(self, resource_type: str) -> Optional[ResourceEntry]:
        return self._entries.get(resource_type)

    def all(self) -> List[ResourceEntry]:
        # Stable order by resource_type for CapabilityStatement determinism.
        return sorted(self._entries.values(), key=lambda e: e.resource_type)

    def types(self) -> List[str]:
        return sorted(self._entries.keys())

    def __contains__(self, resource_type: str) -> bool:
        return resource_type in self._entries

    def __len__(self) -> int:
        return len(self._entries)


# Singleton. Resources register themselves at import time of
# ``app.facade.routes`` (which is imported by the facade router module).
RESOURCE_REGISTRY = _ResourceRegistry()


def register_all() -> None:
    """Register every FHIR resource exposed by the facade.

    Called once at app startup (via ``app.facade.routes`` import). Idempotent
    in practice — but ``register()`` raises on duplicates, so this function
    is best called from a single import site.
    """

    from app.models.clinical_event import ClinicalEvent
    from app.models.doctor_model import DoctorModel
    from app.models.document_model import DocumentModel
    from app.models.examination_model import ExaminationModel
    from app.models.fhir.allergy import AllergyIntolerance
    from app.models.fhir.communication import CommunicationModel
    from app.models.fhir.device import DeviceModel
    from app.models.fhir.medication import Medication, MedicationCatalog
    from app.models.fhir.organization import OrganizationModel
    from app.models.fhir.patient import DiagnosticReport, Observation, Patient
    from app.models.fhir.provenance import ProvenanceModel
    from app.models.enums import MedicationIntent

    from app.services.fhir_converter import (
        fhir_to_allergy_orm,
        fhir_to_communication_orm,
        fhir_to_condition_orm,
        fhir_to_device_orm,
        fhir_to_diagnostic_report_orm,
        fhir_to_document_reference_orm,
        fhir_to_encounter_orm,
        fhir_to_episode_of_care_orm,
        fhir_to_medication_orm,
        fhir_to_medication_request_orm,
        fhir_to_observation_orm,
        fhir_to_organization_orm,
        fhir_to_patient_orm,
        fhir_to_practitioner_orm,
        fhir_to_provenance_orm,
    )

    # ---- Condition-specific search-param filter ----
    # ClinicalEvent's Condition projection stores its clinical status in a
    # ``status`` enum column (handled generically), its code in a ``code``
    # String column (handled generically), but ``category`` and ``encounter``
    # require joins that the generic dispatcher can't infer. This filter emits
    # EXISTS-subquery predicates so the params are honored without polluting
    # the generic crud builder. Returns None to defer to the generic builder
    # for any other param.
    def _condition_param_filter(model, key: str, value: str):
        from sqlalchemy import exists, select as _select

        from app.models.clinical_event import (
            ClinicalEventType,
            EventExaminationLink,
        )
        from app.models.concept_model import Concept

        # Strip the token modifier (e.g. "encounter:identifier=…") — we honor
        # the bare form only.
        base_key = key.split(":", 1)[0]

        if base_key == "encounter":
            # Condition?encounter=Encounter/{id} — events linked (via
            # EventExaminationLink) to the given examination.
            raw = value.split("/")[-1] if "/" in value else value
            try:
                from uuid import UUID as _UUID

                rid = _UUID(str(raw))
            except (ValueError, TypeError):
                return None
            return exists().where(
                EventExaminationLink.event_id == model.id,
                EventExaminationLink.examination_id == rid,
            )

        if base_key == "category":
            # Condition?category=<code> — events whose type's category concept
            # matches the given slug. Concept.slug is the stable token.
            return model.type_id.in_(
                _select(ClinicalEventType.id).where(
                    ClinicalEventType.category_concept_id.in_(
                        _select(Concept.id).where(Concept.slug == value)
                    )
                )
            )

        # Defer to the generic builder.
        return None

    def _episode_of_care_param_filter(model, key: str, value: str):
        """EpisodeOfCare-specific search params.

        ``status`` translates FHIR EpisodeOfCare status values
        (active|finished|onhold|planned) back to the ``ClinicalEventStatus``
        enum stored on the row (ACTIVE|RESOLVED|ON_HOLD|UNKNOWN), since the
        forward projection maps RESOLVED→finished etc. Without this, a search
        ``?status=finished`` would compare against the raw 'RESOLVED' value and
        match nothing.
        """
        from sqlalchemy import func, String as _SAString

        base_key = key.split(":", 1)[0]
        if base_key == "status":
            status_map = {
                "active": "ACTIVE",
                "finished": "RESOLVED",
                "onhold": "ON_HOLD",
                "planned": "UNKNOWN",
            }
            target = status_map.get(value.lower())
            if target is None:
                return None
            return func.lower(model.status.cast(_SAString)) == target.lower()
        return None

    # ---- Patient-compartment resources ----
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="Patient",
            model=Patient,
            fhir_to_orm_fn=fhir_to_patient_orm,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="Observation",
            model=Observation,
            fhir_to_orm_fn=fhir_to_observation_orm,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="Condition",
            model=ClinicalEvent,
            fhir_to_orm_fn=fhir_to_condition_orm,
            # Condition.category / Condition.encounter need joins that the
            # generic dispatcher can't infer — see ``_condition_param_filter``.
            param_filter=_condition_param_filter,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="EpisodeOfCare",
            # Same model as Condition — a health journey is BOTH a problem
            # (Condition) and a journey-over-time (EpisodeOfCare). No new table,
            # no dual-write; the projection method below emits EpisodeOfCare.
            model=ClinicalEvent,
            fhir_to_orm_fn=fhir_to_episode_of_care_orm,
            to_fhir_dict_attr="to_fhir_episode_of_care_dict",
            # status translates FHIR EoC values back to ClinicalEventStatus.
            param_filter=_episode_of_care_param_filter,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="Encounter",
            model=ExaminationModel,
            fhir_to_orm_fn=fhir_to_encounter_orm,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="AllergyIntolerance",
            model=AllergyIntolerance,
            fhir_to_orm_fn=fhir_to_allergy_orm,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="MedicationStatement",
            model=Medication,
            fhir_to_orm_fn=fhir_to_medication_orm,
            # Search filter: only rows with intent=statement.
            search_filter=lambda: Medication.intent == MedicationIntent.STATEMENT,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="MedicationRequest",
            model=Medication,
            fhir_to_orm_fn=fhir_to_medication_request_orm,
            # Search filter: only rows with intent != statement.
            search_filter=lambda: Medication.intent != MedicationIntent.STATEMENT,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="DiagnosticReport",
            model=DiagnosticReport,
            fhir_to_orm_fn=fhir_to_diagnostic_report_orm,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="DocumentReference",
            model=DocumentModel,
            fhir_to_orm_fn=fhir_to_document_reference_orm,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="Device",
            model=DeviceModel,
            fhir_to_orm_fn=fhir_to_device_orm,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="Communication",
            model=CommunicationModel,
            fhir_to_orm_fn=fhir_to_communication_orm,
        )
    )

    # ---- Non-clinical resources ----
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="Organization",
            model=OrganizationModel,
            fhir_to_orm_fn=fhir_to_organization_orm,
            versioned=False,
        )
    )
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="Practitioner",
            model=DoctorModel,
            fhir_to_orm_fn=fhir_to_practitioner_orm,
            versioned=False,
        )
    )

    # ---- Catalog / standalone Medication ----
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="Medication",
            model=MedicationCatalog,
            # No create/update/delete via facade — catalog is read-only there.
            interactions=frozenset({"read", "search-type"}),
            tenant_scope="none",  # global catalog; tenant-scoped via nullable tenant_id
            versioned=False,
            soft_delete=False,
        )
    )

    # ---- Provenance (read + search only — immutable) ----
    RESOURCE_REGISTRY.register(
        ResourceEntry(
            resource_type="Provenance",
            model=ProvenanceModel,
            fhir_to_orm_fn=fhir_to_provenance_orm,
            interactions=frozenset({"read", "search-type", "create"}),
            versioned=False,
            soft_delete=False,
        )
    )
