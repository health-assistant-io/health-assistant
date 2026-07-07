"""Generic search + CRUD dispatcher for the FHIR R4 facade.

The facade exposes one set of generic handlers (``search``, ``read``,
``create``, ``update``, ``delete``) that dispatch on the resource type via
:data:`RESOURCE_REGISTRY`. Each resource's model provides the FHIR
projection via ``to_fhir_dict()``; the converter module provides the
reverse ``fhir_to_*_orm()``.

This module is the meat of audit items C2, C3, C4, C5 — every list
endpoint returns a FHIR Bundle, every search honors standard search params,
every write returns canonical FHIR JSON with proper status codes + headers,
and deletes soft-delete via ``SoftDeleteMixin`` (tombstones → 410 Gone).
"""

import datetime as _dt
import json as _json
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, Float, func, literal, not_, or_, select, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.facade.bundle import build_search_bundle
from app.facade.registry import ResourceEntry
from app.facade.search_params import parse_search_params
from app.schemas.user import TokenData
from app.services.fhir_converter import fhir_to_orm
from app.services.fhir_helpers import FhirSerializationError, assert_valid_fhir
from app.services.provenance_service import (
    record_provenance,
    RECORD_CREATE,
    RECORD_DELETE,
    RECORD_UPDATE,
)


logger = logging.getLogger(__name__)


class PreconditionFailed(Exception):
    """Raised when an ``If-Match`` header's version doesn't match the current
    row's version (F5 optimistic locking). The endpoint maps this to HTTP 412.

    Carries the resource type/id and the expected vs actual version so the
    OperationOutcome diagnostics can be informative.
    """

    def __init__(
        self,
        *,
        resource_type: str,
        resource_id: str,
        expected: Any,
        actual: Any,
    ) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Version mismatch for {resource_type}/{resource_id}: "
            f"If-Match expected {expected!r}, actual {actual!r}"
        )


def _parse_if_match(header_value: str) -> Optional[Any]:
    """Parse an ``If-Match`` header value into the version number it carries.

    FHIR ETags come in two forms (both RFC 7232 compliant):
    - ``W/"<version>"`` (weak ETag — what we emit)
    - ``"<version>"`` (strong ETag)

    Returns the integer version, or None if the header can't be parsed (in
    which case we ignore it — FHIR allows servers to be lenient on ETag form).
    """
    if not header_value:
        return None
    v = header_value.strip()
    # Strip the W/ prefix (weak ETag) if present.
    if v.startswith("W/"):
        v = v[2:].strip()
    # Strip surrounding quotes.
    if v.startswith('"') and v.endswith('"'):
        v = v[1:-1]
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tenant_predicate(entry: ResourceEntry, current_user: TokenData):
    """Build the tenant-scoping predicate for the current user.

    Resources tagged ``tenant_scope='none'`` are global (no filter); others
    filter strictly on ``tenant_id == current_user.tenant_id``.
    """
    if entry.tenant_scope == "none":
        return None
    return entry.model.tenant_id == current_user.tenant_id


def _soft_delete_predicate(entry: ResourceEntry):
    """Return ``deleted_at IS NULL`` predicate if the model supports it."""
    if not entry.soft_delete:
        return None
    if not hasattr(entry.model, "deleted_at"):
        return None
    return entry.model.deleted_at.is_(None)


