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
    """ADR-0015 wire status of a human-in-the-loop task card.

    This is the **card contract** vocabulary the frontend renders
    (``registry.tsx`` ``HITL_STATUS_META`` keys). It is deliberately not the
    persistence vocabulary: ``proposals.status`` is a seven-state machine
    (:class:`ProposalStatus`) and the mapping lives in one module
    (``app.services.proposal_state``). Values are lowercase to match the wire.
    """

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    DISMISSED = "dismissed"

    @classmethod
    def terminal(cls):
        """Return the set of statuses that block a resume continuation turn
        (i.e. the user has finished acting on the proposal)."""
        return frozenset({cls.CONFIRMED, cls.DISMISSED, cls.FAILED})


class ProposalSource(enum.StrEnum):
    """Where a proposal entered the system (``proposals.source``).

    Closed behavioral vocabulary (constitution rule 7a): the column is a
    varchar + mirrored CHECK. ``source_ref`` names the origin row in that
    source's vocabulary — ``chat_message_id`` / ``user_integration_id`` /
    ``background_job_id``.
    """

    CHAT = "chat"
    EXTENSION = "extension"
    AUTOMATION = "automation"


class ProposalStatus(enum.StrEnum):
    """The one HITL proposal state machine (``proposals.status``, plan 5).

    Closed behavioral vocabulary (constitution rule 7a): varchar + mirrored
    CHECK, widened by a migration in the same commit as a new state. The legal
    transitions live in exactly one module — :mod:`app.services.proposals.proposal_state`
    — which raises typed errors for illegal moves (never a silent write).
    """

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"

    @classmethod
    def terminal(cls) -> frozenset["ProposalStatus"]:
        """States from which nothing can be applied any more."""
        return frozenset(
            {
                cls.REJECTED,
                cls.APPLIED,
                cls.FAILED,
                cls.EXPIRED,
                cls.SUPERSEDED,
            }
        )

    @classmethod
    def pending(cls) -> frozenset["ProposalStatus"]:
        """States a resolver/sweeper may still act on."""
        return frozenset({cls.PROPOSED, cls.APPROVED})


class TelemetryAnomalyKind(enum.StrEnum):
    """Direction of a telemetry anomaly (``telemetry_anomalies.kind``).

    Closed behavioral vocabulary (constitution rule 7a): varchar + mirrored
    CHECK. The CAgg rolling-stats engine classifies the latest bucket by the
    sign of its z-score — above the trailing baseline is a spike, below is a
    drop. Detection never branches on a slug; every metric uses the same
    engine.
    """

    SPIKE = "spike"
    DROP = "drop"


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


class AICallRoute(enum.StrEnum):
    """The call shape recorded on ``ai_call_logs.route``.

    Closed behavioral vocabulary (constitution rule 7a): the DB column is a
    native PG enum carrying exactly these values, widened by a migration in the
    same commit as a new member. Mirrors
    :class:`app.ai.providers.task_registry.TaskRoute`.
    """

    CHAT = "chat"
    DECISION = "decision"
    EMBED = "embed"
    STT = "stt"
    OCR = "ocr"
    EXTRACT = "extract"


