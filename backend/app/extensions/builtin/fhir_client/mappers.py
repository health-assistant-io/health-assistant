"""Inbound FHIR R4 adapters for the ``fhir_client`` connector.

The pull-side twin of the outbound mapper set: instead of forking a second
FHIR→HA mapping layer, this module rides the existing one — Observations
through :func:`app.extensions.sdk.fhir.fhir_observation_to_create` and
Encounter/MedicationStatement through ``fhir_server.mappers`` (the same
functions the outbound connector uses to *produce* the records it now
*consumes*). Only the :class:`DiagnosticReport` adapter is genuinely new
(the ``labs`` stream takes ingest-funnel payload dicts, which no outbound
mapper emits).

Conventions inherited from the funnel (``services/integration_ingest.py``):

- every payload is attached to the **local** ``patient_id`` of the owning
  integration; the remote patient id is only the search key;
- ``external_id``-shaped identities stay on the payloads that have the
  columns (examinations, medications); observations dedup on their natural
  key and lab reports on (patient, code, effective) — the connector only
  produces deterministic shapes;
- a resource that cannot be mapped returns ``None`` — the provider counts
  it, the funnel never sees it, nothing is dropped silently.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from app.extensions.builtin.fhir_server.mappers import (
    encounter_to_exam,
    medication_statement_to_record,
)
from app.extensions.sdk.fhir import fhir_observation_to_create

#: A single inbound resource larger than this is treated as hostile
#: (crash-the-parser payloads, base64 dump bins) and rejected before any
#: mapping work. The largest legitimate clinical resources (documented
#: DiagnosticReports with presentedForm) stay two orders of magnitude below.
MAX_RESOURCE_JSON_CHARS = 400_000


def resource_is_hostile(resource: Any) -> str | None:
    """Typed rejection reason for a resource the connector refuses to map.

    Returns ``None`` for acceptable shapes; ``"wrong_type"`` when the entry
    is not a JSON object and ``"oversized"`` when its serialized form
    exceeds :data:`MAX_RESOURCE_JSON_CHARS`.
    """
    if not isinstance(resource, dict):
        return "wrong_type"
    if len(json.dumps(resource, default=str)) > MAX_RESOURCE_JSON_CHARS:
        return "oversized"
    return None


def observations_from_bundle_entry(
    fhir_obs: dict[str, Any],
    *,
    tenant_id: UUID,
    patient_id: UUID,
):
    """FHIR Observation → :class:`ObservationCreate` (the SDK mapper)."""
    return fhir_observation_to_create(fhir_obs, tenant_id=tenant_id, patient_id=patient_id)


def medication_statement_to_create(fhir_statement: dict[str, Any], *, patient_id: UUID):
    """FHIR MedicationStatement → :class:`MedicationRecordCreate`."""
    return medication_statement_to_record(fhir_statement, patient_id=patient_id)


def encounter_to_examination(fhir_encounter: dict[str, Any], *, patient_id: UUID):
    """FHIR Encounter → :class:`ExaminationCreate` (the appointments stream)."""
    return encounter_to_exam(fhir_encounter, patient_id=patient_id)


def diagnostic_report_to_payload(
    fhir_report: dict[str, Any],
    *,
    patient_id: UUID,
) -> dict[str, Any] | None:
    """FHIR DiagnosticReport → an ``ingest_labs`` payload dict.

    Strict on shape: a non-dict code or a non-string status is a rejected
    resource (``None``), never a best-effort coercion — a hostile or
    half-written report must surface as a typed rejection, not as a row
    with fabricated fields.
    """
    if not isinstance(fhir_report, dict):
        return None
    if fhir_report.get("resourceType") != "DiagnosticReport":
        return None
    code = fhir_report.get("code")
    if not isinstance(code, dict) or not code:
        return None
    status = fhir_report.get("status")
    if not isinstance(status, str) or not status:
        return None

    effective = _parse_fhir_datetime(
        fhir_report.get("effectiveDateTime")
        or (fhir_report.get("effectivePeriod") or {}).get("start")
    )
    issued = _parse_fhir_datetime(fhir_report.get("issued"))
    subject = {"reference": f"Patient/{patient_id}"}
    payload: dict[str, Any] = {
        "status": status,
        "code": code,
        "subject": subject,
        "patient_id": patient_id,
    }
    if effective is not None:
        payload["effective_datetime"] = effective
    if issued is not None:
        payload["issued"] = issued
    conclusion = fhir_report.get("conclusion")
    if isinstance(conclusion, str) and conclusion:
        payload["conclusion"] = conclusion[:2000]
    return payload


def patient_summary(fhir_patient: Any) -> dict[str, Any] | None:
    """Validate a remote ``Patient/{id}`` read and reduce it to binding info."""
    if not isinstance(fhir_patient, dict) or fhir_patient.get("resourceType") != "Patient":
        return None
    return {
        "id": str(fhir_patient.get("id") or ""),
        "name": _patient_name(fhir_patient),
        "birth_date": fhir_patient.get("birthDate"),
        "gender": fhir_patient.get("gender"),
    }


def _patient_name(fhir_patient: dict[str, Any]) -> str:
    names = fhir_patient.get("name") or []
    if isinstance(names, dict):
        names = [names]
    for name in names:
        if not isinstance(name, dict):
            continue
        if name.get("text"):
            return str(name["text"])
        given = name.get("given") or []
        given_text = " ".join(given) if isinstance(given, list) else str(given or "")
        full = f"{given_text} {name.get('family') or ''}".strip()
        if full:
            return full
    return "—"


def _parse_fhir_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = [
    "MAX_RESOURCE_JSON_CHARS",
    "diagnostic_report_to_payload",
    "encounter_to_examination",
    "medication_statement_to_create",
    "observations_from_bundle_entry",
    "patient_summary",
    "resource_is_hostile",
]
