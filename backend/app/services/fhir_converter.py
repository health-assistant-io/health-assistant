from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4
import datetime as dt

from app.models.enums import ExportScope
from app.schemas.backup import (
    PROVENANCE_SYSTEM,
    PROVENANCE_CODE,
)

try:
    from fhir.resources.R4B.bundle import Bundle
    from fhir.resources.R4B import get_fhir_model_class
    from pydantic import ValidationError as FhirValidationError

    _FHIR_AVAILABLE = True
except Exception:
    _FHIR_AVAILABLE = False
    FhirValidationError = Exception


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


def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _as_list(v: Any) -> Optional[List[Any]]:
    if v is None:
        return None
    if isinstance(v, list):
        return v
    return [v]


def _normalize_timing(timing: Any) -> Any:
    if not isinstance(timing, dict):
        return timing
    repeat = timing.get("repeat")
    if not isinstance(repeat, dict):
        return timing
    tod = repeat.get("timeOfDay")
    if isinstance(tod, list):
        normalized = []
        for t in tod:
            if isinstance(t, str) and len(t) == 5 and t.count(":") == 1:
                normalized.append(f"{t}:00")
            else:
                normalized.append(t)
        repeat = {**repeat, "timeOfDay": normalized}
        timing = {**timing, "repeat": repeat}
    return timing


