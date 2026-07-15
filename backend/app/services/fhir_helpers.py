"""Low-level FHIR serialization helpers shared between ORM models and the converter.

The primitives here have no app-model imports, so they are safe to import from
anywhere — including model classes. ``fhir.resources`` is imported lazily inside
the functions that need it, so a missing/broken install degrades gracefully at
call time rather than breaking module import.
"""

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from app.models.enums import ExportScope  # noqa: F401  (re-exported for callers)
from app.schemas.backup import PROVENANCE_SYSTEM, PROVENANCE_CODE


class FhirSerializationError(Exception):
    """Raised when a value cannot be serialized to / parsed from a FHIR resource."""


def fhir_isoformat(value: Optional[dt.datetime]) -> Optional[str]:
    """Serialize a datetime to a FHIR-conformant ISO-8601 string.

    FHIR R4 regex requires a timezone offset (``Z`` or ``+HH:MM``); a naive
    ``value.isoformat()`` produces e.g. ``'2026-06-20T22:39:56.471381'``
    which fails validation. Naive datetimes are assumed UTC (the project's
    canonical storage timezone). Returns ``None`` for ``None``.

    This is the defensive layer that prevents naive-datetime values from
    silently failing FHIR validation downstream.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    iso = value.isoformat()
    # Normalize '+00:00' to 'Z' for the canonical FHIR form (fhir.resources
    # accepts both, but 'Z' is the spec-preferred UTC designator).
    if iso.endswith("+00:00"):
        iso = iso[:-6] + "Z"
    return iso


def build_fhir_resource(resource_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a FHIR-shaped dict via ``fhir.resources`` and return its canonical
    camelCase dump (None values excluded).

    This is the single construction path used by every ``to_fhir_dict()`` model
    method: build the field dict, hand it here, get back guaranteed-spec-compliant
    FHIR JSON. Raises :class:`FhirSerializationError` on validation failure so the
    caller can apply its own policy (export/import skip-and-log).
    """
    from fhir.resources.R4B import get_fhir_model_class
    from pydantic import ValidationError

    try:
        cls = get_fhir_model_class(resource_type)
        validated = cls.model_validate(data)
    except ValidationError as e:
        rid = data.get("id") if isinstance(data, dict) else None
        first = e.errors()[:1]
        raise FhirSerializationError(
            f"Invalid {resource_type}{f' id={rid}' if rid else ''}: {first}"
        ) from e
    except Exception as e:  # unknown resource type, etc.
        raise FhirSerializationError(f"Could not build {resource_type}: {e}") from e
    return validated.model_dump(by_alias=True, exclude_none=True, mode="json")


def assert_valid_fhir(obj: Any) -> Dict[str, Any]:
    """Validate an ORM object's FHIR projection and return it.

    Calls ``obj.to_fhir_dict()`` (which constructs + validates via
    :func:`build_fhir_resource`) and returns the canonical FHIR dict. Raises
    :class:`FhirSerializationError` if the object cannot be projected to valid
    FHIR. Intended as the **write-time gate** in ``fhir_service.create_*``/
    ``update_*`` so invalid data can never be persisted — the root-cause fix
    for the FHIR shape-drift bug class. Call this right before ``commit()``."""
    if not hasattr(obj, "to_fhir_dict"):
        raise FhirSerializationError(
            f"{type(obj).__name__} has no to_fhir_dict(); cannot validate"
        )
    return obj.to_fhir_dict()


def validate_and_filter_observations(
    observations: list, logger=None
) -> Tuple[List[Any], int]:
    """Drop observations that cannot be projected to valid FHIR (skip-and-log).

    The write-time gate for the integration data path: every Observation is run
    through :func:`assert_valid_fhir` (which validates the whole resource via
    ``fhir.resources``). Invalid ones are dropped and counted so a single bad
    resource can never abort a whole sync; valid ones are kept.

    Returns ``(valid, dropped_count)``. Does **not** mutate the input list —
    callers that want the filtered set must use the returned ``valid`` list
    (I5: previously mutated the caller's list in place via ``observations[:] =
    valid``, which was a fragile contract — callers holding a reference saw a
    shorter list with no signal that items were removed).
    """
    valid: List[Any] = []
    dropped = 0
    for obs in observations:
        try:
            assert_valid_fhir(obs)
            valid.append(obs)
        except FhirSerializationError as e:
            dropped += 1
            if logger is not None:
                logger.warning(
                    "Skipping invalid Observation %s: %s",
                    getattr(obs, "id", "?"),
                    e,
                )
    return valid, dropped