def _resolve_id(value: str) -> Optional[UUID]:
    """Parse a str into a UUID; return None on failure."""
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _project(row, entry: ResourceEntry) -> Dict[str, Any]:
    """Project an ORM row to its FHIR resource dict.

    Honors ``ResourceEntry.to_fhir_dict_attr`` so a single ORM model can back
    multiple FHIR resources via different projection methods (e.g.
    ``ClinicalEvent`` backs both ``Condition`` via ``to_fhir_dict`` and
    ``EpisodeOfCare`` via ``to_fhir_episode_of_care_dict``).
    """
    return getattr(row, entry.to_fhir_dict_attr)()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def search(
    entry: ResourceEntry,
    query_params: List[Tuple[str, str]],
    current_user: TokenData,
    db: AsyncSession,
    base_url: str,
) -> Dict[str, Any]:
    """Run a FHIR search and return a Bundle dict.

    Honors ``_id``, ``_lastUpdated``, ``_count``, ``_sort``, plus a small
    allowlist of resource-specific params. Tenant-scoped by default; soft-deleted
    rows excluded unless ``_deleted=true``.
    """
    params = parse_search_params(entry.resource_type, query_params)
    model = entry.model

    # Base predicates.
    predicates = []
    tenant_pred = _tenant_predicate(entry, current_user)
    if tenant_pred is not None:
        predicates.append(tenant_pred)
    soft_pred = _soft_delete_predicate(entry)
    if soft_pred is not None:
        predicates.append(soft_pred)
    if entry.search_filter is not None:
        predicates.append(entry.search_filter())

    # _id
    if params._id:
        ids = [_resolve_id(v) for v in params._id if v]
        ids = [i for i in ids if i is not None]
        if ids:
            predicates.append(model.id.in_(ids))
        else:
            # All _id values failed to parse as UUID.
            return _empty_bundle(entry, base_url, query_params)

    # _lastUpdated
    if params._lastUpdated and hasattr(model, "updated_at"):
        for f in params._lastUpdated:
            pred = f.to_orm_filter(model.updated_at)
            if pred is not None:
                predicates.append(pred)

    # Resource-specific params: token filters applied via JSONB path lookups.
    # We apply a small set per resource (patient/subject, code, status, category).
    # Per-resource ``entry.param_filter`` (if registered) is consulted first so
    # resources whose params need a join/EXISTS (e.g. Condition.category /
    # Condition.encounter) can override without polluting the generic builder.
    for key, values in params.resource_filters.items():
        for value in values:
            extra_pred = None
            if entry.param_filter is not None:
                extra_pred = entry.param_filter(model, key, value)
            if extra_pred is None:
                extra_pred = _build_resource_filter(model, key, value)
            if extra_pred is not None:
                predicates.append(extra_pred)

    # F16: _total controls whether the Bundle includes `total` AND whether we
    # pay for the COUNT(*) query. Values per FHIR spec:
    # - 'estimated'  → cheap estimate (we treat it as 'accurate' for now)
    # - 'accurate'   → exact COUNT (default behavior)
    # - 'none'       → skip COUNT entirely, omit `total` from Bundle
    total_param = (params._total or "").lower()
    skip_total = total_param == "none"

    # F16: _summary=count → return only the count (empty entry[], still has
    # total). Skip the main SELECT — useful for cheap "how many match?"
    # queries.
    summary_count = (params._summary or "").lower() in ("count", "true")

    # Count query (full match count for pagination). Skipped when _total=none
    # (F16) — saves a COUNT(*) per search.
    if skip_total:
        total = 0  # not included in the Bundle anyway
    else:
        count_stmt = (
            select(func.count()).select_from(model).where(*predicates)
            if predicates
            else select(func.count()).select_from(model)
        )
        total = (await db.execute(count_stmt)).scalar_one()

    # _summary=count short-circuits: empty entry[], total returned.
    if summary_count:
        raw_qs = "&".join(f"{k}={v}" for k, v in query_params)
        return build_search_bundle(
            base_url=base_url,
            path=entry.route_path,
            query_string=raw_qs.encode("utf-8"),
            resources=[],
            total=total,
            offset=params.offset,
            count=params._count,
            include_total=not skip_total,
        )

    # Main query with sort + pagination.
    stmt = select(model)
    if predicates:
        stmt = stmt.where(*predicates)
    for sort_key, descending in params._sort or [("updated_at", True)]:
        # ``sort_key`` is usually an ORM column-name string; for expression-based
        # sorts (e.g. Patient?_sort=name over a JSONB list of HumanName) it's a
        # callable that builds the SQL expression lazily (avoids circular imports
        # at module load time).
        if callable(sort_key):
            expr = sort_key()
        else:
            expr = getattr(model, sort_key, None)
            if expr is None:
                continue
        stmt = stmt.order_by(expr.desc() if descending else expr.asc())
    stmt = stmt.limit(params._count).offset(params.offset)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    # Serialize each row to FHIR. Skip-and-log on validation failure.
    resources: List[Dict[str, Any]] = []
    for row in rows:
        try:
            resources.append(_project(row, entry))
        except FhirSerializationError as e:
            logger.warning(
                "Skipping invalid %s/%s in search results: %s",
                entry.resource_type,
                getattr(row, "id", "?"),
                e,
            )

    # F14: _elements — project each resource to only the requested top-level
    # fields. Per FHIR R4 spec, the server always includes `resourceType`,
    # `id`, and `meta` regardless of _elements. We apply this post-serialization
    # so the projection doesn't bypass the validator.
    if params._elements:
        resources = [_project_elements(r, params._elements) for r in resources]

    # Build the Bundle. Preserve the original query string for self-link.
    raw_qs = "&".join(f"{k}={v}" for k, v in query_params)
    return build_search_bundle(
        base_url=base_url,
        path=entry.route_path,
        query_string=raw_qs.encode("utf-8"),
        resources=resources,
        total=total,
        offset=params.offset,
        count=params._count,
        include_total=not skip_total,
    )