def build_meta(
    version_id: Optional[str] = None,
    last_updated: Optional[str] = None,
    provenance: bool = True,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    if version_id:
        meta["versionId"] = str(version_id)
    if last_updated:
        meta["lastUpdated"] = last_updated
    meta["versionId"] = meta.get("versionId") or "1"
    meta["lastUpdated"] = meta.get("lastUpdated") or dt.datetime.now(
        dt.timezone.utc
    ).isoformat()
    meta["source"] = PROVENANCE_SYSTEM
    if provenance:
        meta["tag"] = [
            {
                "system": PROVENANCE_SYSTEM,
                "code": PROVENANCE_CODE,
                "display": "Health Assistant export",
            }
        ]
    return meta


def patient_to_fhir(p: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    gender = (p.get("gender") or "").lower() or None
    identifiers: List[Dict[str, Any]] = []
    if p.get("mrn"):
        identifiers.append(
            {"system": "urn:healthassistant:mrn", "value": str(p["mrn"])}
        )
    return _clean(
        {
            "resourceType": "Patient",
            "id": p.get("id"),
            "identifier": identifiers or None,
            "name": p.get("name"),
            "gender": gender,
            "birthDate": p.get("birth_date") or p.get("birthDate"),
            "deceasedBoolean": p.get("deceased_boolean"),
            "deceasedDateTime": p.get("deceased_datetime"),
            "address": p.get("address"),
            "telecom": p.get("telecom"),
            "meta": meta or build_meta(p.get("id")),
        }
    )


def observation_to_fhir(o: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    vq = o.get("value_quantity") or o.get("valueQuantity")
    interpretation = o.get("interpretation")
    if isinstance(interpretation, str):
        interpretation = [{"text": interpretation}]
    return _clean(
        {
            "resourceType": "Observation",
            "id": o.get("id"),
            "status": (o.get("status") or "final").lower(),
            "category": o.get("category"),
            "code": o.get("code"),
            "subject": o.get("subject"),
            "effectiveDateTime": o.get("effective_datetime")
            or o.get("effectiveDateTime"),
            "valueQuantity": vq,
            "valueString": o.get("value_string") or o.get("valueString"),
            "valueCodeableConcept": o.get("value_codeable_concept")
            or o.get("valueCodeableConcept"),
            "referenceRange": o.get("reference_range") or o.get("referenceRange"),
            "interpretation": interpretation,
            "note": [{"text": o.get("comment")}] if o.get("comment") else None,
            "performer": o.get("performer"),
            "method": {"text": o.get("method")} if o.get("method") else None,
            "meta": meta or build_meta(o.get("id")),
        }
    )


def medication_to_fhir(m: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    status = (m.get("status") or "active").lower()
    code = m.get("code") or {}
    med_cc = {"text": code.get("text")} if isinstance(code, dict) else None
    if isinstance(code, dict) and code.get("coding"):
        med_cc = {"text": code.get("text"), "coding": code.get("coding")}

    dosage: List[Dict[str, Any]] = []
    dose_entry: Dict[str, Any] = {}
    if m.get("dosage"):
        dose_entry["text"] = m.get("dosage")
    if m.get("frequency"):
        dose_entry["timing"] = _normalize_timing(m.get("frequency"))
    if dose_entry:
        dosage.append(dose_entry)

    effective: Dict[str, Any] = {}
    if m.get("start_date") or m.get("end_date"):
        effective = {
            "start": m.get("start_date"),
            "end": m.get("end_date"),
        }

    return _clean(
        {
            "resourceType": "MedicationStatement",
            "id": m.get("id"),
            "status": status,
            "medicationCodeableConcept": med_cc,
            "subject": {"reference": f"Patient/{m.get('patient_id')}"}
            if m.get("patient_id")
            else m.get("subject"),
            "effectivePeriod": effective or None,
            "dosage": dosage or None,
            "reasonCode": [{"text": m.get("reason")}] if m.get("reason") else None,
            "note": [{"text": m.get("note")}] if m.get("note") else None,
            "meta": meta or build_meta(m.get("id")),
        }
    )


def allergy_to_fhir(a: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    clinical = (a.get("clinical_status") or "").lower()
    verification = (a.get("verification_status") or "confirmed").lower()
    category = (a.get("category") or "").lower() or None
    criticality = (a.get("criticality") or "").lower() or None

    clinical_status = (
        {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                    "code": clinical,
                }
            ]
        }
        if clinical
        else None
    )
    verification_status = (
        {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                    "code": verification,
                }
            ]
        }
        if verification
        else None
    )

    code = a.get("code") or {}
    allergy_code = {"text": code.get("text")} if isinstance(code, dict) else None
    if isinstance(code, dict) and code.get("coding"):
        allergy_code = {"text": code.get("text"), "coding": code.get("coding")}

    reactions: List[Dict[str, Any]] = []
    for r in a.get("reactions") or []:
        reaction: Dict[str, Any] = {}
        if r.get("manifestation"):
            reaction["manifestation"] = [{"text": r["manifestation"]}]
        if r.get("severity"):
            reaction["severity"] = str(r["severity"]).lower()
        if r.get("date"):
            reaction["onset"] = r["date"]
        if reaction:
            reactions.append(reaction)

    return _clean(
        {
            "resourceType": "AllergyIntolerance",
            "id": a.get("id"),
            "clinicalStatus": clinical_status,
            "verificationStatus": verification_status,
            "category": [category] if category else None,
            "criticality": criticality,
            "code": allergy_code,
            "patient": {"reference": f"Patient/{a.get('patient_id')}"}
            if a.get("patient_id")
            else None,
            "onsetDateTime": a.get("onset_date"),
            "lastOccurrence": a.get("last_occurrence"),
            "note": [{"text": a.get("note")}] if a.get("note") else None,
            "reaction": reactions or None,
            "meta": meta or build_meta(a.get("id")),
        }
    )


def diagnostic_report_to_fhir(
    d: Dict[str, Any], meta: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return _clean(
        {
            "resourceType": "DiagnosticReport",
            "id": d.get("id"),
            "status": (d.get("status") or "final").lower(),
            "category": _as_list(d.get("category")),
            "code": d.get("code"),
            "subject": d.get("subject"),
            "effectiveDateTime": d.get("effective_datetime")
            or d.get("effectiveDateTime"),
            "issued": d.get("issued"),
            "performer": d.get("performer"),
            "conclusion": d.get("conclusion"),
            "conclusionCode": _as_list(d.get("conclusion_code") or d.get("conclusionCode")),
            "presentedForm": _as_list(d.get("presented_form") or d.get("presentedForm")),
            "meta": meta or build_meta(d.get("id")),
        }
    )


def organization_to_fhir(
    o: Dict[str, Any], meta: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return _clean(
        {
            "resourceType": "Organization",
            "id": o.get("id"),
            "active": o.get("active"),
            "type": _as_list(o.get("type")),
            "name": o.get("name"),
            "alias": o.get("alias"),
            "telecom": o.get("telecom"),
            "address": o.get("address"),
            "partOf": {"reference": f"Organization/{o.get('part_of_id')}"}
            if o.get("part_of_id")
            else None,
            "contact": o.get("contact"),
            "meta": meta or build_meta(o.get("id")),
        }
    )


def practitioner_to_fhir(
    d: Dict[str, Any], meta: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    name = d.get("name") or ""
    name_parts = name.split(" ", 1)
    given = [name_parts[0]] if name_parts[0] else []
    family = name_parts[1] if len(name_parts) > 1 else (name_parts[0] or None)

    telecom: List[Dict[str, Any]] = []
    if d.get("email"):
        telecom.append({"system": "email", "value": d.get("email")})
    if d.get("phone"):
        telecom.append({"system": "phone", "value": d.get("phone")})
    if d.get("telecom"):
        telecom = d.get("telecom")

    qualifications: List[Dict[str, Any]] = []
    if d.get("specialty") or d.get("license_number"):
        q: Dict[str, Any] = {}
        if d.get("specialty"):
            q["code"] = {"text": d.get("specialty")}
        if d.get("license_number"):
            q["identifier"] = [{"value": d.get("license_number")}]
        qualifications.append(q)

    return _clean(
        {
            "resourceType": "Practitioner",
            "id": d.get("id"),
            "name": [{"family": family, "given": given, "text": name}] if name else None,
            "qualification": qualifications or None,
            "telecom": telecom or None,
            "address": d.get("address"),
            "meta": meta or build_meta(d.get("id")),
        }
    )


_TO_FHIR = {
    "Patient": patient_to_fhir,
    "Observation": observation_to_fhir,
    "MedicationStatement": medication_to_fhir,
    "AllergyIntolerance": allergy_to_fhir,
    "DiagnosticReport": diagnostic_report_to_fhir,
    "Organization": organization_to_fhir,
    "Practitioner": practitioner_to_fhir,
}


def orm_to_fhir(resource_type: str, orm_dict: Dict[str, Any]) -> Dict[str, Any]:
    fn = _TO_FHIR.get(resource_type)
    if not fn:
        raise ValueError(f"Unsupported FHIR resource type: {resource_type}")
    return fn(orm_dict)


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
    if not _FHIR_AVAILABLE:
        return True, ["fhir.resources not available; validation skipped"]
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
    if not _FHIR_AVAILABLE:
        return True, ["fhir.resources not available; validation skipped"]
    try:
        Bundle.model_validate(bundle_dict)
        return True, []
    except FhirValidationError as e:
        return False, [str(e)]


def _extract_patient_id(ref: Optional[Dict[str, Any]]) -> Optional[str]:
    if not ref:
        return None
    reference = ref.get("reference") if isinstance(ref, dict) else None
    if reference and "/" in reference:
        return reference.split("/")[-1]
    return None


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
            "birth_date": f.get("birthDate") or f.get("birth_date"),
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
            "effective_datetime": f.get("effectiveDateTime")
            or f.get("effective_datetime"),
            "value_quantity": f.get("valueQuantity") or f.get("value_quantity"),
            "value_string": f.get("valueString") or f.get("value_string"),
            "value_codeable_concept": f.get("valueCodeableConcept"),
            "reference_range": f.get("referenceRange") or f.get("reference_range"),
            "interpretation": f.get("interpretation"),
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
    dosage_text = dosage_list[0].get("text") if dosage_list else None
    timing = dosage_list[0].get("timing") if dosage_list else None
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
    fn = _TO_ORM.get(resource_type)
    if not fn:
        raise ValueError(f"Unsupported FHIR resource type: {resource_type}")
    return fn(fhir_dict)