def _enum_value(v: Any, default: Optional[str] = None) -> Optional[str]:
    """Return the string value of an enum *or* pass a raw string through.

    SQLAlchemy ``Enum`` columns hold enum instances once loaded/flushed, but a
    freshly-constructed (pre-flush) object may carry the raw string assigned at
    construction. Serializers (``to_dict``/``to_fhir_dict``) and write-time
    validation must tolerate both. Returns ``default`` for ``None``."""
    if v is None:
        return default
    return getattr(v, "value", v)


def parse_fhir_resource(resource_type: str, data: Dict[str, Any]):
    """Parse + validate a canonical FHIR dict into a typed ``fhir.resources`` model.

    The inverse of :func:`build_fhir_resource`, used by the import path. Raises
    :class:`FhirSerializationError` on validation failure.
    """
    from fhir.resources.R4B import get_fhir_model_class
    from pydantic import ValidationError

    try:
        cls = get_fhir_model_class(resource_type)
        return cls.model_validate(data)
    except ValidationError as e:
        rid = data.get("id") if isinstance(data, dict) else None
        first = e.errors()[:1]
        raise FhirSerializationError(
            f"Invalid {resource_type}{f' id={rid}' if rid else ''}: {first}"
        ) from e
    except Exception as e:  # unknown resource type, etc.
        raise FhirSerializationError(f"Could not parse {resource_type}: {e}") from e


def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
    """Drop keys whose value is None."""
    return {k: v for k, v in d.items() if v is not None}


def _as_list(v: Any) -> Optional[List[Any]]:
    """Wrap a scalar in a list; pass lists/None through unchanged."""
    if v is None:
        return None
    if isinstance(v, list):
        return v
    return [v]


def _coerce_human_name_list(v: Any) -> Optional[List[Any]]:
    """Normalize a stored Patient.name value into FHIR List[HumanName].

    FHIR ``Patient.name`` is 0..* (a list of HumanName). Some legacy rows and
    REST inputs stored a single HumanName *dict* (or even a bare string) rather
    than a list. Tolerate that shape drift at the serialization boundary so
    exports/import don't fail on historical data: dict -> [dict]; empty/None
    -> [] (valid empty list); list -> passthrough."""
    if v is None:
        return []
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return v
    return [{"text": str(v)}]


def _primary_human_name(v: Any) -> Any:
    """Return the primary HumanName as a single dict, regardless of storage.

    The REST/frontend contract is a single HumanName object (``{family, given}``
    or ``{text}``), but the JSONB column may hold either that object or a FHIR
    list of HumanName (e.g. rows created by the FHIR import path, which stores
    canonical FHIR). Normalize on read in ``to_dict()`` so the frontend always
    sees one object: list -> first element (or ``{}`` if empty); dict ->
    passthrough; None/empty -> ``{}``; bare string -> ``{"text": str}``."""
    if v is None:
        return {}
    if isinstance(v, list):
        return v[0] if v else {}
    if isinstance(v, dict):
        return v
    return {"text": str(v)}


