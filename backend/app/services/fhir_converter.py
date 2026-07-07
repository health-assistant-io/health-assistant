from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.models.enums import ExportScope
from app.schemas.backup import PROVENANCE_SYSTEM
from app.services.fhir_helpers import (
    _clean,
    _extract_patient_id,
    _normalize_interpretation,
    parse_fhir_resource,
)

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B import get_fhir_model_class
from pydantic import ValidationError as FhirValidationError


CLINICAL_RESOURCE_TYPES = (
    "Patient",
    "Observation",
    "MedicationStatement",
    "AllergyIntolerance",
    "DiagnosticReport",
    "Organization",
    "Practitioner",
)


def scope_to_smart(scope: ExportScope) -> str:
    if scope == ExportScope.PATIENT:
        return "patient/*.rs"
    if scope == ExportScope.GROUP:
        return "system/*.rs"
    return "system/*.cruds"


def orm_to_fhir(resource_type: str, orm_obj: Any) -> Dict[str, Any]:
    """Serialize an ORM object to a FHIR resource dict.

    Construction is owned by each model's ``to_fhir_dict()`` (which validates via
    ``fhir.resources`` — see ``app/services/fhir_helpers.build_fhir_resource``).
    This dispatcher is a thin entry point for callers that have an ORM object.
    Raises ``FhirSerializationError`` if the resource is not FHIR-valid.
    """
    if hasattr(orm_obj, "to_fhir_dict"):
        return orm_obj.to_fhir_dict()
    raise TypeError(
        f"orm_to_fhir expects an ORM object with to_fhir_dict(); "
        f"got {type(orm_obj).__name__} for {resource_type}."
    )


def build_bundle(
    entries: List[Tuple[str, Dict[str, Any], str]],
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    bundle_entries: List[Dict[str, Any]] = []
    for full_url, resource, method in entries:
        rt = resource.get("resourceType")
        url = rt or "Resource"
        bundle_entries.append(
            {
                "fullUrl": full_url,
                "resource": resource,
                "request": {"method": method, "url": url},
            }
        )
    bundle: Dict[str, Any] = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": bundle_entries,
    }
    if meta:
        bundle["meta"] = meta
    bundle["identifier"] = {
        "system": PROVENANCE_SYSTEM,
        "value": f"backup-{uuid4()}",
    }
    return bundle


def validate_resource(resource_dict: Dict[str, Any]) -> Tuple[bool, List[str]]:
    rt = resource_dict.get("resourceType")
    if not rt:
        return False, ["Missing resourceType"]
    try:
        cls = get_fhir_model_class(rt)
        cls.model_validate(resource_dict)
        return True, []
    except FhirValidationError as e:
        return False, [str(e)]
    except Exception as e:
        return False, [f"Unknown resource type {rt}: {e}"]


def validate_bundle(bundle_dict: Dict[str, Any]) -> Tuple[bool, List[str]]:
    try:
        Bundle.model_validate(bundle_dict)
        return True, []
    except FhirValidationError as e:
        return False, [str(e)]


# ---------------------------------------------------------------------------
# FHIR -> ORM reverse conversion (canonical FHIR input only — used by import).
# Phase B will rewrite these to parse via fhir.resources typed models.
# ---------------------------------------------------------------------------


def fhir_to_patient_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    mrn = None
    for ident in f.get("identifier") or []:
        if ident.get("system", "").endswith("mrn"):
            mrn = ident.get("value")
            break
    return _clean(
        {
            "id": f.get("id"),
            "name": f.get("name"),
            "gender": (f.get("gender") or "unknown").upper(),
            "birth_date": f.get("birthDate"),
            "deceased_boolean": f.get("deceasedBoolean"),
            "deceased_datetime": f.get("deceasedDateTime"),
            "address": f.get("address"),
            "telecom": f.get("telecom"),
            "mrn": mrn,
        }
    )


def fhir_to_observation_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    return _clean(
        {
            "id": f.get("id"),
            "status": f.get("status") or "final",
            "category": f.get("category"),
            "code": f.get("code"),
            "subject": f.get("subject"),
            "effective_datetime": f.get("effectiveDateTime"),
            "value_quantity": f.get("valueQuantity"),
            "value_string": f.get("valueString"),
            "value_codeable_concept": f.get("valueCodeableConcept"),
            "reference_range": f.get("referenceRange"),
            "interpretation": _normalize_interpretation(f.get("interpretation")),
            "component": f.get("component"),
            "comment": f.get("comment"),
            "performer": f.get("performer"),
            "method": (f.get("method") or {}).get("text")
            if isinstance(f.get("method"), dict)
            else f.get("method"),
            "patient_id": _extract_patient_id(f.get("subject")),
        }
    )


