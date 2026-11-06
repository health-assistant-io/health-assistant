# ruff: noqa: F401 -- the original module prelude, kept verbatim;
# imports here are the package's shared namespace (F401 is the point).

import hashlib
import json
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.converters import parse_dt as _parse_dt
from app.core.converters import to_uuid as _uuid
from app.models.anatomy_model import AnatomyStructure
from app.models.clinical_event import (
    ClinicalEvent,
    ClinicalEventType,
)
from app.models.concept_model import Concept, ConceptEdge, ConceptKindTag
from app.models.document_model import DocumentModel
from app.models.enums import (
    AllergyCategory,
    AllergyClinicalStatus,
    AllergyCriticality,
    CodingSystem,
    ConceptKind,
    ConceptProvenance,
    ConceptRelationType,
    ConceptStatus,
    EdgeApprovalStatus,
    EdgeEndpointType,
    Gender,
    JobStatus,
    MedicationStatus,
)
from app.models.examination_model import ExaminationModel
from app.models.export_import_job import ImportJobModel
from app.models.fhir.allergy import AllergyCatalog, AllergyIntolerance
from app.models.fhir.communication import CommunicationModel
from app.models.fhir.device import DeviceModel
from app.models.fhir.medication import Medication, MedicationCatalog
from app.models.fhir.organization import OrganizationModel
from app.models.fhir.patient import DiagnosticReport, Observation, Patient
from app.models.fhir.provenance import ProvenanceModel
from app.models.notification import NotificationTrigger
from app.models.telemetry_model import TelemetryDataModel
from app.models.user_integration import UserIntegration
from app.schemas.backup import BackupManifest, RestoreResult
from app.schemas.import_data import (
    CSVImportConfig,
    FHIRImportConfig,
    ImportResult,
    ImportStatus,
)
from app.services.fhir.fhir_converter import (
    fhir_to_orm,
    validate_bundle,
)
from app.services.fhir.fhir_helpers import coerce_patient_id

logger = logging.getLogger(__name__)


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v if not isinstance(v, datetime) else v.date()
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError, AttributeError):
        return None


# ---------------- FHIR resource type -> ORM model (for verb routing) ----------------
#
# Used by DELETE (soft-delete) and POST+ifNoneExist (conditional create) routing
# in ``_restore_one_fhir_resource``. Practitioner is resolved lazily because
# ``DoctorModel`` is imported lazily elsewhere to avoid a circular import.
# MedicationRequest maps to the same ``Medication`` table as MedicationStatement
# (distinguished by the ``intent`` discriminator).
_RESOURCE_TYPE_TO_MODEL: dict[str, Any] = {
    "Patient": Patient,
    "Observation": Observation,
    "MedicationStatement": Medication,
    "MedicationRequest": Medication,
    "AllergyIntolerance": AllergyIntolerance,
    "DiagnosticReport": DiagnosticReport,
    "Organization": OrganizationModel,
    "Condition": ClinicalEvent,
    "Encounter": ExaminationModel,
    "Device": DeviceModel,
    "Communication": CommunicationModel,
    "Provenance": ProvenanceModel,
}


# ---------------- FHIR reference field → resource type (G11) ----------------
#
# Used by _apply_remap to route bare urn:uuid: references to the correct
# resource type. Entries with None are ambiguous (could be several types) and
# are resolved by a bundle look-ahead (urn_type_index built in restore_fhir_bundle).
FIELD_HINT_TO_TYPE: dict[str, str | None] = {
    "subject": "Patient",
    "patient": "Patient",
    "performer": "Practitioner",
    "partOf": "Organization",
    "context": "Encounter",
    "encounter": "Encounter",
    "author": "Practitioner",
    "device": "Device",
    "specimen": "Specimen",
    "sender": None,  # Practitioner | Organization | Device | Patient — resolve via look-ahead
    "recipient": None,  # Practitioner | Organization | Patient — resolve via look-ahead
}


def _model_for_type(rt: str) -> Any | None:
    """Return the ORM model class for a FHIR resource type, or None."""
    if rt == "Practitioner":
        from app.models.doctor_model import DoctorModel

        return DoctorModel
    return _RESOURCE_TYPE_TO_MODEL.get(rt)


@dataclass
class BundleRestoreResult:
    """Structured result of restoring a single FHIR Bundle.

    Breaking change (2026-06-30, G7/I4): ``restore_fhir_bundle`` previously
    returned a 5-tuple ``(created, updated, errors, warnings, id_remap)``.
    It now returns a ``BundleRestoreResult`` dataclass with attribute access.
    The new ``deleted`` and ``skipped`` counters track DELETE soft-deletes and
    conditional-create skips (``ifNoneExist`` matched) respectively.
    """

    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    id_remap: dict[str, str] = field(default_factory=dict)

    @property
    def total_created(self) -> int:
        return sum(self.created.values())

    @property
    def total_updated(self) -> int:
        return sum(self.updated.values())

    @property
    def total_deleted(self) -> int:
        return sum(self.deleted.values())
