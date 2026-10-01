import enum


class QuantityType(enum.StrEnum):
    MASS_CONCENTRATION = "MASS_CONCENTRATION"
    MOLAR_CONCENTRATION = "MOLAR_CONCENTRATION"
    NUMBER_CONCENTRATION = "NUMBER_CONCENTRATION"
    PERCENTAGE = "PERCENTAGE"
    PRESSURE = "PRESSURE"
    VOLUME = "VOLUME"
    MASS = "MASS"
    TIME = "TIME"
    RATIO = "RATIO"
    TEMPERATURE = "TEMPERATURE"
    OTHER = "OTHER"


class ClinicalEventStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    ON_HOLD = "ON_HOLD"
    UNKNOWN = "UNKNOWN"


class ScheduleKind(enum.StrEnum):
    """How a ``ClinicalEventType`` should be rendered in calendar/schedule views.

    Set on the type blueprint (the default for all instances of that type).
    Replaces the frontend's status-based heuristic in ``adaptClinicalEventToEvents``
    with an explicit, admin-declared intent.

    - ``STATE``    — ongoing condition with no fixed end (Pain, Chronic Illness,
                     Pregnancy). Calendar renders once on onset; never expanded.
    - ``RANGE``    — bounded episode with a known end (3-day flu, surgical
                     recovery, hospital admission). One card with ``endDate``.
    - ``RECURRING``— repeats on a schedule declared via ``event_metadata.frequency``
                     (weekly physio, monthly check-in). Expanded per recurrence.
    - ``POINT``    — single incident (a fall, an injury). One card on the date.
    """

    STATE = "state"
    RANGE = "range"
    RECURRING = "recurring"
    POINT = "point"

    @classmethod
    def from_string(cls, value):
        if value is None:
            return None
        try:
            return cls(value)
        except ValueError:
            return None


class NotificationType(enum.StrEnum):
    MEDICATION_REMINDER = "MEDICATION_REMINDER"
    EXAMINATION_REMINDER = "EXAMINATION_REMINDER"
    BIOMARKER_ALERT = "BIOMARKER_ALERT"
    BIOMARKER_THRESHOLD = "BIOMARKER_THRESHOLD"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    CALENDAR_EVENT = "CALENDAR_EVENT"
    AI_SUGGESTION = "AI_SUGGESTION"
    HITL_TASK = "HITL_TASK"
    AGENT_RESULT = "AGENT_RESULT"
    INTEGRATION_EVENT = "INTEGRATION_EVENT"
    SYNC_FAILURE = "SYNC_FAILURE"
    SYSTEM_UPDATE = "SYSTEM_UPDATE"
    SYSTEM_BROADCAST = "SYSTEM_BROADCAST"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    CLINICAL_EVENT = "CLINICAL_EVENT"
    CUSTOM = "CUSTOM"


class NotificationSource(enum.StrEnum):
    """Origin system of a notification event."""

    SYSTEM = "SYSTEM"
    INTEGRATION = "INTEGRATION"
    AGENT = "AGENT"
    RULE = "RULE"
    CLINICAL = "CLINICAL"
    SCHEDULED = "SCHEDULED"


class NotificationCategory(enum.StrEnum):
    """UI grouping for the notification center."""

    REMINDER = "reminder"
    ALERT = "alert"
    HITL = "hitl"
    AGENT = "agent"
    SYSTEM = "system"
    INTEGRATION = "integration"
    CLINICAL_EVENT = "clinical_event"


class NotificationSeverity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RecipientKind(enum.StrEnum):
    """The principal kind a notification target was specified as (pre-resolution)."""

    USER = "USER"
    PATIENT = "PATIENT"
    DOCTOR = "DOCTOR"
    TENANT = "TENANT"
    SYSTEM = "SYSTEM"


class RecipientStatus(enum.StrEnum):
    """Per-recipient inbox state (the user-facing read/dismiss lifecycle)."""

    UNREAD = "unread"
    READ = "read"
    DISMISSED = "dismissed"


