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
    {
        "_id",
        "_lastUpdated",
        "_count",
        "_sort",
        "_format",
        "_include",
        "_revinclude",
        "_summary",
        "_elements",
        "_total",
    }
)

# Per-resource params we recognize AND honor in facade.crud._build_resource_filter.
# F8: previously this list advertised params that the dispatcher silently
# dropped (clients couldn't tell if their filter was honored). It now contains
# only params with a real handler in _build_resource_filter; rarely-used long-
# tail params (Device.manufacturer/model, Medication.form, Encounter.class /
# reason-code / diagnosis / practitioner, Condition.severity, Provenance.entity)
# are deliberately omitted — when implementation lands, add them back here.
RESOURCE_PARAMS: Dict[str, frozenset] = {
    "Patient": frozenset(
        {"identifier", "name", "family", "given", "birthdate", "gender", "active"}
    ),
    "Observation": frozenset(
        {
            "patient",
            "subject",
            "code",
            "date",
            "status",
            "category",
            "value-quantity",
            "performer",
        }
    ),
    "Condition": frozenset(
        {
            "patient",
            "subject",
            "code",
            "clinical-status",
            "verification-status",
            "onset-date",
            "category",
            "encounter",
        }
    ),
    "Encounter": frozenset({"patient", "subject", "status", "date"}),
    "Device": frozenset(
        {"patient", "subject", "identifier", "type", "status", "parent"}
    ),
    "MedicationStatement": frozenset(
        {"patient", "subject", "status", "medication", "effective", "context"}
    ),
    "MedicationRequest": frozenset(
        {
            "patient",
            "subject",
            "status",
            "intent",
            "medication",
            "encounter",
            "authored-on",
        }
    ),
    "Medication": frozenset({"code", "identifier"}),
    "AllergyIntolerance": frozenset(
        {
            "patient",
            "subject",
            "clinical-status",
            "verification-status",
            "category",
            "criticality",
            "code",
            "onset-date",
        }
    ),
    "DiagnosticReport": frozenset(
        {"patient", "subject", "status", "code", "category", "date", "encounter"}
    ),
    "DocumentReference": frozenset(
        {"patient", "subject", "status", "type", "category", "date", "encounter"}
    ),
    "Communication": frozenset(
        {
            "patient",
            "subject",
            "status",
            "category",
            "sent",
            "received",
            "sender",
            "recipient",
        }
    ),
    "Organization": frozenset({"identifier", "name", "active", "type", "partof"}),
    "Practitioner": frozenset({"identifier", "name", "family", "given", "active"}),
    "Provenance": frozenset({"target", "agent", "recorded", "activity"}),
}

# Default and bounds for _count.
DEFAULT_COUNT = 50
MAX_COUNT = 250


# Sort columns allowlist per resource. Values map a FHIR search param to either:
# - an ORM column name string (the common case), OR
# - a callable that builds a SQLAlchemy sort expression (for expression-based
#   sorts like JSONB path extraction). The dispatcher in ``facade.crud.search``
#   calls the callable when it's not a string.
# Dispatch rejects any sort key not in this map (defends against SQL injection
# via raw column names).
def _patient_family_name_sort():
    """Build a case-insensitive sort expression for ``Patient?_sort=name``.

    Handles both stored shapes of ``Patient.name``:
    - a list of HumanName (FHIR canonical; the new shape post-FHIR-import),
    - a single HumanName dict (legacy REST-created rows).

    ``name -> 0 ->> 'family'`` extracts the family of the first list element
    (NULL when ``name`` is a dict, not an array). ``name ->> 'family'``
    extracts the family of the root object (NULL when ``name`` is an array).
    COALESCE picks whichever is non-NULL; LOWER makes the sort case-insensitive
    so 'adams' < 'Smith' < 'Taylor' (rather than ASCII-byte order where all
    uppercase precedes all lowercase).
    """
    from sqlalchemy import func

    from app.models.fhir.patient import Patient

    return func.lower(
        func.coalesce(
            (Patient.name.op("->")(0)).op("->>")("family"),
            Patient.name.op("->>")("family"),
            "",
        )
    )