def _jsonb_codeable_concept_match(column, value: str, *, is_list: bool):
    """Build a JSONB ``@>`` containment predicate for FHIR token search against
    a CodeableConcept column.

    FHIR ``CodeableConcept`` is ``{coding: [{system, code, display}], text}``.
    The ``@>`` containment operator matches if the stored JSON contains the
    supplied fragment anywhere in its structure — so a resource with multiple
    codings (``coding: [{code: A}, {code: B}]``) matches both ``code=A`` and
    ``code=B``. This is the F9 fix: previously only ``coding[0]`` was inspected.

    Args:
        column: the SQLAlchemy JSONB column (e.g. ``Observation.code``).
        value: the search token. Supports:
            - ``"1234-5"`` (bare code) — matches any coding with that code.
            - ``"http://loinc.org|1234-5"`` (system|code) — matches the
              system+code pair.
        is_list: ``True`` if the column is a list of CodeableConcept (e.g.
            ``Observation.category``); ``False`` if it's a single
            CodeableConcept (e.g. ``Observation.code``).

    Returns ``None`` if the JSONB operator is unavailable (column not JSONB).
    """
    # Only emit @> against real JSONB columns — the SQLAlchemy column.type
    # tells us. This keeps the predicate a no-op against enum/text columns.
    col_type = getattr(getattr(column, "type", None), "__class__", None)
    if col_type is None or col_type.__name__ != "JSONB":
        return None

    if "|" in value:
        system, code = value.split("|", 1)
        coding_fragment: Dict[str, Any] = {"system": system, "code": code}
    else:
        coding_fragment = {"code": value}

    if is_list:
        # column is a list of CodeableConcept; wrap the fragment in a list.
        rhs = _json.dumps([{"coding": [coding_fragment]}])
    else:
        rhs = _json.dumps({"coding": [coding_fragment]})

    # @> containment. The literal is typed as String so it can be rendered
    # with literal_binds (the JSONB literal renderer isn't available at
    # compile time); PG's CAST(... AS JSONB) does the type conversion.
    rhs_literal = literal(rhs, String)
    return column.op("@>")(rhs_literal.cast(JSONB))


# Conventional FHIR resource type for each reference field — used when the
# client sends a bare UUID (no `Type/` prefix) and we need to build the JSONB
# fragment. Per-resource type rules per FHIR R4 spec; we use the most common
# type for each field in our model layer.
_REFERENCE_TYPE_HINTS: Dict[str, str] = {
    "performer": "Practitioner",
    "author": "Practitioner",
    "sender": "Practitioner",
    "recipient": "Patient",
    "agent": "Practitioner",
    "target": "Observation",  # generic; overridden by Type/uuid form
    "partof": "Organization",
    "parent": "Device",
    "subject": "Patient",
    "patient": "Patient",
}


def _jsonb_reference_match(model, field_name: str, value: str):
    """Build a JSONB ``@>`` reference predicate for a reference-bearing field.

    Handles the common FHIR reference forms:
    - ``field=Type/uuid`` → fragment ``{"reference": "Type/uuid"}`` (preferred).
    - ``field=uuid`` → uses :data:`_REFERENCE_TYPE_HINTS` to pick the type
      (e.g. ``performer`` → ``Practitioner/uuid``).
    - ``field=urn:uuid:uuid`` → urn:uuid form (rare for non-bundle requests).

    The column may be a single Reference JSONB (``{"reference": "..."}``) or a
    list of References (``[{"reference": "..."}]``). We emit both fragment
    shapes inside a single ``OR (@> ..., @> ...)`` so either storage shape
    matches.

    Returns ``None`` if the column isn't present or isn't JSONB.
    """
    col = getattr(model, field_name, None)
    if col is None:
        return None
    col_type = getattr(getattr(col, "type", None), "__class__", None)
    if col_type is None or col_type.__name__ != "JSONB":
        return None

    # Normalize the value to a canonical "Type/uuid" reference string.
    if "/" in value:
        reference = value
    elif value.startswith("urn:uuid:"):
        reference = value
    else:
        # Bare UUID — pick the conventional type for this field.
        type_hint = _REFERENCE_TYPE_HINTS.get(field_name, "Patient")
        reference = f"{type_hint}/{value}"

    single_fragment = _json.dumps({"reference": reference})
    list_fragment = _json.dumps([{"reference": reference}])

    single_pred = col.op("@>")(literal(single_fragment, String).cast(JSONB))
    list_pred = col.op("@>")(literal(list_fragment, String).cast(JSONB))
    return or_(single_pred, list_pred)


