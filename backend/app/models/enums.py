import enum

class QuantityType(str, enum.Enum):
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

class ClinicalEventStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    ON_HOLD = "ON_HOLD"
    UNKNOWN = "UNKNOWN"

class NotificationType(str, enum.Enum):
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


class NotificationSource(str, enum.Enum):
    """Origin system of a notification event."""
    SYSTEM = "SYSTEM"
    INTEGRATION = "INTEGRATION"
    AGENT = "AGENT"
    RULE = "RULE"
    CLINICAL = "CLINICAL"
    SCHEDULED = "SCHEDULED"


class NotificationCategory(str, enum.Enum):
    """UI grouping for the notification center."""
    REMINDER = "reminder"
    ALERT = "alert"
    HITL = "hitl"
    AGENT = "agent"
    SYSTEM = "system"
    INTEGRATION = "integration"
    CLINICAL_EVENT = "clinical_event"


class NotificationSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RecipientKind(str, enum.Enum):
    """The principal kind a notification target was specified as (pre-resolution)."""
    USER = "USER"
    PATIENT = "PATIENT"
    DOCTOR = "DOCTOR"
    TENANT = "TENANT"
    SYSTEM = "SYSTEM"


class RecipientStatus(str, enum.Enum):
    """Per-recipient inbox state (the user-facing read/dismiss lifecycle)."""
    UNREAD = "unread"
    READ = "read"
    DISMISSED = "dismissed"


class NotificationChannel(str, enum.Enum):
    IN_APP = "IN_APP"
    PUSH = "PUSH"
    EMAIL = "EMAIL"
    SMS = "SMS"


class NotificationStatus(str, enum.Enum):
    """Per-channel delivery lifecycle (delivery log)."""
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class NotificationRuleType(str, enum.Enum):
    """What a NotificationRule evaluates."""
    BIOMARKER_THRESHOLD = "BIOMARKER_THRESHOLD"
    OUT_OF_NORMAL_RANGE = "OUT_OF_NORMAL_RANGE"
    TREND_ANOMALY = "TREND_ANOMALY"
    EVENT_LIFECYCLE = "EVENT_LIFECYCLE"


class ComparisonOperator(str, enum.Enum):
    GT = ">"
    LT = "<"
    GTE = ">="
    LTE = "<="
    EQ = "=="
    OUT_OF_NORMAL = "out_of_normal"

class HitlTaskStatus(str, enum.Enum):
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

class TriggerType(str, enum.Enum):
    TIME = "TIME"
    RECURRING = "RECURRING"
    EVENT = "EVENT"
    THRESHOLD = "THRESHOLD"

class Gender(str, enum.Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"

class MedicationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ENTERED_IN_ERROR = "ENTERED_IN_ERROR"
    INTENDED = "INTENDED"
    STOPPED = "STOPPED"
    ON_HOLD = "ON_HOLD"
    UNKNOWN = "UNKNOWN"


class MedicationIntent(str, enum.Enum):
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

class AIScope(str, enum.Enum):
    SYSTEM = "SYSTEM"
    TENANT = "TENANT"
    USER = "USER"
    ORGANIZATION = "ORGANIZATION"

class ImportFormat(str, enum.Enum):
    CSV = "CSV"
    JSON = "JSON"
    FHIR = "FHIR"
    PDF = "PDF"
    IMAGE = "IMAGE"

class ImportSourceType(str, enum.Enum):
    FILE_UPLOAD = "FILE_UPLOAD"
    URL = "URL"
    MANUAL_ENTRY = "MANUAL_ENTRY"
    WEARABLE = "WEARABLE"
    LAB_SYSTEM = "LAB_SYSTEM"

class ImportStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"

class AllergyCategory(str, enum.Enum):
    FOOD = "FOOD"
    MEDICATION = "MEDICATION"
    ENVIRONMENT = "ENVIRONMENT"
    BIOLOGIC = "BIOLOGIC"
    OTHER = "OTHER"

class AllergyCriticality(str, enum.Enum):
    LOW = "LOW"
    HIGH = "HIGH"
    UNABLE_TO_ASSESS = "UNABLE_TO_ASSESS"

class AllergyClinicalStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    RESOLVED = "RESOLVED"

class ReactionSeverity(str, enum.Enum):
    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"

class Role(str, enum.Enum):
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    USER = "USER"

class OrganizationType(str, enum.Enum):
    HOUSEHOLD = "HOUSEHOLD"
    CLINIC = "CLINIC"
    DEPARTMENT = "DEPARTMENT"
    PROVIDER_GROUP = "PROVIDER_GROUP"
    HOSPITAL = "HOSPITAL"
    OTHER = "OTHER"

class CodingSystem(str, enum.Enum):
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

class IntegrationStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class ExportScope(str, enum.Enum):
    PATIENT = "patient"
    GROUP = "group"
    SYSTEM = "system"


class ExportType(str, enum.Enum):
    FHIR_ONLY = "fhir_only"
    FULL_BACKUP = "full_backup"
    CATALOG_ONLY = "catalog_only"


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


class AnatomyCategory(str, enum.Enum):
    SYSTEM = "SYSTEM"
    REGION = "REGION"
    ORGAN = "ORGAN"
    ORGAN_PART = "ORGAN_PART"
    TISSUE = "TISSUE"
    CELL = "CELL"
    SUBSTANCE = "SUBSTANCE"
    JOINT = "JOINT"
    OTHER = "OTHER"

class AnatomyRelationType(str, enum.Enum):
    PART_OF = "PART_OF"
    BRANCH_OF = "BRANCH_OF"
    DRAINS_INTO = "DRAINS_INTO"
    ARTICULATES_WITH = "ARTICULATES_WITH"
    INNERVATED_BY = "INNERVATED_BY"
    SUPPLIED_BY = "SUPPLIED_BY"
    CONTINUOUS_WITH = "CONTINUOUS_WITH"