def fhir_to_medication_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a canonical FHIR MedicationStatement to an ORM dict (intent=statement).

    Reuses the legacy ``fhir_to_medication_orm`` mapping but tags the row
    with ``intent='statement'`` so the facade can route it correctly.
    """
    base = _fhir_to_medication_orm_legacy(f)
    base["intent"] = "statement"
    return base


def _fhir_to_medication_orm_legacy(f: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy MedicationStatement → ORM mapping (used by import + facade)."""
    med_cc = f.get("medicationCodeableConcept") or {}
    dosage_list = f.get("dosage") or []
    dosage_text = (
        dosage_list[0].get("text")
        if dosage_list and isinstance(dosage_list[0], dict)
        else None
    )
    timing = (
        dosage_list[0].get("timing")
        if dosage_list and isinstance(dosage_list[0], dict)
        else None
    )
    period = f.get("effectivePeriod") or {}
    reason_code = f.get("reasonCode") or []
    note_list = f.get("note") or []
    return _clean(
        {
            "id": f.get("id"),
            "status": (f.get("status") or "active").upper(),
            "code": med_cc,
            "patient_id": _extract_patient_id(f.get("subject")),
            "subject": f.get("subject"),
            "start_date": period.get("start"),
            "end_date": period.get("end"),
            "dosage": dosage_text,
            "frequency": timing,
            "reason": reason_code[0].get("text") if reason_code else None,
            "note": note_list[0].get("text") if note_list else None,
        }
    )