def _jsonb_identifier_match(column, value: str):
    """Build a JSONB ``@>`` identifier predicate against a ``[{system, value}]``
    column (Patient/Device/Organization/Practitioner/Medication identifier).

    FHIR identifier token forms:
    - ``identifier=system|value`` → fragment ``[{"system": sys, "value": val}]``
    - ``identifier=value`` → fragment ``[{"value": val}]``
    - ``identifier=system|`` → fragment ``[{"system": sys}]``
    """
    if "|" in value:
        system, _, ident_value = value.partition("|")
        if ident_value == "":
            fragment = [{"system": system}]
        else:
            fragment = [{"system": system, "value": ident_value}]
    else:
        fragment = [{"value": value}]
    rhs = _json.dumps(fragment)
    return column.op("@>")(literal(rhs, String).cast(JSONB))


# FHIR quantity prefixes we honor for value-quantity search. Same set as date
# prefixes (minus ne/ap which don't apply to numerics) — see FHIR R4 Search.
_QUANTITY_PREFIXES = ("eq", "ne", "gt", "ge", "lt", "le", "sa", "eb", "ap")


def _value_quantity_match(column, value: str):
    """Build a numeric predicate against ``valueQuantity.value`` (JSONB path).

    FHIR quantity search format: ``[prefix][number][||system|code]``. We honor
    the prefix and number; the optional unit (system|code after ``||``) is
    parsed but not yet used for filtering (deferred).

    Examples:
    - ``value-quantity=5.4``           → ``value == 5.4``
    - ``value-quantity=gt5.4``         → ``value > 5.4``
    - ``value-quantity=5.4||mg/dL``    → ``value == 5.4`` (unit ignored for now)
    - ``value-quantity=ap10``          → ``value >= 9 AND value <= 11``
    """
    # Strip the optional ||unit suffix.
    numeric_part = value.split("||", 1)[0]

    # Optional prefix.
    prefix = "eq"
    for p in _QUANTITY_PREFIXES:
        if numeric_part.startswith(p):
            rest = numeric_part[len(p) :]
            if rest:
                prefix = p
                numeric_part = rest
                break

    try:
        number = float(numeric_part)
    except (ValueError, TypeError):
        return None

    # JSONB path valueQuantity.value → numeric. Use func to extract cast to
    # float so the comparison binds to a numeric (the stored value may be int
    # or float; PG's ->> returns text, cast to FLOAT for the comparison).
    value_expr = func.cast(column["value"].astext, Float)

    if prefix == "eq":
        return value_expr == number
    if prefix == "ne":
        return value_expr != number
    if prefix in ("gt", "sa"):
        return value_expr > number
    if prefix == "ge":
        return value_expr >= number
    if prefix in ("lt", "eb"):
        return value_expr < number
    if prefix == "le":
        return value_expr <= number
    if prefix == "ap":
        # Approximate: ±10% window.
        return and_(value_expr >= number * 0.9, value_expr <= number * 1.1)
    return value_expr == number


def _project_elements(
    resource: Dict[str, Any], elements: Optional[List[str]]
) -> Dict[str, Any]:
    """Apply the ``_elements`` projection to a FHIR resource dict.

    Per FHIR R4 spec (https://hl7.org/fhir/R4/search.html#elements), the
    server always includes ``resourceType``, ``id``, and ``meta`` regardless
    of the requested elements. The projection is applied **post-serialization**
    (after ``to_fhir_dict()`` has validated the resource) so it never bypasses
    the validator.

    Returns the input dict unchanged when ``elements`` is None or empty.
    """
    if not elements:
        return resource
    always_present = {"resourceType", "id", "meta"}
    wanted = set(elements) | always_present
    return {k: v for k, v in resource.items() if k in wanted}


