"""FHIR resource schemas"""

from .patient import (
    PatientCreate,
    PatientUpdate,
    PatientResponse,
)
from app.models.enums import Gender
from .observation import (
    ObservationCreate,
    ObservationUpdate,
    ObservationResponse,
    ObservationList,
)
from .diagnostic_report import (
    DiagnosticReportCreate,
    DiagnosticReportUpdate,
    DiagnosticReportResponse,
)
from .medication import (
    MedicationCreate,
    MedicationUpdate,
    MedicationResponse,
    MedicationList,
)

__all__ = [
    # Patient
    "PatientCreate",
    "PatientUpdate",
    "PatientResponse",
    "Gender",
    # Observation
    "ObservationCreate",
    "ObservationUpdate",
    "ObservationResponse",
    "ObservationList",
    # Diagnostic Report
    "DiagnosticReportCreate",
    "DiagnosticReportUpdate",
    "DiagnosticReportResponse",
    # Medication
    "MedicationCreate",
    "MedicationUpdate",
    "MedicationResponse",
    "MedicationList",
]
