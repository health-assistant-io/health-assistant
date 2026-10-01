"""FHIR resource schemas"""

from app.models.enums import Gender

from .diagnostic_report import (
    DiagnosticReportCreate,
    DiagnosticReportResponse,
    DiagnosticReportUpdate,
)
from .medication import (
    MedicationCreate,
    MedicationList,
    MedicationResponse,
    MedicationUpdate,
)
from .observation import (
    ObservationCreate,
    ObservationList,
    ObservationResponse,
    ObservationUpdate,
)
from .patient import (
    PatientCreate,
    PatientResponse,
    PatientUpdate,
)

__all__ = [
    # Diagnostic Report
    "DiagnosticReportCreate",
    "DiagnosticReportResponse",
    "DiagnosticReportUpdate",
    "Gender",
    # Medication
    "MedicationCreate",
    "MedicationList",
    "MedicationResponse",
    "MedicationUpdate",
    # Observation
    "ObservationCreate",
    "ObservationList",
    "ObservationResponse",
    "ObservationUpdate",
    # Patient
    "PatientCreate",
    "PatientResponse",
    "PatientUpdate",
]