def _build_resource_filter(model, key: str, value: str):
    """Build a SQLAlchemy predicate for a resource-specific search param.

    FHIR token search has several forms:
    - ``patient=Patient/uuid`` → reference lookup
    - ``patient=uuid`` → bare UUID
    - ``code=http://loinc.org|1234-5`` → system|code
    - ``code=1234-5`` → bare code
    - ``code:not=1234-5`` → token modifier :not (exclude)
    - ``status=active`` → enum/string
    - ``category=vital-signs`` → matches any CodeableConcept in the list

    Token / JSONB semantics (F9 fix):
    - For ``code`` on a JSONB CodeableConcept column: use the ``@>`` containment
      operator so multi-coding resources match (previously only ``coding[0]``
      was inspected — multi-coding resources silently missed).
    - For ``category`` on a JSONB list-of-CodeableConcept column (Observation,
      Communication, DiagnosticReport, DocumentReference): use ``@>`` on the
      list shape so any element matches (previously ``category.astext == value``
      compared the whole list as a scalar string and matched nothing).
    - ``category`` on a scalar enum column (AllergyIntolerance) keeps the simple
      equality match.
    - Token modifiers ``:not`` negates the match; other modifiers (``:above``,
      ``:below``, ``:in``, ``:text``) are deferred (Phase 9).
    """
    # Split token modifier (e.g. "code:not=1234" → key suffix ":not", value "1234").
    modifier: Optional[str] = None
    base_key = key
    if ":" in key:
        base_key, _, modifier = key.partition(":")

    negate = modifier == "not"

    if base_key in ("patient", "subject"):
        # Strip the "Patient/" prefix if present.
        raw = value.split("/")[-1] if "/" in value else value
        rid = _resolve_id(raw)
        if rid is None:
            return None
        # Try direct patient_id column first; fall back to JSONB subject lookup.
        if hasattr(model, "patient_id"):
            return model.patient_id == rid
        if hasattr(model, "subject_patient_id"):
            return model.subject_patient_id == rid
        return None
    if base_key in ("encounter", "context"):
        raw = value.split("/")[-1] if "/" in value else value
        rid = _resolve_id(raw)
        if rid is None:
            return None
        if hasattr(model, "encounter_id"):
            return model.encounter_id == rid
        if hasattr(model, "examination_id"):
            return model.examination_id == rid
        return None
    if base_key == "code":
        if not hasattr(model, "code"):
            return None
        # JSONB CodeableConcept (Observation, DiagnosticReport, …) — @> match.
        pred = _jsonb_codeable_concept_match(model.code, value, is_list=False)
        if pred is not None:
            return not_(pred) if negate else pred
        # String column (e.g. ClinicalEvent.code) — direct equality on the bare
        # code. Honor the "system|code" token form by taking the code segment.
        col_type = getattr(getattr(model.code, "type", None), "__class__", None)
        if col_type is not None and col_type.__name__ in ("String", "TEXT"):
            bare = value.split("|")[-1] if "|" in value else value
            pred = model.code == bare
            return not_(pred) if negate else pred
        return None
    if base_key == "type":
        # DocumentReference.type / DiagnosticReport.type — JSONB CodeableConcept.
        col = getattr(model, "type", None)
        if col is None:
            col = getattr(model, "code", None)
        if col is None:
            return None
        pred = _jsonb_codeable_concept_match(col, value, is_list=False)
        if pred is None:
            return None
        return not_(pred) if negate else pred
    if base_key in ("status", "clinical-status", "verification-status", "intent"):
        # Map FHIR param to model column. Status columns are typically snake_case.
        col_name_map = {
            "status": "status",
            "clinical-status": "clinical_status",
            "verification-status": "verification_status",
            "intent": "intent",
        }
        col_name = col_name_map.get(base_key)
        if not col_name or not hasattr(model, col_name):
            # Fallback: ``clinical-status`` may be stored in a ``status`` column
            # (ClinicalEvent stores HL7 condition-clinical status in ``status``).
            if base_key == "clinical-status" and hasattr(model, "status"):
                col_name = "status"
            else:
                return None
        col = getattr(model, col_name)
        # Compare case-insensitively via a text cast: PG ENUM columns (whose
        # labels are uppercase, e.g. ClinicalEventStatus) would otherwise reject
        # a lowercase FHIR token ("active") as "invalid input value for enum".
        # CAST(col AS TEXT) sidesteps enum-input validation; func.lower makes
        # the match case-insensitive on both Enum and String columns.
        pred = func.lower(col.cast(String)) == value.lower()
        return not_(pred) if negate else pred
    if base_key == "category":
        if not hasattr(model, "category"):
            return None
        col = model.category
        # Decide JSONB-list vs JSONB-scalar vs enum-column by inspecting the
        # SQLAlchemy column type. JSONB list (Observation, Communication,
        # DiagnosticReport, DocumentReference) needs the @> containment check;
        # scalar enum (AllergyIntolerance) uses simple equality.
        col_type = getattr(getattr(col, "type", None), "__class__", None)
        type_name = col_type.__name__ if col_type is not None else ""
        if type_name == "JSONB":
            # Try list-shape containment first (most categories are lists);
            # fall back to single-CodeableConcept shape (some legacy rows).
            pred = _jsonb_codeable_concept_match(col, value, is_list=True)
        else:
            # Scalar column (Enum / String) — direct case-insensitive equality.
            value_upper = value.upper()
            pred = or_(col == value, col == value_upper)
        if pred is None:
            return None
        return not_(pred) if negate else pred
    if base_key == "criticality":
        # AllergyIntolerance.criticality is a scalar enum (low|high|unable-to-assess).
        col = getattr(model, "criticality", None)
        if col is None:
            return None
        # Case-insensitive via text-cast (see status handler for rationale).
        pred = func.lower(col.cast(String)) == value.lower()
        return not_(pred) if negate else pred
    if base_key == "medication":
        # MedicationStatement/Request.medication is projected from the same
        # `code` CodeableConcept column (see Medication.to_fhir_dict). Match
        # against that column directly.
        col = getattr(model, "code", None)
        if col is None:
            return None
        pred = _jsonb_codeable_concept_match(col, value, is_list=False)
        if pred is None:
            return None
        return not_(pred) if negate else pred
    if base_key == "activity":
        # Provenance.activity is a JSONB CodeableConcept.
        col = getattr(model, "activity", None)
        if col is None:
            return None
        pred = _jsonb_codeable_concept_match(col, value, is_list=False)
        if pred is None:
            return None
        return not_(pred) if negate else pred
    if base_key in (
        "performer",
        "sender",
        "recipient",
        "agent",
        "target",
        "partof",
        "parent",
        "author",
    ):
        # Reference-bearing fields stored as JSONB. Build a @> containment
        # fragment for {"reference": "Type/uuid"} (single) or
        # [{"reference": "Type/uuid"}] (list). Bare UUIDs are normalized to the
        # conventional reference type for the field (e.g. performer→Practitioner).
        ref_pred = _jsonb_reference_match(model, base_key, value)
        if ref_pred is None:
            return None
        return not_(ref_pred) if negate else ref_pred
    if base_key == "identifier":
        # JSONB list of {system, value}. FHIR format: system|value | value |
        # system|. Use @> containment with whichever fragment is provided.
        col = getattr(model, "identifier", None)
        if col is None:
            return None
        col_type = getattr(getattr(col, "type", None), "__class__", None)
        if col_type is None or col_type.__name__ != "JSONB":
            return None
        pred = _jsonb_identifier_match(col, value)
        if pred is None:
            return None
        return not_(pred) if negate else pred
    if base_key in ("name", "family", "given"):
        # Patient/Practitioner name search. Stored as JSONB list of HumanName
        # (or legacy single dict). Match via substring on the text-cast of the
        # whole name blob — pragmatic v1 that catches any family/given token;
        # we don't distinguish family vs given here (defer).
        col = getattr(model, "name", None)
        if col is None:
            return None
        col_type = getattr(getattr(col, "type", None), "__class__", None)
        if col_type is None or col_type.__name__ != "JSONB":
            return None
        # Cast JSONB → text and ILIKE the value as a substring. Handles both
        # storage shapes (list-of-HumanName, single-HumanName-dict) uniformly.
        pred = col.cast(String).ilike(f"%{value}%")
        return not_(pred) if negate else pred
    if base_key in ("gender", "active"):
        # Simple scalar tokens. gender is a String/Enum column; active is bool.
        col = getattr(model, base_key, None)
        if col is None:
            # active may be missing on some resources — no-op.
            return None
        if base_key == "active":
            # Boolean: "true"/"false". Accept both case variants.
            bool_val = value.strip().lower() in ("true", "1", "yes")
            pred = col.is_(bool_val)
        else:
            # gender is an Enum column — case-insensitive via text-cast.
            pred = func.lower(col.cast(String)) == value.lower()
        return not_(pred) if negate else pred
    if base_key == "value-quantity":
        # Observation.value-quantity: [prefix][number][||system|code] against
        # the valueQuantity.value JSONB numeric path. Optional system|code
        # narrows the unit; we ignore it for the numeric comparison but
        # surface it in the documentation as a follow-up.
        col = getattr(model, "value_quantity", None)
        if col is None:
            return None
        pred = _value_quantity_match(col, value)
        if pred is None:
            return None
        return not_(pred) if negate else pred
    # Date params: onset-date, date, effective, sent, received, authored-on.
    # Routes through DateFilter.to_orm_filter (the same path _lastUpdated uses)
    # so FHIR precision semantics (year/month/day implicit ranges) and the
    # prefix matrix (eq/ne/gt/ge/lt/le/sa/eb/ap) are honored uniformly —
    # single source of truth.
    date_param_to_col = {
        "date": "examination_date",
        "onset-date": "onset_date",
        "effective": "effective_datetime",
        "sent": "sent",
        "received": "received",
        "authored-on": "created_at",
        "recorded": "recorded",
        "birthdate": "birth_date",
    }
    if key in date_param_to_col:
        col_name = date_param_to_col[key]
        col = getattr(model, col_name, None)
        if col is None:
            return None
        from app.facade.search_params import _split_date_param

        f = _split_date_param(value)
        return f.to_orm_filter(col)
    return None


