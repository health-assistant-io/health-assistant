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
    CALENDAR_EVENT = "CALENDAR_EVENT"
    AI_SUGGESTION = "AI_SUGGESTION"
    SYSTEM_UPDATE = "SYSTEM_UPDATE"
    CUSTOM = "CUSTOM"

class NotificationChannel(str, enum.Enum):
    IN_APP = "IN_APP"
    PUSH = "PUSH"
    EMAIL = "EMAIL"
    SMS = "SMS"

class NotificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    READ = "READ"
    DISMISSED = "DISMISSED"
    FAILED = "FAILED"

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

