from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.models.enums import ExportScope
from app.schemas.backup import PROVENANCE_SYSTEM
from app.services.fhir_helpers import (
    _clean,
    _extract_patient_id,
    _flatten_interpretation,
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
            "interpretation": _flatten_interpretation(f.get("interpretation")),
            "comment": f.get("comment"),
            "performer": f.get("performer"),
            "method": (f.get("method") or {}).get("text")
            if isinstance(f.get("method"), dict)
            else f.get("method"),
            "patient_id": _extract_patient_id(f.get("subject")),
        }
    )


def fhir_to_medication_orm(f: Dict[str, Any]) -> Dict[str, Any]:
    med_cc = f.get("medicationCodeableConcept") or {}
    dosage_list = f.get("dosage") or []
    dosage_text = (
        dosage_list[0].get("text") if dosage_list and isinstance(dosage_list[0], dict) else None
    )
    timing = (
        dosage_list[0].get("timing") if dosage_list and isinstance(dosage_list[0], dict) else None
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
            "note": (f.get("note") or [{}])[0].get("text")
            if f.get("note")
            else None,
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
    full_name = name_obj.get("text") or " ".join(
        (name_obj.get("given") or []) + [name_obj.get("family") or ""]
    ).strip()

    qualifications = f.get("qualification") or []
    specialty = (
        qualifications[0].get("code", {}).get("text") if qualifications else None
    )
    license_number = None
    if qualifications and qualifications[0].get("identifier"):
        license_number = qualifications[0]["identifier"][0].get("value")

    telecom = f.get("telecom") or []
    email = next(
        (t.get("value") for t in telecom if t.get("system") == "email"), None
    )
    phone = next(
        (t.get("value") for t in telecom if t.get("system") == "phone"), None
    )

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


_TO_ORM = {
    "Patient": fhir_to_patient_orm,
    "Observation": fhir_to_observation_orm,
    "MedicationStatement": fhir_to_medication_orm,
    "AllergyIntolerance": fhir_to_allergy_orm,
    "DiagnosticReport": fhir_to_diagnostic_report_orm,
    "Organization": fhir_to_organization_orm,
    "Practitioner": fhir_to_practitioner_orm,
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