SORT_COLUMNS: Dict[str, Dict[str, Any]] = {
    "Patient": {
        "_id": "id",
        "_lastUpdated": "updated_at",
        "birthdate": "birth_date",
        "name": _patient_family_name_sort,
    },
    "Observation": {
        "_id": "id",
        "_lastUpdated": "updated_at",
        "date": "effective_datetime",
        "code": "code",
    },
    # F10: Condition.onset-date must map to onset_date (the actual ORM column).
    # Previously mapped to the nonexistent 'fhir_onset_datetime'.
    "Condition": {
        "_id": "id",
        "_lastUpdated": "updated_at",
        "onset-date": "onset_date",
    },
    "Encounter": {
        "_id": "id",
        "_lastUpdated": "updated_at",
        "date": "examination_date",
    },
    "Device": {"_id": "id", "_lastUpdated": "updated_at"},
    "MedicationStatement": {
        "_id": "id",
        "_lastUpdated": "updated_at",
        "effective": "start_date",
    },
    # F10: MedicationRequest.authored-on filter uses created_at (the FHIR
    # authoredOn is projected from Medication.created_at — see
    # Medication._to_medication_request). Sort by created_at too so filter
    # and sort agree on the same column. Previously sort used 'start_date',
    # which disagreed with the filter and produced surprising results.
    "MedicationRequest": {
        "_id": "id",
        "_lastUpdated": "updated_at",
        "authored-on": "created_at",
    },
    "Medication": {"_id": "id", "_lastUpdated": "updated_at"},
    "AllergyIntolerance": {
        "_id": "id",
        "_lastUpdated": "updated_at",
        "onset-date": "onset_date",
    },
    "DiagnosticReport": {
        "_id": "id",
        "_lastUpdated": "updated_at",
        "date": "effective_datetime",
    },
    "DocumentReference": {
        "_id": "id",
        "_lastUpdated": "updated_at",
        "date": "created_at",
    },
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

        Honors FHIR R4 date-precision semantics
        (https://hl7.org/fhir/R4/search.html#date): a value with year/month/day
        precision defines an *implicit range* of all instants within that
        period, and each prefix compares against that range rather than the
        naive single-instant reading.

        - ``eq2024`` → everything in 2024 (any time) — range overlap.
        - ``ne2024`` → nothing in 2024 — range non-overlap.
        - ``gt2024`` / ``sa2024`` → strictly after 2024 (>= 2025-01-01).
        - ``ge2024`` → at or after start of 2024 (>= 2024-01-01).
        - ``lt2024`` / ``eb2024`` → strictly before 2024 (< 2024-01-01).
        - ``le2024`` → at or before end of 2024 (< 2025-01-01).
        - ``ap2024`` → ±1 day window around the range (server-discretionary).

        Returns ``None`` if the value cannot be parsed (the caller should
        ignore such filters rather than 400 — FHIR search is lenient).
        """
        from sqlalchemy import and_, or_

        rng = _parse_fhir_date_range(self.value)
        if rng is None:
            return None
        start, end = rng

        prefix = self.prefix or "eq"
        if prefix == "eq":
            # Range overlap: the actual datetime falls within [start, end).
            return and_(column >= start, column < end)
        if prefix == "ne":
            return or_(column < start, column >= end)
        if prefix in ("gt", "sa"):
            # Strictly after the implicit period.
            return column >= end
        if prefix == "ge":
            return column >= start
        if prefix in ("lt", "eb"):
            # Strictly before the implicit period.
            return column < start
        if prefix == "le":
            return column < end
        if prefix == "ap":
            # ±1 day window around the range — server-discretionary per FHIR.
            import datetime as _dt

            return and_(
                column >= start - _dt.timedelta(days=1),
                column < end + _dt.timedelta(days=1),
            )
        # Unknown prefix: fall back to eq semantics.
        return and_(column >= start, column < end)


@dataclass
class FhirSearchParams:
    """Typed view of FHIR search query params for one request."""

    resource_type: str
    # Standard params.
    _id: Optional[List[str]] = None
    _lastUpdated: Optional[List[DateFilter]] = None
    _count: int = DEFAULT_COUNT
    _sort: List[Tuple[str, bool]] = field(
        default_factory=list
    )  # (column_name, descending)
    _format: Optional[str] = None
    _include: List[str] = field(default_factory=list)
    _revinclude: List[str] = field(default_factory=list)
    _summary: Optional[str] = None
    # F16: _total controls whether the Bundle includes the `total` key AND
    # whether the dispatcher runs the COUNT(*) query. Values: 'accurate'
    # (default), 'estimated' (treated as accurate), 'none' (skip + omit).
    _total: Optional[str] = None
    # F14: _elements is a comma-separated list of top-level fields to include
    # in the returned resources (e.g. `_elements=name,birthDate`). The
    # dispatcher projects each resource to the requested fields plus the
    # always-present `resourceType`, `id`, `meta` (per FHIR R4 spec).
    _elements: Optional[List[str]] = None
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

    Note: this returns the *start* of the implicit period for date-precision
    values (e.g. ``"2024"`` → ``2024-01-01T00:00:00Z``). For FHIR search
    comparisons that must honor the precision's implicit range, use
    :func:`_parse_fhir_date_range` instead.
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


def _parse_fhir_date_range(value: str) -> Optional[Tuple[Any, Any]]:
    """Parse a FHIR date/datetime into a ``(start_inclusive, end_exclusive)``
    timezone-aware datetime tuple, honoring FHIR precision semantics.

    Per https://hl7.org/fhir/R4/search.html#date, a date with year/month/day
    precision defines an *implicit range* of all instants within that period:

    - ``"2024"`` → ``[2024-01-01T00:00:00Z, 2025-01-01T00:00:00Z)``
    - ``"2024-05"`` → ``[2024-05-01T00:00:00Z, 2024-06-01T00:00:00Z)``
    - ``"2024-05-15"`` → ``[2024-05-15T00:00:00Z, 2024-05-16T00:00:00Z)``
    - ``"2024-05-15T13:30:00Z"`` → ``[that instant, that instant + 1µs)`` so
      ``eq`` over an exact instant still works correctly.

    Returns ``None`` if the value cannot be parsed.
    """
    import datetime as _dt
    from datetime import timezone, timedelta

    s = value.strip()
    if not s:
        return None

    # Detect precision by length / separator, then parse the start instant.
    # Year precision: "YYYY" (4 chars, no dash).
    if s.isdigit() and len(s) == 4:
        try:
            year = int(s)
            start = _dt.datetime(year, 1, 1, tzinfo=timezone.utc)
            end = _dt.datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            return (start, end)
        except ValueError:
            return None

    # Year-month precision: "YYYY-MM" (7 chars).
    if len(s) == 7 and s[4] == "-":
        try:
            year, month = map(int, s.split("-"))
            start = _dt.datetime(year, month, 1, tzinfo=timezone.utc)
            if month == 12:
                end = _dt.datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                end = _dt.datetime(year, month + 1, 1, tzinfo=timezone.utc)
            return (start, end)
        except (ValueError, IndexError):
            return None

    # Date precision: "YYYY-MM-DD" exactly (10 chars, no time separator).
    # The previous condition (len >= 10) was too greedy — it would match the
    # date prefix of a full datetime like "2024-05-15T13:30:00Z" and silently
    # drop the time component, returning a day-precision range. Check the
    # boundary explicitly.
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            start = _dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            return (start, end)
        except ValueError:
            return None

    # Datetime precision: try the full ISO formats; an exact instant has no
    # range to expand, so the range is [instant, instant+1µs).
    parsed = _parse_fhir_datetime(s)
    if parsed is None:
        return None
    return (parsed, parsed + timedelta(microseconds=1))


def _split_date_param(value: str) -> DateFilter:
    """Split a FHIR date param value into optional prefix + value."""
    for prefix in DATE_PREFIXES:
        if value.startswith(prefix):
            rest = value[len(prefix) :]
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
                params._lastUpdated = (params._lastUpdated or []) + [
                    _split_date_param(value)
                ]
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
                # F14: only JSON-family formats are supported. Reject XML
                # explicitly with a 400 (the previous behavior silently
                # returned JSON regardless of the requested format, which
                # was misleading). The spec-correct mime types for JSON are
                # ``json``, ``application/json``, ``application/fhir+json``.
                v_lower = value.lower().strip()
                supported = {
                    "json",
                    "application/json",
                    "application/fhir+json",
                }
                if v_lower in supported or "json" in v_lower:
                    params._format = value
                else:
                    # XML, RDF, TTL, etc. — explicit not-supported per FHIR.
                    raise HTTPException(
                        status_code=400,
                        detail=_operation_outcome_detail(
                            "fatal",
                            f"_format={value!r} is not supported. Only JSON-family "
                            f"formats (json, application/json, application/fhir+json) "
                            f"are implemented.",
                        ),
                    )
            elif key == "_include":
                params._include.append(value)
            elif key == "_revinclude":
                params._revinclude.append(value)
            elif key == "_summary":
                params._summary = value
            elif key == "_total":
                params._total = value
            elif key == "_elements":
                # Comma-separated list of top-level fields. Empty tokens dropped.
                fields_list = [t.strip() for t in value.split(",") if t.strip()]
                if fields_list:
                    params._elements = fields_list
        elif key in allowed:
            params.resource_filters.setdefault(key, []).append(value)
        elif key == "page":
            # Pagination cursor (1-based page number). The FHIR R4 spec does
            # not mandate a specific cursor param — pagination is driven by
            # the Bundle's link[] relations (self/first/last/previous/next).
            # We use `page` (1-based) as our cursor because it's the most
            # common convention (Vonk/Firely, and most REST APIs). HAPI uses
            # `_page`; we accept both forms on input and emit `page` on output.
            try:
                page = int(value)
                if page < 1:
                    raise ValueError
                params.offset = (page - 1) * params._count
            except ValueError:
                pass  # ignore malformed page
        elif key == "_page":
            # HAPI-style pagination cursor (alias of `page`).
            try:
                page = int(value)
                if page < 1:
                    raise ValueError
                params.offset = (page - 1) * params._count
            except ValueError:
                pass
        elif key == "_offset":
            # Tolerant alias: some legacy/non-standard clients (and the
            # previous version of this server) emit ``_offset=N`` in pagination
            # links. We emit ``page=N`` on output (most common convention) but
            # accept ``_offset`` on input so those legacy links keep working.
            # The value is the 0-based row offset.
            try:
                off = int(value)
                if off < 0:
                    raise ValueError
                params.offset = off
            except ValueError:
                pass  # ignore malformed _offset
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