def fhir_to_medication_request_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a canonical FHIR MedicationRequest to an ORM dict (intent=order/plan/...).

    The reverse of :meth:`Medication._to_medication_request`. Maps:
    - intent → intent discriminator (order/plan/proposal)
    - status → MedicationStatus (mapped back from MR vocabulary)
    - subject → patient_id
    - encounter → examination_id
    - medicationCodeableConcept → code
    - dosageInstruction[0] → dosage + frequency
    - authoredOn → ignored (no column; created_at handles it)
    - reasonCode[0].text → reason
    - note[0].text → note
    """
    from app.models.enums import MedicationIntent

    intent_value = (f.get("intent") or "order").lower()
    try:
        intent_enum = MedicationIntent(intent_value)
    except ValueError:
        intent_enum = MedicationIntent.ORDER

    # Map MedicationRequest status → MedicationStatus enum.
    mr_status = (f.get("status") or "active").lower()
    status_map = {
        "active": "ACTIVE",
        "completed": "COMPLETED",
        "cancelled": "CANCELLED",
        "entered-in-error": "ENTERED_IN_ERROR",
        "stopped": "STOPPED",
        "on-hold": "ON_HOLD",
        "draft": "INTENDED",
        "unknown": "UNKNOWN",
    }
    app_status = status_map.get(mr_status, "ACTIVE")

    med_cc = f.get("medicationCodeableConcept") or {}
    dosage_list = f.get("dosageInstruction") or []
    dosage_text = (
        dosage_list[0].get("text")
        if dosage_list and isinstance(dosage_list[0], dict)
        else None
    )
    timing = (
        dosage_list[0].get("timing")
        if dosage_list and isinstance(dosage_list[0], dict)
        else None
    )
    reason_code = f.get("reasonCode") or []
    note_list = f.get("note") or []

    return _clean(
        {
            "id": f.get("id"),
            "status": app_status,
            "intent": intent_enum,
            "code": med_cc,
            "patient_id": _extract_patient_id(f.get("subject")),
            "subject": f.get("subject"),
            "examination_id": _extract_patient_id(f.get("encounter")),
            "dosage": dosage_text,
            "frequency": timing,
            "reason": reason_code[0].get("text") if reason_code else None,
            "note": note_list[0].get("text") if note_list else None,
        }
    )


def fhir_to_allergy_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    clinical = (
        f.get("clinicalStatus", {}).get("coding", [{}])[0].get("code", "active")
        if f.get("clinicalStatus")
        else "active"
    )
    verification = (
        f.get("verificationStatus", {}).get("coding", [{}])[0].get("code", "confirmed")
        if f.get("verificationStatus")
        else "confirmed"
    )
    categories = f.get("category") or []
    category = categories[0].upper() if categories else None
    criticality = (f.get("criticality") or "").upper() or None

    reactions: List[Dict[str, Any]] = []
    for r in f.get("reaction") or []:
        manifestations = r.get("manifestation") or []
        reactions.append(
            {
                "manifestation": manifestations[0].get("text")
                if manifestations
                else None,
                "severity": (r.get("severity") or "").upper() or None,
                "date": r.get("onset"),
            }
        )

    return _clean(
        {
            "id": f.get("id"),
            "clinical_status": str(clinical).upper(),
            "verification_status": str(verification),
            "category": category,
            "criticality": criticality,
            "code": f.get("code"),
            "patient_id": _extract_patient_id(f.get("patient")),
            "onset_date": f.get("onsetDateTime"),
            "last_occurrence": f.get("lastOccurrence"),
            "note": (f.get("note") or [{}])[0].get("text") if f.get("note") else None,
            "reactions": reactions or None,
        }
    )


def fhir_to_diagnostic_report_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    return _clean(
        {
            "id": f.get("id"),
            "status": f.get("status") or "final",
            "category": f.get("category"),
            "code": f.get("code"),
            "subject": f.get("subject"),
            "effective_datetime": f.get("effectiveDateTime"),
            "issued": f.get("issued"),
            "performer": f.get("performer"),
            "conclusion": f.get("conclusion"),
            "conclusion_code": f.get("conclusionCode"),
            "presented_form": f.get("presentedForm"),
            "patient_id": _extract_patient_id(f.get("subject")),
        }
    )


def fhir_to_organization_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    return _clean(
        {
            "id": f.get("id"),
            "active": f.get("active"),
            "type": f.get("type"),
            "name": f.get("name"),
            "alias": f.get("alias"),
            "telecom": f.get("telecom"),
            "address": f.get("address"),
            "part_of_id": _extract_patient_id(f.get("partOf")),
            "contact": f.get("contact"),
        }
    )


def fhir_to_practitioner_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    name_list = f.get("name") or []
    name_obj = name_list[0] if name_list else {}
    full_name = (
        name_obj.get("text")
        or " ".join(
            (name_obj.get("given") or []) + [name_obj.get("family") or ""]
        ).strip()
    )

    qualifications = f.get("qualification") or []
    specialty = (
        qualifications[0].get("code", {}).get("text") if qualifications else None
    )
    license_number = None
    if qualifications and qualifications[0].get("identifier"):
        license_number = qualifications[0]["identifier"][0].get("value")

    telecom = f.get("telecom") or []
    email = next((t.get("value") for t in telecom if t.get("system") == "email"), None)
    phone = next((t.get("value") for t in telecom if t.get("system") == "phone"), None)

    return _clean(
        {
            "id": f.get("id"),
            "name": full_name or None,
            "specialty": specialty,
            "license_number": license_number,
            "email": email,
            "phone": phone,
            "telecom": telecom or None,
            "address": f.get("address"),
        }
    )


def fhir_to_condition_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a canonical FHIR Condition dict to a ClinicalEvent ORM dict.

    The reverse of :meth:`ClinicalEvent.to_fhir_dict`. Maps:
    - clinicalStatus → status enum (resolved → RESOLVED, otherwise ACTIVE)
    - code → code/coding_system + title (from code.text)
    - subject → patient_id
    - onsetDateTime → onset_date
    - abatementDateTime → resolved_date
    - note[0].text → description

    Fields without an analog (e.g. stage, evidence) are dropped — the
    Condition projection is intentionally lossy. ClinicalEvent's metadata-driven
    JSONB can hold these in ``event_metadata`` if needed by future callers.
    """
    from app.models.enums import ClinicalEventStatus, CodingSystem

    # Clinical status → ClinicalEventStatus enum.
    clinical_codings = (f.get("clinicalStatus") or {}).get("coding") or []
    clinical_code = next(
        (c.get("code") for c in clinical_codings if c.get("code")),
        "active",
    )
    if clinical_code == "resolved" or f.get("abatementDateTime"):
        status = ClinicalEventStatus.RESOLVED
    elif clinical_code in ("inactive", "remission"):
        status = ClinicalEventStatus.ON_HOLD
    else:
        status = ClinicalEventStatus.ACTIVE

    # Code → title/code/coding_system.
    code_obj = f.get("code") or {}
    title = code_obj.get("text") or "Untitled Condition"
    code = None
    coding_system = None
    coding_list = code_obj.get("coding") or []
    if coding_list:
        first = coding_list[0]
        code = first.get("code")
        system_url = first.get("system") or ""
        if "loinc.org" in system_url:
            coding_system = CodingSystem.LOINC
        elif "snomed.info" in system_url:
            coding_system = CodingSystem.SNOMED
        else:
            coding_system = CodingSystem.CUSTOM

    note_list = f.get("note") or []
    description = (
        note_list[0].get("text")
        if note_list and isinstance(note_list[0], dict)
        else None
    )

    onset_raw = f.get("onsetDateTime")
    onset_dt = _parse_iso(onset_raw)
    abatement_raw = f.get("abatementDateTime")
    abatement_dt = _parse_iso(abatement_raw)

    return _clean(
        {
            "id": f.get("id"),
            "patient_id": _extract_patient_id(f.get("subject")),
            "status": status,
            "title": title,
            "description": description,
            "onset_date": onset_dt,
            "resolved_date": abatement_dt,
            "code": code,
            "coding_system": coding_system,
        }
    )