def _clean_quantity(q: Any) -> Optional[Dict[str, Any]]:
    """Strip empty/whitespace-only string values from a FHIR Quantity dict.

    FHIR ``Quantity`` fields have strict pattern constraints — e.g. ``code``
    must match ``^[^\\s]+(\\s[^\\s]+)*$`` (non-empty, no leading/trailing
    whitespace). Legacy/integration rows sometimes store ``""`` for
    ``unit``/``code``/``system`` when the source provided none. Drop those keys
    so ``fhir.resources`` validation passes; return None if nothing meaningful
    remains so the whole ``valueQuantity`` is excluded. Non-dict input is
    returned unchanged (let the validator raise on truly malformed data).

    FHIR interop note: ``system`` and ``code`` are a coded-unit pair that
    SHOULD travel together (system defines the code's namespace). If only one
    survives cleaning, drop the other so external systems never receive a
    dangling ``system`` without ``code`` (or vice versa). ``unit`` (the
    human-readable form) is independent and kept on its own."""
    if not isinstance(q, dict):
        return q
    cleaned: Dict[str, Any] = {}
    for k, val in q.items():
        if isinstance(val, str):
            if val.strip():
                cleaned[k] = val
        elif val is not None:
            cleaned[k] = val
    if "system" in cleaned and "code" not in cleaned:
        cleaned.pop("system", None)
    if "code" in cleaned and "system" not in cleaned:
        cleaned.pop("code", None)
    return cleaned or None


def _normalize_timing(timing: Any) -> Any:
    """Normalize a FHIR Timing dict: expand short ``timeOfDay`` ("HH:MM")
    values into full ISO ("HH:MM:SS"). Non-dict values pass through."""
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
    """Build a FHIR ``meta`` block with versionId, lastUpdated, source and tag."""
    meta: Dict[str, Any] = {}
    if version_id:
        meta["versionId"] = str(version_id)
    if last_updated:
        meta["lastUpdated"] = last_updated
    meta["versionId"] = meta.get("versionId") or "1"
    meta["lastUpdated"] = (
        meta.get("lastUpdated") or dt.datetime.now(dt.timezone.utc).isoformat()
    )
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


def _extract_patient_id(ref: Optional[Dict[str, Any]]) -> Optional[str]:
    """Pull the trailing id from a FHIR reference like {"reference": "Patient/<id>"}."""
    if not ref:
        return None
    reference = ref.get("reference") if isinstance(ref, dict) else None
    if reference:
        if "/" in reference:
            return reference.split("/")[-1]
        if reference.startswith("urn:uuid:"):
            return reference.replace("urn:uuid:", "")
    return None


def coerce_patient_id(
    explicit: Any, subject: Optional[Dict[str, Any]]
) -> Optional[UUID]:
    """Resolve a relational ``patient_id`` for an Observation/DiagnosticReport
    (audit B3). Prefers an explicit value, then derives it from the FHIR
    ``subject`` reference; returns a validated ``UUID`` or ``None``.
    """
    raw = explicit if explicit else _extract_patient_id(subject)
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


def _flatten_interpretation(interpretation: Any) -> Optional[str]:
    """Flatten a FHIR interpretation value into a single display string.

    Accepts a string (passed through) or a FHIR list of CodeableConcepts
    ([{coding:[{display,code}], text}]) and returns the first non-empty
    display/code/text. None for empty/None input.

    Read-side helper for the frontend / analytics contract (which expect a
    single string). The ORM column itself stores the canonical FHIR list
    shape (JSONB) as of I6 — this helper flattens on read.
    """
    if not interpretation:
        return None
    if isinstance(interpretation, str):
        return interpretation
    if isinstance(interpretation, list):
        first = interpretation[0] if interpretation else None
        if isinstance(first, dict):
            coding = first.get("coding") or []
            if coding and isinstance(coding, list):
                c = coding[0] if isinstance(coding[0], dict) else {}
                val = c.get("display") or c.get("code")
                if val:
                    return val
            text = first.get("text")
            if text:
                return text
    return None


def _normalize_interpretation(value: Any) -> Optional[List[Dict[str, Any]]]:
    """Normalize any interpretation input to the canonical FHIR R4 list shape.

    The ORM column is JSONB (``0..* CodeableConcept``). This helper accepts:
    - ``None`` → ``None``
    - a canonical FHIR list ``[{...}]`` → passed through unchanged
    - a bare string ``"High"`` → wrapped as ``[{"text": "High"}]`` (backward
      compat for REST callers + the OCR pipeline that emit display strings)

    Used at every write path (REST create, OCR persistence, FHIR import) so
    the column always holds the canonical shape regardless of input form.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return value if value else None
    if isinstance(value, str):
        s = value.strip()
        return [{"text": s}] if s else None
    return None