class NotificationChannel(enum.StrEnum):
    IN_APP = "IN_APP"
    PUSH = "PUSH"
    EMAIL = "EMAIL"
    SMS = "SMS"


class NotificationStatus(enum.StrEnum):
    """Per-channel delivery lifecycle (delivery log)."""

    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class NotificationRuleType(enum.StrEnum):
    """What a NotificationRule evaluates."""

    BIOMARKER_THRESHOLD = "BIOMARKER_THRESHOLD"
    OUT_OF_NORMAL_RANGE = "OUT_OF_NORMAL_RANGE"
    TREND_ANOMALY = "TREND_ANOMALY"
    EVENT_LIFECYCLE = "EVENT_LIFECYCLE"


class ComparisonOperator(enum.StrEnum):
    GT = ">"
    LT = "<"
    GTE = ">="
    LTE = "<="
    EQ = "=="
    OUT_OF_NORMAL = "out_of_normal"


class HitlTaskStatus(enum.StrEnum):
    """Status of a human-in-the-loop task card proposed by the AI assistant.
    Values are lowercase to match the JSONB payload contract consumed by the
    frontend (registry.tsx HITL_STATUS_META keys)."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    DISMISSED = "dismissed"

    @classmethod
    def terminal(cls):
        """Return the set of statuses that block a resume continuation turn
        (i.e. the user has finished acting on the proposal)."""
        return frozenset({cls.CONFIRMED, cls.DISMISSED, cls.FAILED})


class TriggerType(enum.StrEnum):
    TIME = "TIME"
    RECURRING = "RECURRING"
    EVENT = "EVENT"
    THRESHOLD = "THRESHOLD"


class Gender(enum.StrEnum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class MedicationStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ENTERED_IN_ERROR = "ENTERED_IN_ERROR"
    INTENDED = "INTENDED"
    STOPPED = "STOPPED"
    ON_HOLD = "ON_HOLD"
    UNKNOWN = "UNKNOWN"


class MedicationIntent(enum.StrEnum):
    """Discriminator for whether a Medication row is a MedicationStatement
    (what the patient is taking) or a MedicationRequest (what was prescribed).

    Used by the R4 facade to route a single ``fhir_medications`` row to either
    ``/fhir/R4/MedicationStatement`` or ``/fhir/R4/MedicationRequest``. Audit
    items C11 + C12: one table serves both FHIR resources.
    """

    STATEMENT = "statement"
    ORDER = "order"
    PLAN = "plan"
    PROPOSAL = "proposal"


class AIScope(enum.StrEnum):
    SYSTEM = "SYSTEM"
    TENANT = "TENANT"
    USER = "USER"
    ORGANIZATION = "ORGANIZATION"


class AIModelCapability(enum.StrEnum):
    """The input/output modalities a model supports (its "features").

    Family vocabulary (ai-features §15, frozen 2026-09-19):
    ``text | vision | tools | stt | tts | embeddings``.

    A model carries a SET of these (stored as a JSONB array on ``AIModel``).
    Tasks require specific capabilities — a model is only eligible for a task
    assignment when it advertises the capability that task needs:

      * ``TEXT``        — text in/out (chat, structured extraction, definitions).
                          Assumed for every model (the baseline modality).
      * ``VISION``      — image input (multimodal chat, vision-based OCR).
      * ``TOOLS``       — tool/function calling.
      * ``STT``         — speech-to-text (``whisper-1``; the ``transcription``
                          task). Replaces the legacy ``audio_input`` value.
      * ``TTS``         — text-to-speech (no health task consumes it yet).
      * ``EMBEDDINGS``  — embedding vectors (no health task consumes it yet).
    """

    TEXT = "text"
    VISION = "vision"
    TOOLS = "tools"
    STT = "stt"
    TTS = "tts"
    EMBEDDINGS = "embeddings"

    @classmethod
    def all_values(cls) -> list:
        return [c.value for c in cls]

    @classmethod
    def from_string(cls, value):
        if value is None:
            return None
        try:
            return cls(value)
        except ValueError:
            return None


class ImportFormat(enum.StrEnum):
    CSV = "CSV"
    JSON = "JSON"
    FHIR = "FHIR"
    PDF = "PDF"
    IMAGE = "IMAGE"


class ImportSourceType(enum.StrEnum):
    FILE_UPLOAD = "FILE_UPLOAD"
    URL = "URL"
    MANUAL_ENTRY = "MANUAL_ENTRY"
    WEARABLE = "WEARABLE"
    LAB_SYSTEM = "LAB_SYSTEM"


class ImportStatus(enum.StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class ImmunizationStatus(enum.StrEnum):
    """FHIR R4 Immunization.status (closed value set)."""

    COMPLETED = "completed"
    ENTERED_IN_ERROR = "entered-in-error"
    NOT_DONE = "not-done"


class AllergyCategory(enum.StrEnum):
    FOOD = "FOOD"
    MEDICATION = "MEDICATION"
    ENVIRONMENT = "ENVIRONMENT"
    BIOLOGIC = "BIOLOGIC"
    OTHER = "OTHER"


class AllergyCriticality(enum.StrEnum):
    LOW = "LOW"
    HIGH = "HIGH"
    UNABLE_TO_ASSESS = "UNABLE_TO_ASSESS"


class AllergyClinicalStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    RESOLVED = "RESOLVED"


class ReactionSeverity(enum.StrEnum):
    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"


class Role(enum.StrEnum):
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    USER = "USER"


class CatalogScope(enum.StrEnum):
    """The visibility/ownership tier of a catalog item.

    Drives the ownership-based access model (plan §1):

    - ``SYSTEM``  — canonical reference (shipped seeds / curated). ``tenant_id``
      is NULL; only SYSTEM_ADMIN may modify.
    - ``TENANT``  — shared across the tenant. ADMIN/MANAGER of that tenant may
      modify.
    - ``USER``    — personal entry by ``created_by``. The creator + ADMIN may
      modify; visible to the whole tenant (read).
    """

    SYSTEM = "system"
    TENANT = "tenant"
    USER = "user"


class OrganizationType(enum.StrEnum):
    HOUSEHOLD = "HOUSEHOLD"
    CLINIC = "CLINIC"
    DEPARTMENT = "DEPARTMENT"
    PROVIDER_GROUP = "PROVIDER_GROUP"
    HOSPITAL = "HOSPITAL"
    OTHER = "OTHER"


class CodingSystem(enum.StrEnum):
    LOINC = "loinc"
    SNOMED = "snomed"
    CUSTOM = "custom"

    @property
    def fhir_system(self) -> str:
        """The canonical FHIR ``system`` URL for this coding system."""
        if self == CodingSystem.LOINC:
            return "http://loinc.org"
        elif self == CodingSystem.SNOMED:
            return "http://snomed.info/sct"
        return "urn:uuid:health-assistant:custom-biomarker"


class BiomarkerValueType(enum.StrEnum):
    """The shape of values a ``BiomarkerDefinition`` accepts.

    A true discriminator — every consumer (analytics, OCR pipeline, FHIR
    serialization, telemetry routing, the frontend) branches on this
    explicitly. No silent ``float()`` calls on unknown shapes.

    - ``QUANTITY`` — numeric value stored in ``Observation.value_quantity``
      with a ``Unit`` and numeric reference ranges. The default and only
      pre-state-biomarker behavior.
    - ``STATE``    — categorical value stored in
      ``Observation.value_codeable_concept``, drawn from the biomarker's
      ``allowed_states`` set (a controlled vocabulary sourced from HL7
      v3-ObservationInterpretation / SNOMED / DataAbsentReason). The
      "normal set" (``BiomarkerAllowedState.is_normal``) replaces numeric
      reference ranges. Telemetry is hard-blocked.
    """

    QUANTITY = "quantity"
    STATE = "state"


class IntegrationStatus(enum.StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class ExportScope(enum.StrEnum):
    PATIENT = "patient"
    GROUP = "group"
    SYSTEM = "system"


class ExportType(enum.StrEnum):
    FHIR_ONLY = "fhir_only"
    FULL_BACKUP = "full_backup"
    CATALOG_ONLY = "catalog_only"


class JobStatus(enum.StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class AnatomyRelationType(enum.StrEnum):
    """.. deprecated:: Migrated into :class:`ConceptRelationType`.

    Retained as a thin alias so schema/endpoint code that still references it
    by name compiles during the transition. All anatomy hierarchy edges now
    live in ``concept_edges`` with these same string values.
    """

    PART_OF = "PART_OF"
    BRANCH_OF = "BRANCH_OF"
    DRAINS_INTO = "DRAINS_INTO"
    ARTICULATES_WITH = "ARTICULATES_WITH"
    INNERVATED_BY = "INNERVATED_BY"
    SUPPLIED_BY = "SUPPLIED_BY"
    CONTINUOUS_WITH = "CONTINUOUS_WITH"


class ConceptKind(enum.StrEnum):
    """The domain a Concept belongs to in the unified taxonomy.

    Values are lowercase short codes (used verbatim as FHIR CodeSystem codes
    and as API ``?kind=`` query params). Add new domains here — no schema
    change required (the migration creates the enum type idempotently).
    """

    SPECIALTY = "specialty"
    EXAMINATION_CATEGORY = "examination_category"
    EVENT_CATEGORY = "event_category"
    BIOMARKER_CLASS = "biomarker_class"
    BIOMARKER_PANEL = "biomarker_panel"
    ANATOMY_CLASS = "anatomy_class"
    VACCINE_CLASS = "vaccine_class"
    MEDICATION_CLASS = "medication_class"
    DOCUMENT_CATEGORY = "document_category"
    DISEASE = "disease"
    BODY_SYSTEM = "body_system"
    PROCEDURE = "procedure"
    LIFESTYLE = "lifestyle"
    FACTOR = "factor"
    SYMPTOM = "symptom"
    ORGAN = "organ"


class ConceptStatus(enum.StrEnum):
    """Lifecycle of a Concept (mirrors FHIR CodeSystem concept status)."""

    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class ConceptProvenance(enum.StrEnum):
    """Where a Concept or ConceptEdge originated.

    Drives the curated-wins conflict resolution (``seed`` > ``integration`` >
    ``ai`` > ``manual``) and gates HITL review for ``ai`` rows.
    """

    SEED = "seed"
    INTEGRATION = "integration"
    AI = "ai"
    MANUAL = "manual"


class EdgeApprovalStatus(enum.StrEnum):
    """Approval state of a ConceptEdge.

    Only ``approved`` rows count for graph queries; ``proposed`` rows are
    HITL-pending (AI suggestions). ``rejected`` rows are kept for audit.
    """

    APPROVED = "approved"
    PROPOSED = "proposed"
    REJECTED = "rejected"


class EdgeEndpointType(enum.StrEnum):
    """Polymorphic type tag for a ConceptEdge endpoint.

    ``concept`` endpoints reference ``concepts.id``; all others reference the
    primary key of a domain entity table (biomarker_definitions, doctors,
    examinations, etc.). There is no hard FK across tables — referential
    integrity is enforced in the service layer + a nightly orphan-cleanup job.
    """

    CONCEPT = "concept"
    BIOMARKER = "biomarker"
    MEDICATION = "medication"
    CLINICAL_EVENT_TYPE = "clinical_event_type"
    ALLERGY = "allergy"
    IMMUNIZATION = "immunization"
    OBSERVATION = "observation"
    DOCTOR = "doctor"
    EXAMINATION = "examination"
    ANATOMY = "anatomy"
    DOCUMENT = "document"


class MetadataFieldType(enum.StrEnum):
    """Discriminator for a ``ClinicalEventType.metadata_schema`` field.

    Drives the frontend renderer switch in ``DynamicMetadataForm``. Each value
    maps 1:1 to a render branch — the union is exhaustive (TS ``never`` guard),
    so adding a new field type without a renderer branch is a compile error
    (closes the legacy dead-branch class of bugs where ``select``/``code``
    silently rendered nothing).

    Values are lowercase kebab to match the JSONB wire format consumed verbatim
    by the frontend ``MetadataFieldType`` literal union.
    """

    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    CATALOG_SELECT = "catalog-select"


class CatalogType(enum.StrEnum):
    """The searchable catalog domains a ``CATALOG_SELECT`` metadata field can
    reference.

    Mirrors the frontend ``CatalogType`` literal union
    (``frontend/src/types/catalog.ts``) and the catalog registry type keys
    (``app/catalogs/registrations.py``). Values are lowercase so the same
    string flows seed → JSONB → frontend unchanged.
    """

    BIOMARKER = "biomarker"
    MEDICATION = "medication"
    ALLERGY = "allergy"
    ANATOMY = "anatomy"
    VACCINE = "vaccine"
    CONCEPT = "concept"


class CatalogRelationType(enum.StrEnum):
    """How a picked catalog item in a ``CATALOG_SELECT`` field relates to the
    clinical event.

    Generalizes the legacy ``EventAnatomyLink.relation_type`` free-text values
    (``primary_site``/``radiates_to``/``referred_to``) into an enum, and adds
    the semantic medical-knowledge relations so a non-anatomy field can declare
    its binding semantics (e.g. a biomarker field ``relation=monitors``).
    """

    PRIMARY_SITE = "primary_site"
    RADIATES_TO = "radiates_to"
    REFERRED_TO = "referred_to"
    MONITORS = "monitors"
    TREATS = "treats"
    INDICATES = "indicates"


class ConceptRelationType(enum.StrEnum):
    """Typed relationships between Concepts, or between an entity and a Concept.

    Split into two groups: **structural / classification** (single-valued
    classification is usually a direct FK on the entity table; these cover the
    M:N and cross-domain cases) and **semantic / medical knowledge** (the graph
    that powers correlations and recommendations).
    """

    # --- structural / classification -----------------------------------------
    MEMBER_OF = "MEMBER_OF"
    HAS_SPECIALTY = "HAS_SPECIALTY"
    CLASSIFIED_AS = "CLASSIFIED_AS"
    EXAMINES = "EXAMINES"
    IMAGES = "IMAGES"
    PERFORMS = "PERFORMS"
    ORDERS = "ORDERS"
    LOCATED_IN = "LOCATED_IN"
    PART_OF = "PART_OF"

    # --- anatomy hierarchy (migrated from AnatomyRelationType) ---------------
    BRANCH_OF = "BRANCH_OF"
    DRAINS_INTO = "DRAINS_INTO"
    ARTICULATES_WITH = "ARTICULATES_WITH"
    INNERVATED_BY = "INNERVATED_BY"
    SUPPLIED_BY = "SUPPLIED_BY"
    CONTINUOUS_WITH = "CONTINUOUS_WITH"

    # --- semantic / medical knowledge ----------------------------------------
    AFFECTS = "AFFECTS"
    TREATS = "TREATS"
    INDICATES = "INDICATES"
    PREVENTS = "PREVENTS"
    CONTRAINDICATES = "CONTRAINDICATES"
    CORRELATES_WITH = "CORRELATES_WITH"
    CAUSED_BY = "CAUSED_BY"
    MONITORS = "MONITORS"
    RISK_OF = "RISK_OF"
    SCREENS_FOR = "SCREENS_FOR"