def fhir_to_encounter_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a canonical FHIR Encounter dict to an ExaminationModel ORM dict.

    The reverse of :meth:`ExaminationModel.to_fhir_dict`. ExaminationModel is
    the app-facing vocabulary for what FHIR calls an Encounter. Maps:
    - status → ignored (ExaminationModel has no status column for visit state)
    - subject → patient_id
    - period.start → examination_date (date only)
    - class.code → ignored (no column; category_entity drives app categorization)
    - serviceProvider → organization_id
    - reasonCode[0].text → notes
    - diagnosis[].condition.display → diagnoses JSONB list

    Fields without an analog (hospitalization, priority, admission) are dropped.
    """
    period = f.get("period") or {}
    start_raw = period.get("start")
    exam_date = None
    if start_raw:
        parsed = _parse_iso(start_raw)
        if parsed is not None:
            exam_date = parsed.date()

    diagnoses: List[Dict[str, Any]] = []
    for d in f.get("diagnosis") or []:
        cond = d.get("condition") or {}
        text = cond.get("display") or cond.get("reference")
        if text:
            diagnoses.append({"text": text})

    reason_list = f.get("reasonCode") or []
    notes = (
        reason_list[0].get("text")
        if reason_list and isinstance(reason_list[0], dict)
        else None
    )

    return _clean(
        {
            "id": f.get("id"),
            "patient_id": _extract_patient_id(f.get("subject")),
            "examination_date": exam_date,
            "organization_id": _extract_patient_id(f.get("serviceProvider")),
            "notes": notes,
            "diagnoses": diagnoses or None,
        }
    )


def fhir_to_document_reference_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a canonical FHIR DocumentReference to a DocumentModel ORM dict.

    The reverse of :meth:`DocumentModel.to_fhir_dict`. DocumentModel stores
    files in app storage; the FHIR DocumentReference is metadata-only. Maps:
    - status → status (default 'current'; map to app status vocabulary)
    - docStatus → ignored (no column for draft/final)
    - subject → patient_id
    - author[0] → practitioner_id (F11: was owner_id, but owner_id is a User
      FK and the FHIR `author` reference must point to a Practitioner — so we
      preserve it as practitioner_id; owner_id must be set separately by the
      application layer since it's not a FHIR concept)
    - content[0].attachment.title → filename (REQUIRED)
    - content[0].attachment.url → file_path (relative path or urn)
    - context.encounter[0] → examination_id
    """
    content = f.get("content") or []
    attachment = (content[0].get("attachment") if content else {}) or {}
    filename = attachment.get("title") or "untitled"
    file_path = attachment.get("url") or ""

    # Map FHIR status → app status.
    fhir_status = (f.get("status") or "current").lower()
    app_status = {
        "current": "uploaded",
        "superseded": "archived",
        "entered-in-error": "failed",
    }.get(fhir_status, "uploaded")

    author_list = f.get("author") or []
    # F11: preserve the resolved Practitioner reference; the application layer
    # is responsible for setting owner_id (User FK) since that's not a FHIR
    # concept.
    practitioner_id = _extract_patient_id(author_list[0]) if author_list else None

    context = f.get("context") or {}
    encounter_list = context.get("encounter") or []
    examination_id = _extract_patient_id(encounter_list[0]) if encounter_list else None

    return _clean(
        {
            "id": f.get("id"),
            "filename": filename,
            "file_path": file_path,
            "practitioner_id": practitioner_id,
            "patient_id": _extract_patient_id(f.get("subject")),
            "examination_id": examination_id,
            "status": app_status,
        }
    )


