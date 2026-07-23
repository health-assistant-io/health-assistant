"""FHIR R4 → Health Assistant schema mappers for the ``fhir_server`` provider.

Pure, defensive functions: each takes a remote FHIR resource dict and
returns the matching HA Pydantic create-schema (or ``None`` when the
resource can't be mapped — e.g. no code, no usable value). Keeping the
mapping logic out of :mod:`integrations.fhir_server.provider` makes it
trivial to unit-test each resource type in isolation and to extend the
surface without touching the provider's plumbing.

Conventions:

- The remote patient reference is **not** preserved — every payload is
  attached to the *local* ``patient_id`` the engine passes in (the
  remote patient id is only the search key, mirroring
  ``fhir_observation_to_create``).
- ``external_id`` is always set to the remote resource's ``id`` so the
  engine dedups across syncs on
  ``(tenant, patient, source_integration_id, external_id)``.
- Datetimes are parsed leniently (bad/missing → ``None``) and kept
  timezone-aware where the source carries offset info.
- Unknown enum values fall back to the schema/model default rather than
  dropping the whole record — partial data beats no data.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.models.enums import (
    AllergyCategory,
    AllergyClinicalStatus,
    AllergyCriticality,
    ClinicalEventStatus,
    CodingSystem,
    ImmunizationStatus,
    MedicationIntent,
    MedicationStatus,
    ReactionSeverity,
)
from app.schemas.allergy import AllergyIntoleranceCreate
from app.schemas.clinical_event import ClinicalEventCreate
from app.schemas.examination import ExaminationCreate
from app.schemas.medication import MedicationRecordCreate
from app.schemas.vaccine import PatientImmunizationCreate, VaccineCodeableConcept


# ---------------------------------------------------------------------------
# Shared low-level helpers
# ---------------------------------------------------------------------------


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse a FHIR datetime/instant string (lenient, tz-aware)."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_date(value: Any) -> Optional[date]:
    """Parse a FHIR date (``YYYY-MM-DD``) or datetime into a ``date``."""
    dt = _parse_dt(value)
    if dt is not None:
        return dt.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _codeable_text(cc: Any) -> str:
    """Best-effort display text from a FHIR CodeableConcept."""
    if not isinstance(cc, dict):
        return ""
    if cc.get("text"):
        return str(cc["text"])
    for coding in cc.get("coding") or []:
        if isinstance(coding, dict) and coding.get("display"):
            return str(coding["display"])
        if isinstance(coding, dict) and coding.get("code"):
            return str(coding["code"])
    return ""


def _first_coding(cc: Any) -> Optional[Dict[str, Any]]:
    """Return the first coding dict of a CodeableConcept (or ``None``)."""
    if not isinstance(cc, dict):
        return None
    for coding in cc.get("coding") or []:
        if isinstance(coding, dict):
            return coding
    return None


def _coding_system_for(system: Optional[str]) -> CodingSystem:
    """Map a FHIR coding ``system`` URL to the local :class:`CodingSystem`."""
    if not system:
        return CodingSystem.CUSTOM
    if "loinc.org" in system:
        return CodingSystem.LOINC
    if "snomed.info" in system or "snomed.ct" in system:
        return CodingSystem.SNOMED
    return CodingSystem.CUSTOM


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


# ---------------------------------------------------------------------------
# Condition → ClinicalEventCreate (Phase 1)
# ---------------------------------------------------------------------------

# FHIR condition-clinical → ClinicalEventStatus (we only carry
# ACTIVE/RESOLVED/ON_HOLD/UNKNOWN, so the long-tail collapses sensibly).
_CONDITION_STATUS_MAP = {
    "active": ClinicalEventStatus.ACTIVE,
    "recurrence": ClinicalEventStatus.ACTIVE,
    "relapse": ClinicalEventStatus.ACTIVE,
    "resolved": ClinicalEventStatus.RESOLVED,
    "remission": ClinicalEventStatus.RESOLVED,
    "inactive": ClinicalEventStatus.RESOLVED,
}


def condition_to_event(
    fhir_condition: Dict[str, Any],
    *,
    patient_id: UUID,
) -> Optional[ClinicalEventCreate]:
    """Map a FHIR R4 ``Condition`` to a :class:`ClinicalEventCreate`.

    Returns ``None`` when the condition has no usable code (there's
    nothing to title the event with). The engine stamps
    ``source_integration_id``; we only set ``external_id``.
    """
    if not isinstance(fhir_condition, dict):
        return None
    code = fhir_condition.get("code")
    title = _codeable_text(code) if code else ""
    if not title:
        return None

    coding = _first_coding(code) or {}
    coding_system = _coding_system_for(coding.get("system"))
    code_value = coding.get("code")

    clinical = fhir_condition.get("clinicalStatus")
    status_text = ""
    if isinstance(clinical, dict):
        for c in clinical.get("coding") or []:
            if isinstance(c, dict) and c.get("code"):
                status_text = str(c["code"]).lower()
                break
    status = _CONDITION_STATUS_MAP.get(status_text, ClinicalEventStatus.ACTIVE)

    description = None
    note_list = fhir_condition.get("note") or []
    if note_list and isinstance(note_list[0], dict) and note_list[0].get("text"):
        description = str(note_list[0]["text"])

    kwargs: Dict[str, Any] = {
        "patient_id": patient_id,
        "title": title[:255],
        "status": status,
        "onset_date": _parse_dt(fhir_condition.get("onsetDateTime"))
        or _parse_dt((fhir_condition.get("onsetPeriod") or {}).get("start")),
        "resolved_date": _parse_dt(fhir_condition.get("abatementDateTime"))
        or _parse_dt((fhir_condition.get("abatementPeriod") or {}).get("start")),
        "coding_system": coding_system,
        "external_id": _resource_id(fhir_condition),
    }
    if code_value:
        kwargs["code"] = str(code_value)
    if description:
        kwargs["description"] = description
    return ClinicalEventCreate(**kwargs)


# ---------------------------------------------------------------------------
# Encounter → ExaminationCreate (Phase 1)
# ---------------------------------------------------------------------------


def encounter_to_exam(
    fhir_encounter: Dict[str, Any],
    *,
    patient_id: UUID,
) -> Optional[ExaminationCreate]:
    """Map a FHIR R4 ``Encounter`` to an :class:`ExaminationCreate`.

    ``period.start`` → ``examination_date`` (falls back to ``period.end``,
    then the encounter class leaves a date-less exam as ``None`` — the
    service tolerates that). ``reasonCode`` / ``type`` → ``notes``.
    """
    if not isinstance(fhir_encounter, dict):
        return None

    period = fhir_encounter.get("period") or {}
    exam_date = _parse_date(period.get("start")) or _parse_date(period.get("end"))

    # Build notes from reasonCode[] + type[] (the why of the visit).
    note_parts: List[str] = []
    for cc in (fhir_encounter.get("reasonCode") or []) + (
        fhir_encounter.get("type") or []
    ):
        txt = _codeable_text(cc)
        if txt:
            note_parts.append(txt)
    notes = "; ".join(dict.fromkeys(note_parts)) or None  # de-dup, preserve order

    # class.code (e.g. AMB/IMP/EMER) → a free-text category hint. The
    # examination service resolves category text → concept id; an unknown
    # hint lands as an uncategorized exam (non-fatal).
    encounter_class = fhir_encounter.get("class") or {}
    category_hint = None
    if isinstance(encounter_class, dict) and encounter_class.get("code"):
        category_hint = str(encounter_class["code"])

    kwargs: Dict[str, Any] = {
        "patient_id": patient_id,
        "external_id": _resource_id(fhir_encounter),
    }
    if exam_date is not None:
        kwargs["examination_date"] = exam_date
    if notes:
        kwargs["notes"] = notes[:2000]
    if category_hint:
        kwargs["category"] = category_hint
    return ExaminationCreate(**kwargs)


# ---------------------------------------------------------------------------
# DocumentReference → attachment metadata (Phase 2)
# ---------------------------------------------------------------------------


class DocumentReferenceMeta:
    """Lightweight holder for a DocumentReference's pull-relevant metadata.

    The provider fetches each attachment's bytes (via ``_fetch_attachment``)
    and builds a :class:`~integrations.sdk.documents.DocumentPull` per
    attachment; this object carries everything that's constant across a
    single DocumentReference's attachments.
    """

    __slots__ = (
        "external_id",
        "category_concept_slug",
        "examination_external_id",
        "attachments",
    )

    def __init__(
        self,
        *,
        external_id: Optional[str],
        category_concept_slug: Optional[str],
        examination_external_id: Optional[str],
        attachments: List[Dict[str, Optional[str]]],
    ) -> None:
        self.external_id = external_id
        self.category_concept_slug = category_concept_slug
        self.examination_external_id = examination_external_id
        self.attachments = attachments


def document_reference_meta(
    fhir_doc_ref: Dict[str, Any],
) -> Optional[DocumentReferenceMeta]:
    """Extract pull-relevant metadata from a FHIR ``DocumentReference``.

    Returns ``None`` when the resource has no ``content[]`` (nothing to
    fetch). Each entry in ``attachments`` is
    ``{"filename", "content_type", "url"}`` — the provider resolves the
    URL to bytes. ``category_concept_slug`` is a best-effort kebab slug
    derived from the first ``category`` coding; misses are non-fatal
    (the engine's ``resolve_concept_by_slug`` returns ``None`` and the
    doc is created uncategorized).
    """
    if not isinstance(fhir_doc_ref, dict):
        return None
    contents = fhir_doc_ref.get("content") or []
    if not contents:
        return None

    attachments: List[Dict[str, Optional[str]]] = []
    for content in contents:
        if not isinstance(content, dict):
            continue
        attachment = content.get("attachment") or {}
        if not isinstance(attachment, dict):
            continue
        url = attachment.get("url")
        if not url:
            continue
        filename = (
            attachment.get("title")
            or attachment.get("id")
            or f"document-{_resource_id(fhir_doc_ref) or 'unknown'}"
        )
        attachments.append(
            {
                "filename": str(filename),
                "content_type": attachment.get("contentType"),
                "url": str(url),
            }
        )
    if not attachments:
        return None

    # category → best-effort concept slug.
    category_slug: Optional[str] = None
    categories = fhir_doc_ref.get("category") or []
    if categories and isinstance(categories[0], dict):
        category_slug = _slugify(_codeable_text(categories[0])) or None

    # context.encounter → the linked Encounter external id (for exam linking).
    exam_ext_id: Optional[str] = None
    context = fhir_doc_ref.get("context") or {}
    encounters = context.get("encounter") or []
    if encounters and isinstance(encounters[0], dict):
        ref = encounters[0].get("reference") or ""
        # "Encounter/<id>" → "<id>"
        exam_ext_id = ref.split("/", 1)[-1] if "/" in ref else (ref or None)

    return DocumentReferenceMeta(
        external_id=_resource_id(fhir_doc_ref),
        category_concept_slug=category_slug,
        examination_external_id=exam_ext_id,
        attachments=attachments,
    )


# ---------------------------------------------------------------------------
# MedicationStatement / MedicationRequest → MedicationRecordCreate (Phase 4)
# ---------------------------------------------------------------------------

# FHIR MedicationStatement.status (lowercase) → MedicationStatus.
_MED_STATEMENT_STATUS_MAP = {
    "active": MedicationStatus.ACTIVE,
    "completed": MedicationStatus.COMPLETED,
    "entered-in-error": MedicationStatus.ENTERED_IN_ERROR,
    "intended": MedicationStatus.INTENDED,
    "on-hold": MedicationStatus.ON_HOLD,
    "stopped": MedicationStatus.STOPPED,
    "not-taken": MedicationStatus.INACTIVE,
    "unknown": MedicationStatus.UNKNOWN,
}


def _medication_code(med_codeable: Any) -> Optional[Dict[str, Any]]:
    """Build the HA ``Medication.code`` JSONB from a FHIR CodeableConcept.

    Returns ``None`` if there's no text and no coding to anchor on — the
    HA ``code`` column is NOT NULL, so a code-less medication is dropped.
    """
    text = _codeable_text(med_codeable)
    coding = (med_codeable or {}).get("coding") if isinstance(med_codeable, dict) else None
    if not text and not coding:
        return None
    return {"text": text or None, "coding": coding or []}


def _medication_base_kwargs(
    fhir_med: Dict[str, Any],
    *,
    patient_id: UUID,
    intent: MedicationIntent,
) -> Optional[Dict[str, Any]]:
    """Shared extraction for MedicationStatement + MedicationRequest."""
    code_field = "medicationCodeableConcept"
    med_codeable = fhir_med.get(code_field)
    # Some servers reference a Medication resource instead of inlining.
    if med_codeable is None:
        med_ref = fhir_med.get("medicationReference")
        if isinstance(med_ref, dict) and med_ref.get("display"):
            med_codeable = {"text": med_ref["display"]}
    code = _medication_code(med_codeable)
    if code is None:
        return None

    kwargs: Dict[str, Any] = {
        "patient_id": patient_id,
        "code": code,
        "intent": intent,
        "external_id": _resource_id(fhir_med),
    }

    status_text = str(fhir_med.get("status") or "").lower()
    status = _MED_STATEMENT_STATUS_MAP.get(status_text)
    if status is not None:
        kwargs["status"] = status

    # effective / authoredOn → start_date
    start = _parse_date(fhir_med.get("effectiveDateTime"))
    if start is None:
        eff_period = fhir_med.get("effectivePeriod") or {}
        start = _parse_date(eff_period.get("start"))
    if start is None:
        start = _parse_date(fhir_med.get("authoredOn"))
    if start is not None:
        kwargs["start_date"] = start

    # dosage: MedicationStatement.dosage[0].text or MedicationRequest.dosageInstruction[0].text
    for dosage_key in ("dosage", "dosageInstruction"):
        dosage_list = fhir_med.get(dosage_key) or []
        if dosage_list and isinstance(dosage_list[0], dict):
            d = dosage_list[0]
            dosage_text = d.get("text")
            if dosage_text:
                kwargs["dosage"] = str(dosage_text)[:255]
                break

    # reasonCode[] → reason
    reason_parts = [_codeable_text(rc) for rc in (fhir_med.get("reasonCode") or [])]
    reason = "; ".join(p for p in reason_parts if p) or None
    if reason:
        kwargs["reason"] = reason[:2000]

    # note[0].text → note
    note_list = fhir_med.get("note") or []
    if note_list and isinstance(note_list[0], dict) and note_list[0].get("text"):
        kwargs["note"] = str(note_list[0]["text"])

    return kwargs


def medication_statement_to_record(
    fhir_statement: Dict[str, Any],
    *,
    patient_id: UUID,
) -> Optional[MedicationRecordCreate]:
    """Map a FHIR ``MedicationStatement`` → ``MedicationRecordCreate``.

    ``intent`` defaults to STATEMENT (the schema/model default); callers
    may override but the FHIR resource type already implies it.
    """
    if not isinstance(fhir_statement, dict):
        return None
    kwargs = _medication_base_kwargs(
        fhir_statement, patient_id=patient_id, intent=MedicationIntent.STATEMENT
    )
    if kwargs is None:
        return None
    return MedicationRecordCreate(**kwargs)


def medication_request_to_record(
    fhir_request: Dict[str, Any],
    *,
    patient_id: UUID,
) -> Optional[MedicationRecordCreate]:
    """Map a FHIR ``MedicationRequest`` → ``MedicationRecordCreate``.

    Marked ``intent=order`` so the row projects to a MedicationRequest
    on the R4 facade (matching how it was sourced).
    """
    if not isinstance(fhir_request, dict):
        return None
    kwargs = _medication_base_kwargs(
        fhir_request, patient_id=patient_id, intent=MedicationIntent.ORDER
    )
    if kwargs is None:
        return None
    return MedicationRecordCreate(**kwargs)


# ---------------------------------------------------------------------------
# AllergyIntolerance → AllergyIntoleranceCreate (Phase 4)
# ---------------------------------------------------------------------------

_ALLERGY_CATEGORY_MAP = {
    "food": AllergyCategory.FOOD,
    "medication": AllergyCategory.MEDICATION,
    "environment": AllergyCategory.ENVIRONMENT,
    "biologic": AllergyCategory.BIOLOGIC,
    "biotics": AllergyCategory.BIOLOGIC,
}

_ALLERGY_CRITICALITY_MAP = {
    "low": AllergyCriticality.LOW,
    "high": AllergyCriticality.HIGH,
    "unable-to-assess": AllergyCriticality.UNABLE_TO_ASSESS,
}

_ALLERGY_CLINICAL_STATUS_MAP = {
    "active": AllergyClinicalStatus.ACTIVE,
    "inactive": AllergyClinicalStatus.INACTIVE,
    "resolved": AllergyClinicalStatus.RESOLVED,
}


def _allergy_clinical_status(fhir_allergy: Dict[str, Any]) -> AllergyClinicalStatus:
    cs = fhir_allergy.get("clinicalStatus")
    if isinstance(cs, dict):
        for c in cs.get("coding") or []:
            if isinstance(c, dict) and c.get("code"):
                return _ALLERGY_CLINICAL_STATUS_MAP.get(
                    str(c["code"]).lower(), AllergyClinicalStatus.ACTIVE
                )
    return AllergyClinicalStatus.ACTIVE


def _allergy_verification_status(fhir_allergy: Dict[str, Any]) -> str:
    vs = fhir_allergy.get("verificationStatus")
    if isinstance(vs, dict):
        for c in vs.get("coding") or []:
            if isinstance(c, dict) and c.get("code"):
                return str(c["code"])  # confirmed, unconfirmed, refuted, ...
    return "confirmed"


def allergy_intolerance_to_create(
    fhir_allergy: Dict[str, Any],
    *,
    patient_id: UUID,
) -> Optional[AllergyIntoleranceCreate]:
    """Map a FHIR ``AllergyIntolerance`` → ``AllergyIntoleranceCreate``."""
    if not isinstance(fhir_allergy, dict):
        return None
    code = fhir_allergy.get("code")
    code_text = _codeable_text(code)
    coding = (code or {}).get("coding") if isinstance(code, dict) else None
    if not code_text and not coding:
        return None  # ``code`` column is NOT NULL

    kwargs: Dict[str, Any] = {
        "patient_id": patient_id,
        "code": {"text": code_text or None, "coding": coding or []},
        "clinical_status": _allergy_clinical_status(fhir_allergy),
        "verification_status": _allergy_verification_status(fhir_allergy),
        "external_id": _resource_id(fhir_allergy),
    }

    category_list = fhir_allergy.get("category") or []
    if category_list and isinstance(category_list[0], str):
        kwargs["category"] = _ALLERGY_CATEGORY_MAP.get(
            category_list[0].lower()
        )

    crit = fhir_allergy.get("criticality")
    if isinstance(crit, str):
        kwargs["criticality"] = _ALLERGY_CRITICALITY_MAP.get(crit.lower())

    onset = _parse_dt(
        fhir_allergy.get("onsetDateTime")
        or (fhir_allergy.get("onsetPeriod") or {}).get("start")
    )
    if onset:
        kwargs["onset_date"] = onset
    last_occ = _parse_dt(fhir_allergy.get("lastOccurrence"))
    if last_occ:
        kwargs["last_occurrence"] = last_occ

    note_list = fhir_allergy.get("note") or []
    if note_list and isinstance(note_list[0], dict) and note_list[0].get("text"):
        kwargs["note"] = str(note_list[0]["text"])

    reactions = _map_allergy_reactions(fhir_allergy.get("reaction"))
    if reactions:
        kwargs["reactions"] = reactions

    return AllergyIntoleranceCreate(**kwargs)


def _map_allergy_reactions(raw: Any) -> List[Dict[str, Any]]:
    """FHIR AllergyIntolerance.reaction[] → HA ``reactions`` JSONB rows."""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for reaction in raw:
        if not isinstance(reaction, dict):
            continue
        manifestations = [
            _codeable_text(m)
            for m in (reaction.get("manifestation") or [])
            if _codeable_text(m)
        ]
        if not manifestations:
            continue
        severity_text = str(reaction.get("severity") or "").lower()
        severity = None
        if severity_text in ("mild", "moderate", "severe"):
            # Store the enum's string value (not the enum instance) so the
            # JSONB column serializes cleanly through the custom encoder.
            severity = ReactionSeverity(severity_text.upper()).value
        row: Dict[str, Any] = {
            "manifestation": "; ".join(manifestations),
            "severity": severity,
        }
        when = _parse_dt(reaction.get("onset")) or _parse_dt(reaction.get("date"))
        if when:
            row["date"] = when.isoformat()
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Immunization → PatientImmunizationCreate (Phase 4)
# ---------------------------------------------------------------------------


def immunization_to_create(
    fhir_immunization: Dict[str, Any],
    *,
    patient_id: UUID,
) -> Optional[PatientImmunizationCreate]:
    """Map a FHIR ``Immunization`` → ``PatientImmunizationCreate``."""
    if not isinstance(fhir_immunization, dict):
        return None
    vaccine_code_cc = fhir_immunization.get("vaccineCode")
    text = _codeable_text(vaccine_code_cc)
    coding = (
        (vaccine_code_cc or {}).get("coding") if isinstance(vaccine_code_cc, dict) else None
    )
    if not text and not coding:
        return None  # ``vaccine_code`` is required on the schema

    vaccine_code = VaccineCodeableConcept(
        text=text or "(unknown vaccine)",
        coding=coding or None,
    )

    kwargs: Dict[str, Any] = {
        "patient_id": patient_id,
        "vaccine_code": vaccine_code,
        "external_id": _resource_id(fhir_immunization),
    }

    status_text = str(fhir_immunization.get("status") or "").lower()
    if status_text in ("completed", "entered-in-error", "not-done"):
        kwargs["status"] = ImmunizationStatus(status_text)

    administered = _parse_dt(
        fhir_immunization.get("occurrenceDateTime")
        or (fhir_immunization.get("occurrenceString"))
    )
    if administered is None:
        # occurrenceString is a date-only string sometimes.
        d = _parse_date(fhir_immunization.get("occurrenceString"))
        if d is not None:
            administered = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    if administered:
        kwargs["administered_at"] = administered

    protocol = fhir_immunization.get("protocolApplied") or []
    if protocol and isinstance(protocol[0], dict):
        series = protocol[0].get("doseNumberPositiveInt") or protocol[0].get("seriesDosesPositiveInt")
        if series:
            kwargs["dose_number"] = str(series)

    if fhir_immunization.get("lotNumber"):
        kwargs["lot_number"] = str(fhir_immunization["lotNumber"])[:100]
    manufacturer = fhir_immunization.get("manufacturer")
    if isinstance(manufacturer, dict) and manufacturer.get("display"):
        kwargs["manufacturer"] = str(manufacturer["display"])[:255]
    if fhir_immunization.get("location"):
        kwargs["location"] = str(fhir_immunization["location"])[:255]

    note_list = fhir_immunization.get("note") or []
    if note_list and isinstance(note_list[0], dict) and note_list[0].get("text"):
        kwargs["note"] = str(note_list[0]["text"])

    return PatientImmunizationCreate(**kwargs)


# ---------------------------------------------------------------------------
# small shared utility
# ---------------------------------------------------------------------------


def _resource_id(resource: Dict[str, Any]) -> Optional[str]:
    """The remote resource's stable ``id`` (the dedup external_id)."""
    rid = resource.get("id")
    return str(rid) if rid else None


__all__ = [
    "DocumentReferenceMeta",
    "allergy_intolerance_to_create",
    "condition_to_event",
    "document_reference_meta",
    "encounter_to_exam",
    "immunization_to_create",
    "medication_request_to_record",
    "medication_statement_to_record",
]