class AICallStatus(enum.StrEnum):
    """Outcome of one audited AI call (``ai_call_logs.status``).

    ``cancelled`` = the caller went away (client disconnect / task cancelled);
    ``timeout`` = the abort/timeout family fired; ``error`` = every other
    failure, with ``error_class`` carrying the typed machine class.
    """

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


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
                          task).
      * ``TTS``         — text-to-speech (no health task consumes it yet).
      * ``EMBEDDINGS``  — embedding vectors (the ``embed`` task; setup
                          gap-fills the slot from the preset default).
      * ``DECISION``    — decision models (plan 26 / ADR-0032). Sparse and
                          explicit-only, never inferred from a model name:
                          a decision model serves the ``decision_*`` task
                          slots ONLY, and those slots never accept anything
                          else (D4 slot separation, both directions).
    """

    TEXT = "text"
    VISION = "vision"
    TOOLS = "tools"
    STT = "stt"
    TTS = "tts"
    EMBEDDINGS = "embeddings"
    DECISION = "decision"

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


class IntegrationCredentialKind(enum.StrEnum):
    """Credential shape held by one ``integration_credentials`` row.

    Closed behavioral vocabulary (constitution rule 7a): varchar + mirrored
    CHECK. ``oauth2`` is a refreshable access/refresh token pair, ``api_key``
    a long-lived bearer/API token, ``signed`` the HMAC material inbound
    machine routes verify. The sealed payload is opaque to the engine; only
    the owning connector interprets it.
    """

    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    SIGNED = "signed"


class IntegrationSyncStream(enum.StrEnum):
    """Typed data streams of the connector framework (plan 8 §2.2/§2.3).

    Closed behavioral vocabulary: adding a stream changes what the sync
    engine routes, so it is a StrEnum + mirrored CHECK (one migration in the
    same commit). ``cursor_key`` names a provider-scoped sub-cursor inside a
    stream (a FHIR server pulls several resource types under
    ``observations``); the empty string is the stream's primary cursor.
    """

    OBSERVATIONS = "observations"
    DOCUMENTS = "documents"
    APPOINTMENTS = "appointments"
    LABS = "labs"
    TELEMETRY = "telemetry"


class IntegrationCursorKind(enum.StrEnum):
    """Where a sync cursor advances to.

    Closed behavioral vocabulary. ``timestamp`` is an ISO-8601 instant,
    ``page_token`` an opaque provider pagination token, ``etag`` a
    representation validator, ``commit`` an opaque source commit/checkpoint
    token (JSON text when structured).
    """

    TIMESTAMP = "timestamp"
    PAGE_TOKEN = "page_token"
    ETAG = "etag"
    COMMIT = "commit"


class IntegrationSyncErrorClass(enum.StrEnum):
    """Typed failure class recorded on sync-state and sync-log rows.

    Closed behavioral vocabulary = the charter's 15 extension error codes
    (``app.extensions.polite_http.ERROR_CODES`` — one source, pinned by a
    parity test) plus the two engine-only classes: ``mapping_gap`` (an
    observation that could not be mapped to a catalog biomarker and was
    stored raw) and ``credential`` (a missing/revoked/unreadable credential —
    the fail-closed auth gate).
    """

    NOT_READY = "not-ready"
    NEEDS_SECRET = "needs-secret"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"
    IO = "io"
    NOT_FOUND = "not-found"
    PATH_DENIED = "path-denied"
    NET_DENIED = "net-denied"
    AUTH = "auth"
    RATE_LIMITED = "rate-limited"
    UNREACHABLE = "unreachable"
    PROTOCOL = "protocol"
    PROVIDER = "provider"
    BUDGET = "budget"
    CANCELED = "canceled"
    MAPPING_GAP = "mapping_gap"
    CREDENTIAL = "credential"


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
    (closes the dead-branch class of bugs where ``select``/``code`` silently
    rendered nothing).

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

    Generalizes the ``EventAnatomyLink.relation_type`` free-text values
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


class ErasureSubjectType(enum.StrEnum):
    """The erased subject kind (``erasure_requests.subject_type``).

    Closed behavioral vocabulary (constitution rule 7a): varchar + mirrored
    CHECK. ``user`` erases the account and every patient it owns, ``patient``
    erases one clinical record, ``tenant`` erases the whole isolation boundary.
    """

    USER = "user"
    PATIENT = "patient"
    TENANT = "tenant"


class ErasureStatus(enum.StrEnum):
    """Erasure request lifecycle (``erasure_requests.status``).

    ``queued`` → ``running`` → ``done`` (or ``failed``); a dry run ends at
    ``dry_run_done`` and never deletes. ``done`` rows are the erasure ledger
    replayed against every restore (plan 6 §2.7).
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DRY_RUN_DONE = "dry_run_done"


class RetentionScope(enum.StrEnum):
    """What a retention policy governs (``retention_policies.scope``)."""

    CLASS = "class"
    TABLE = "table"


class RetentionAction(enum.StrEnum):
    """What a retention job does at the cutoff (``retention_policies.action``).

    ``delete`` removes rows; ``anonymize`` keeps the row but clears its
    payload (the audit-retention shape). Company retention law picks the
    action — see SECURITY.md's erasure/retention subsection.
    """

    DELETE = "delete"
    ANONYMIZE = "anonymize"


class ExtensionSourceKind(enum.StrEnum):
    """Where imported extension content comes from (``extension_sources.kind``).

    Closed behavioral vocabulary (constitution rule 7a): varchar + mirrored
    CHECK. ``git`` resolves a repository ref, ``registry`` an index entry,
    ``file`` a local/dev source descriptor (the charter's
    ``localSourceDescriptor`` — production boots fail it closed).
    """

    GIT = "git"
    FILE = "file"
    REGISTRY = "registry"


class ExtensionSourceTrustTier(enum.StrEnum):
    """The trust tier a source's lane imports (``extension_sources.trust_tier``).

    The source lane carries **imported code only** (plan 7): ``T2`` sandboxed
    capability-RPC content and ``T3`` external MCP processes. Declarative T0
    data never rides this lane (the charter's envelope-only file import), and
    bundled T1 content has no source. Tier is provenance — the value gates
    which lanes an import may claim; ``contract.derive_tier`` still derives
    the artifact's real tier at registration.
    """

    T2 = "T2"
    T3 = "T3"


class ExtensionTier(enum.StrEnum):
    """The charter's tier ladder (``extension_isolation_posture`` tier columns).

    Closed behavioral vocabulary mirroring the charter's ``tiers``. The
    posture row records ``requested_tier`` (the source's lane claim) next to
    ``derived_tier`` (``contract.derive_tier`` — the one derivation), so a
    contradiction is queryable evidence, never a silent retier.
    """

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class ExtensionVersionStatus(enum.StrEnum):
    """The immutable content-version state machine
    (``extension_source_versions.status``).

    ``pending`` (resolved, not yet reviewed) → ``approved`` (the digest an
    instance may run) or ``rejected`` (registration problems — the typed
    reason lives in the row, never only a log). ``revoked`` pulls an
    already-approved digest back. Content columns never change: a digest
    change is a NEW row requiring re-approval (the consent anchor).
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class ExtensionConsentDecision(enum.StrEnum):
    """One principal's decision on one capability of one content digest
    (``extension_consents.decision``).

    Consent binds to a manifest DIGEST (via ``version_id``): an upgraded
    extension re-prompts for what is new; unchanged capabilities carry their
    grant forward as new rows (browser-permission model, ADR-0040).
    """

    GRANTED = "granted"
    DENIED = "denied"