def fhir_to_provenance_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a canonical FHIR Provenance dict to a ProvenanceModel ORM dict.

    Provenance is immutable; this converter is mainly used for round-trip
    imports (Provenance resources traveling in a Bundle). The recorded
    timestamp is preserved; agent/target/activity/entity pass through.
    """
    recorded_raw = f.get("recorded")
    recorded_dt = _parse_iso(recorded_raw)

    return _clean(
        {
            "id": f.get("id"),
            "target": f.get("target"),
            "recorded": recorded_dt,
            "activity": f.get("activity"),
            "agent": f.get("agent"),
            "entity": f.get("entity"),
        }
    )


def fhir_to_device_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a canonical FHIR Device to a DeviceModel ORM dict."""
    # Device.serialNumber is a single string in R4B (not a list).
    serial = f.get("serialNumber")
    if isinstance(serial, list):
        serial = serial[0] if serial else None
    device_name_list = f.get("deviceName") or []
    return _clean(
        {
            "id": f.get("id"),
            "identifier": f.get("identifier"),
            "device_name": device_name_list or None,
            "type": f.get("type"),
            "manufacturer": f.get("manufacturer"),
            "model_number": f.get("modelNumber"),
            "serial_number": serial,
            "status": f.get("status") or "active",
            "patient_id": _extract_patient_id(f.get("patient")),
            "owner_integration_id": _extract_patient_id(f.get("owner")),
        }
    )


def fhir_to_communication_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a canonical FHIR Communication to a CommunicationModel ORM dict."""
    sent_raw = f.get("sent")
    received_raw = f.get("received")
    return _clean(
        {
            "id": f.get("id"),
            "status": f.get("status") or "completed",
            "category": f.get("category"),
            "priority": f.get("priority"),
            "topic": f.get("topic"),
            "payload": f.get("payload"),
            "sent": _parse_iso(sent_raw),
            "received": _parse_iso(received_raw),
            "sender": f.get("sender"),
            "recipient": f.get("recipient"),
            "subject_patient_id": _extract_patient_id(f.get("subject")),
            "encounter_id": _extract_patient_id(f.get("encounter")),
        }
    )


def _parse_iso(value: Optional[str]) -> Optional[Any]:
    """Parse an ISO-8601 string to a timezone-aware datetime; return None on failure."""
    import datetime as _dt
    from datetime import timezone

    if not value:
        return None
    s = value.rstrip("Z")
    formats = (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%Y-%m",
        "%Y",
    )
    for fmt in formats:
        try:
            parsed = _dt.datetime.strptime(s, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    return None


_TO_ORM = {
    "Patient": fhir_to_patient_orm,
    "Observation": fhir_to_observation_orm,
    "MedicationStatement": fhir_to_medication_orm,
    "MedicationRequest": fhir_to_medication_request_orm,
    "Medication": lambda f: _clean({"id": f.get("id"), "code": f.get("code")}),
    "AllergyIntolerance": fhir_to_allergy_orm,
    "DiagnosticReport": fhir_to_diagnostic_report_orm,
    "Organization": fhir_to_organization_orm,
    "Practitioner": fhir_to_practitioner_orm,
    "Condition": fhir_to_condition_orm,
    "Encounter": fhir_to_encounter_orm,
    "DocumentReference": fhir_to_document_reference_orm,
    "Provenance": fhir_to_provenance_orm,
    "Device": fhir_to_device_orm,
    "Communication": fhir_to_communication_orm,
}


def fhir_to_orm(resource_type: str, fhir_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a canonical FHIR resource dict into an ORM-shape dict.

    Input MUST be canonical FHIR (camelCase). It is validated via
    ``fhir.resources`` first (raises ``FhirSerializationError`` on invalid
    input); the mapping functions then read camelCase keys only. Used by the
    import path; the REST CRUD path does not use this (it receives ORM-shape).
    """
    fn = _TO_ORM.get(resource_type)
    if not fn:
        raise ValueError(f"Unsupported FHIR resource type: {resource_type}")
    parse_fhir_resource(resource_type, fhir_dict)  # validate canonical FHIR
    return fn(fhir_dict)
