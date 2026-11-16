"""Audit-diff redaction — PHI never reaches ``audit_events``.

``log_audit_action`` runs both diff payloads (before/after) through
:func:`redact_value` before they are stored: keys in :data:`REDACTED_FIELDS`
(identity, contact, free-text clinical fields — the vocabularies that carry
PHI in practice) have their values replaced with ``"[redacted]"`` at any
nesting depth; everything else (ids, statuses, timestamps, counts, HTTP
codes — the operational payload an audit viewer needs) passes through.

The map is value-shape agnostic: a ``name`` key redacts scalars, FHIR
``HumanName`` dicts and lists of them identically. Keys are matched
case-sensitively on the exact vocabulary (the app's serializers are the only
writers), and unknown keys pass through — redaction here is a hygiene map,
not a sanitizer for attacker-controlled keys (model output is untrusted and
never lands in audit diffs unvalidated).

Sanctioned rewrites stay layered on top: data-subject erasure still nulls
the actor and diffs, and the 3650-day audit retention still anonymizes the
diff payload — the write-time map keeps the *stored* window PHI-free, the
lifecycle flows keep the long tail clean.
"""

from __future__ import annotations

from typing import Any

#: The marker stored in place of a redacted value.
REDACTED = "[redacted]"

#: Keys whose values are PHI/PII-bearing in this app's serializers —
#: identity (FHIR ``HumanName`` shapes, user ``full_name``), dates of
#: birth, contact points, free-text clinical fields, and document names
#: (uploads often embed patient names). ``user_id``/``patient_id`` and the
#: other id-shaped keys stay: the audit trail must keep saying *which*
#: record changed, only not *who* the human payload was.
REDACTED_FIELDS = frozenset(
    {
        "name",
        "full_name",
        "given",
        "family",
        "first_name",
        "last_name",
        "middle_name",
        "maiden_name",
        "birth_date",
        "birthdate",
        "birthDate",
        "date_of_birth",
        "dob",
        "email",
        "email_address",
        "phone",
        "phone_number",
        "telephone",
        "telecom",
        "contact",
        "address",
        "street",
        "street_address",
        "city",
        "postal_code",
        "postcode",
        "zip",
        "zip_code",
        "mrn",
        "ssn",
        "national_id",
        "insurance_id",
        "insurance_number",
        "emergency_contact",
        "notes",
        "note",
        "text",
        "description",
        "reason",
        "comment",
        "comments",
        "summary",
        "free_text",
        "message",
        "diagnosis",
        "findings",
        "anamnesis",
        "complaint",
        "filename",
    }
)


def redact_value(value: Any) -> Any:
    """Recursively mask :data:`REDACTED_FIELDS` values in a JSON payload."""
    if isinstance(value, dict):
        return {
            key: REDACTED if key in REDACTED_FIELDS else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def redact_diffs(
    old_value: dict[str, Any] | None,  # gate-allow: *, audit diff field
    new_value: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Redact both diff payloads of one audit event."""
    return (
        None if old_value is None else redact_value(old_value),  # gate-allow: *, audit diff
        None if new_value is None else redact_value(new_value),
    )