def _empty_bundle(
    entry: ResourceEntry, base_url: str, query_params: List[Tuple[str, str]]
) -> Dict[str, Any]:
    raw_qs = "&".join(f"{k}={v}" for k, v in query_params)
    return build_search_bundle(
        base_url=base_url,
        path=entry.route_path,
        query_string=raw_qs.encode("utf-8"),
        resources=[],
        total=0,
        offset=0,
        count=50,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def read(
    entry: ResourceEntry,
    resource_id: str,
    current_user: TokenData,
    db: AsyncSession,
) -> Optional[Dict[str, Any]]:
    """Fetch one resource by id. Returns the FHIR dict or None (with a 'reason'
    indicator the caller uses to choose 404 vs 410)."""
    rid = _resolve_id(resource_id)
    if rid is None:
        return None

    model = entry.model
    predicates = [model.id == rid]
    tenant_pred = _tenant_predicate(entry, current_user)
    if tenant_pred is not None:
        predicates.append(tenant_pred)

    result = await db.execute(select(model).where(*predicates))
    row = result.scalar_one_or_none()
    if row is None:
        return None

    # Tombstone check.
    if entry.soft_delete and hasattr(row, "deleted_at") and row.deleted_at is not None:
        return {"_tombstone": True, "id": str(row.id)}

    return _project(row, entry)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create(
    entry: ResourceEntry,
    fhir_data: Dict[str, Any],
    current_user: TokenData,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Create a new resource from canonical FHIR JSON.

    Returns the persisted FHIR dict. Raises ``FhirSerializationError`` on
    invalid input. Records a Provenance on success.
    """
    if "create" not in entry.interactions:
        raise PermissionError(f"create not supported for {entry.resource_type}")

    # Convert canonical FHIR → ORM-shape dict via the registered converter.
    orm_dict = fhir_to_orm(entry.resource_type, fhir_data)

    # Construct the ORM object. Strip the id if the client supplied one —
    # we always generate a new id server-side.
    orm_dict.pop("id", None)
    model = entry.model
    obj = model(**orm_dict)

    # Force tenant_id to the current user's tenant for compartment resources.
    if entry.tenant_scope == "tenant_id":
        obj.tenant_id = current_user.tenant_id

    # Validate the FHIR projection before persisting. This is the write-time
    # gate that guarantees invalid FHIR can never be persisted via the facade.
    assert_valid_fhir(obj)

    db.add(obj)
    await db.flush()  # assign id without committing
    fhir_response = _project(obj, entry)

    # Best-effort Provenance.
    if entry.resource_type != "Provenance":
        await record_provenance(
            db,
            target_resource_type=entry.resource_type,
            target_id=obj.id,
            activity=RECORD_CREATE,
            tenant_id=current_user.tenant_id,
            user_id=current_user.user_id,
            client_id=getattr(current_user, "client_id", None),
        )

    await db.commit()
    await db.refresh(obj)
    return _project(obj, entry)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def update(
    entry: ResourceEntry,
    resource_id: str,
    fhir_data: Dict[str, Any],
    current_user: TokenData,
    db: AsyncSession,
    if_match: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update an existing resource. Returns the updated FHIR dict, or None
    if the resource doesn't exist.

    F5: if ``if_match`` is supplied (the raw ``If-Match`` header value, e.g.
    ``W/"3"`` or ``"3"``), the version must match the current row's version
    or a ``PreconditionFailed`` is raised (HTTP 412). This implements
    optimistic locking even though we declare ``versioning="no-version"`` in
    the CapabilityStatement (the versionId/ETag is still tracked in the row's
    ``VersionedMixin.version`` column and exposed in the ETag header).
    """
    if "update" not in entry.interactions:
        raise PermissionError(f"update not supported for {entry.resource_type}")

    rid = _resolve_id(resource_id)
    if rid is None:
        return None

    model = entry.model
    predicates = [model.id == rid]
    tenant_pred = _tenant_predicate(entry, current_user)
    if tenant_pred is not None:
        predicates.append(tenant_pred)

    result = await db.execute(select(model).where(*predicates))
    obj = result.scalar_one_or_none()
    if obj is None:
        return None

    # F5: If-Match optimistic locking. If the header is present, the version
    # in the ETag must match the current row's version exactly; otherwise 412.
    if if_match is not None:
        expected_version = _parse_if_match(if_match)
        if expected_version is not None:
            current_version = getattr(obj, "version", None) or 1
            if int(expected_version) != int(current_version):
                raise PreconditionFailed(
                    resource_type=entry.resource_type,
                    resource_id=str(rid),
                    expected=expected_version,
                    actual=current_version,
                )

    # Convert the incoming FHIR to ORM-shape and apply mutations.
    orm_dict = fhir_to_orm(entry.resource_type, fhir_data)
    orm_dict.pop("id", None)  # don't allow id mutation
    if "tenant_id" in orm_dict:
        orm_dict.pop("tenant_id")  # don't allow tenant mutation
    for key, value in orm_dict.items():
        if hasattr(obj, key):
            setattr(obj, key, value)

    # Bump version if versioned.
    if entry.versioned and hasattr(obj, "version"):
        obj.version = (obj.version or 1) + 1

    # Validate after mutation.
    assert_valid_fhir(obj)

    await record_provenance(
        db,
        target_resource_type=entry.resource_type,
        target_id=obj.id,
        activity=RECORD_UPDATE,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
    )

    await db.commit()
    await db.refresh(obj)
    return _project(obj, entry)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def delete(
    entry: ResourceEntry,
    resource_id: str,
    current_user: TokenData,
    db: AsyncSession,
) -> bool:
    """Soft-delete (tombstone) a resource. Returns True on success.

    Audit item C5: subsequent reads return 410 Gone (tombstone semantics),
    NOT 404 Not Found. Hard deletes are never used by the facade.
    """
    if "delete" not in entry.interactions:
        raise PermissionError(f"delete not supported for {entry.resource_type}")

    rid = _resolve_id(resource_id)
    if rid is None:
        return False

    model = entry.model
    predicates = [model.id == rid]
    tenant_pred = _tenant_predicate(entry, current_user)
    if tenant_pred is not None:
        predicates.append(tenant_pred)

    result = await db.execute(select(model).where(*predicates))
    obj = result.scalar_one_or_none()
    if obj is None:
        return False

    if entry.soft_delete and hasattr(obj, "deleted_at"):
        obj.deleted_at = _dt.datetime.now(_dt.timezone.utc)
    else:
        # No soft-delete support; hard-delete (rare — only Provenance-ish resources).
        await db.delete(obj)

    await record_provenance(
        db,
        target_resource_type=entry.resource_type,
        target_id=obj.id if hasattr(obj, "id") else rid,
        activity=RECORD_DELETE,
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
    )

    await db.commit()
    return True
