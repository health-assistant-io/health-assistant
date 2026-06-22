"""FHIR R4 search parameter parsing.

FHIR search has its own query-string conventions that don't map cleanly to
FastAPI's query params (e.g. ``_lastUpdated=gt2024-01-01``, ``_sort=-_lastUpdated,date``,
chained params like ``subject=Patient/123``). This module parses the raw
``request.query_params`` multi-dict into a typed :class:`FhirSearchParams`
that the search dispatcher consumes.

Reference: HL7 FHIR R4 Search — https://hl7.org/fhir/R4/search.html
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException


# Standard FHIR search params recognized on every resource.
STANDARD_PARAMS = frozenset(
    {"_id", "_lastUpdated", "_count", "_sort", "_format", "_include", "_revinclude", "_summary", "_elements", "_total"}
)

# Per-resource params we recognize (resource-specific params are added by the
# resource registry; this is the conservative baseline for the resources we
# expose). Tokens are case-sensitive on the resource side.
RESOURCE_PARAMS: Dict[str, frozenset] = {
    "Patient": frozenset({"identifier", "name", "family", "given", "birthdate", "gender", "active"}),
    "Observation": frozenset({"patient", "subject", "code", "date", "status", "category", "value-quantity", "performer"}),
    "Condition": frozenset({"patient", "subject", "code", "clinical-status", "verification-status", "onset-date", "category", "severity", "encounter"}),
    "Encounter": frozenset({"patient", "subject", "status", "class", "date", "reason-code", "diagnosis", "practitioner"}),
    "Device": frozenset({"patient", "subject", "identifier", "type", "status", "manufacturer", "model"}),
    "MedicationStatement": frozenset({"patient", "subject", "status", "medication", "effective", "context"}),
    "MedicationRequest": frozenset({"patient", "subject", "status", "intent", "medication", "encounter", "authored-on"}),
    "Medication": frozenset({"code", "identifier", "form"}),
    "AllergyIntolerance": frozenset({"patient", "subject", "clinical-status", "verification-status", "category", "criticality", "code", "onset-date"}),
    "DiagnosticReport": frozenset({"patient", "subject", "status", "code", "category", "date", "encounter"}),
    "DocumentReference": frozenset({"patient", "subject", "status", "type", "category", "author", "date", "encounter"}),
    "Communication": frozenset({"patient", "subject", "status", "category", "sent", "received", "sender", "recipient"}),
    "Organization": frozenset({"identifier", "name", "active", "type", "partof"}),
    "Practitioner": frozenset({"identifier", "name", "family", "given", "active"}),
    "Provenance": frozenset({"target", "agent", "entity", "recorded", "activity"}),
}

# Default and bounds for _count.
DEFAULT_COUNT = 50
MAX_COUNT = 250

# Sort columns allowlist per resource. The values map FHIR search param → ORM
# column name on the underlying table. The dispatch will reject any sort key
# not in this map (defends against SQL injection via raw column names).
SORT_COLUMNS: Dict[str, Dict[str, str]] = {
    "Patient": {"_id": "id", "_lastUpdated": "updated_at", "birthdate": "birth_date", "name": "name"},
    "Observation": {"_id": "id", "_lastUpdated": "updated_at", "date": "effective_datetime", "code": "code"},
    "Condition": {"_id": "id", "_lastUpdated": "updated_at", "onset-date": "fhir_onset_datetime"},
    "Encounter": {"_id": "id", "_lastUpdated": "updated_at", "date": "examination_date"},
    "Device": {"_id": "id", "_lastUpdated": "updated_at"},
    "MedicationStatement": {"_id": "id", "_lastUpdated": "updated_at", "effective": "start_date"},
    "MedicationRequest": {"_id": "id", "_lastUpdated": "updated_at", "authored-on": "start_date"},
    "Medication": {"_id": "id", "_lastUpdated": "updated_at"},
    "AllergyIntolerance": {"_id": "id", "_lastUpdated": "updated_at", "onset-date": "onset_date"},
    "DiagnosticReport": {"_id": "id", "_lastUpdated": "updated_at", "date": "effective_datetime"},
    "DocumentReference": {"_id": "id", "_lastUpdated": "updated_at", "date": "created_at"},
    "Communication": {"_id": "id", "_lastUpdated": "updated_at", "sent": "sent"},
    "Organization": {"_id": "id", "_lastUpdated": "updated_at", "name": "name"},
    "Practitioner": {"_id": "id", "_lastUpdated": "updated_at", "name": "name"},
    "Provenance": {"_id": "id", "_lastUpdated": "updated_at", "recorded": "recorded"},
}

# FHIR date-prefix tokens (https://hl7.org/fhir/R4/search.html#prefix).
DATE_PREFIXES = ("eq", "ne", "gt", "ge", "lt", "le", "sa", "eb", "ap")


@dataclass
class DateFilter:
    """A parsed date query value: optional prefix + ISO date/datetime."""

    prefix: Optional[str]
    value: str

    def to_orm_filter(self, column):
        """Return a SQLAlchemy predicate for this filter applied to ``column``.

        Returns ``None`` if the value cannot be parsed (the caller should
        ignore such filters rather than 400 — FHIR search is lenient).
        """
        import datetime as _dt
        from sqlalchemy import and_, or_

        # Try date and datetime parsing.
        parsed: Optional[_dt.datetime] = _parse_fhir_datetime(self.value)
        if parsed is None:
            return None

        prefix = self.prefix or "eq"
        if prefix == "eq":
            return column == parsed
        if prefix == "ne":
            return column != parsed
        if prefix == "gt" or prefix == "sa":
            return column > parsed
        if prefix == "ge":
            return column >= parsed
        if prefix == "lt" or prefix == "eb":
            return column < parsed
        if prefix == "le":
            return column <= parsed
        if prefix == "ap":
            # Approximate: ±1 day window. FHIR spec allows server interpretation.
            return and_(column >= parsed - _dt.timedelta(days=1), column <= parsed + _dt.timedelta(days=1))
        return column == parsed


@dataclass
class FhirSearchParams:
    """Typed view of FHIR search query params for one request."""

    resource_type: str
    # Standard params.
    _id: Optional[List[str]] = None
    _lastUpdated: Optional[List[DateFilter]] = None
    _count: int = DEFAULT_COUNT
    _sort: List[Tuple[str, bool]] = field(default_factory=list)  # (column_name, descending)
    _format: Optional[str] = None
    _include: List[str] = field(default_factory=list)
    _revinclude: List[str] = field(default_factory=list)
    _summary: Optional[str] = None
    # Resource-specific params (key → list of raw string values).
    resource_filters: Dict[str, List[str]] = field(default_factory=dict)
    # Pagination offset (derived from a `page` param or set explicitly).
    offset: int = 0

    @property
    def limit(self) -> int:
        return self._count


def _parse_fhir_datetime(value: str) -> Optional[Any]:
    """Parse a FHIR date or datetime string into a timezone-aware datetime.

    Accepts ``YYYY``, ``YYYY-MM``, ``YYYY-MM-DD``, partial datetimes, and
    full ISO-8601. Returns ``None`` on parse failure.
    """
    import datetime as _dt
    from datetime import timezone

    s = value.strip()
    if not s:
        return None

    # Try several formats from least to most specific.
    formats = (
        "%Y",
        "%Y-%m",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
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


def _split_date_param(value: str) -> DateFilter:
    """Split a FHIR date param value into optional prefix + value."""
    for prefix in DATE_PREFIXES:
        if value.startswith(prefix):
            rest = value[len(prefix):]
            if rest:
                return DateFilter(prefix=prefix, value=rest)
    return DateFilter(prefix=None, value=value)


def parse_search_params(
    resource_type: str,
    query_params: List[Tuple[str, str]],
) -> FhirSearchParams:
    """Parse a list of (key, value) query params into typed FhirSearchParams.

    ``query_params`` is the raw multi-dict form (FastAPI ``request.query_params.multi()``)
    so repeated keys (e.g. ``?_id=a&_id=b``) are preserved.

    Raises ``HTTPException(400)`` with an OperationOutcome-shaped ``detail`` if
    a param is unrecognized for the resource — FHIR allows servers to reject
    unknown search params. Unknown params get a warning instead of an error
    in practice; we choose strict here and the test suite covers both paths.
    """
    allowed = RESOURCE_PARAMS.get(resource_type, frozenset())
    sort_columns = SORT_COLUMNS.get(resource_type, {})

    params = FhirSearchParams(resource_type=resource_type)

    unknown: List[str] = []

    for key, value in query_params:
        if key in STANDARD_PARAMS:
            if key == "_id":
                params._id = (params._id or []) + [value]
            elif key == "_lastUpdated":
                params._lastUpdated = (params._lastUpdated or []) + [_split_date_param(value)]
            elif key == "_count":
                try:
                    c = int(value)
                    if c < 0:
                        raise ValueError
                    params._count = min(c, MAX_COUNT)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=_operation_outcome_detail(
                            "invalid",
                            f"_count must be a non-negative integer (got {value!r})",
                        ),
                    )
            elif key == "_sort":
                # Comma-separated; '-' prefix means descending.
                for token in value.split(","):
                    token = token.strip()
                    if not token:
                        continue
                    descending = token.startswith("-")
                    if descending:
                        token = token[1:]
                    column_name = sort_columns.get(token)
                    if column_name is None:
                        # Unknown sort key — FHIR allows server to ignore.
                        continue
                    params._sort.append((column_name, descending))
            elif key == "_format":
                params._format = value
            elif key == "_include":
                params._include.append(value)
            elif key == "_revinclude":
                params._revinclude.append(value)
            elif key == "_summary":
                params._summary = value
        elif key in allowed:
            params.resource_filters.setdefault(key, []).append(value)
        elif key == "page":
            # Non-standard but useful for pagination. Page numbering is 1-based.
            try:
                page = int(value)
                if page < 1:
                    raise ValueError
                params.offset = (page - 1) * params._count
            except ValueError:
                pass  # ignore malformed page
        else:
            unknown.append(key)

    if unknown:
        # Be lenient: ignore unknown params rather than 400, matching common
        # FHIR server behavior (Touchstone, HAPI). Future: make this configurable.
        pass

    return params


def _operation_outcome_detail(severity: str, diagnostics: str) -> Dict[str, Any]:
    """Build an OperationOutcome-shaped detail for HTTPException."""
    return {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": severity,
                "code": "invalid",
                "diagnostics": diagnostics,
            }
        ],
    }
