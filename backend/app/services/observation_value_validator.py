"""Hard validator for the Observation ↔ BiomarkerDefinition value contract.

Plan: ``dev/plans/state-biomarkers-2026-08-05.md`` Step 5. This is the single
chokepoint that enforces the value-shape contract for every Observation write
path (REST create, OCR pipeline, integration sync, FHIR import).

The contract (Decision §5):

| biomarker.value_type     | supports_multi_state | Required value shape                         |
|--------------------------|----------------------|----------------------------------------------|
| QUANTITY                 | n/a                  | value_quantity present; no top-level vCC     |
| STATE                    | False                | exactly one top-level valueCodeableConcept   |
|                          |                      | with coding[0].{code,system} in allowed set  |
| STATE                    | True                 | component[] with >=2 entries, each carrying  |
|                          |                      | code + valueCodeableConcept in allowed set   |

Mismatch raises :class:`InvalidObservationValue` (mapped to HTTP 422 by the
global handler in ``app/main.py``). The validator is a **no-op when
``biomarker`` is None** — the auto-create path in
``map_observations_to_biomarkers`` may not yet have resolved a biomarker.

The validator accepts value[x] either as separate kwargs (preferred for new
call sites) or via the legacy ``observation_data`` dict shape (the keys
actually used by the REST create path).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.models.biomarker_model import BiomarkerDefinition
from app.models.enums import BiomarkerValueType


class InvalidObservationValue(Exception):
    """The observation's value[x] shape doesn't match its biomarker's contract."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


AllowedStateSet = Iterable[Tuple[str, str]]


def _resolve_state_set(biomarker: BiomarkerDefinition) -> AllowedStateSet:
    """Build the ``{(code, system)}`` set this STATE biomarker accepts.

    Assumes ``biomarker.allowed_states`` is loaded (it's ``selectin``). Each
    join row carries the related ``state`` with ``code`` and ``system``.
    """
    out = set()
    for allowed in biomarker.allowed_states:
        if allowed.state is not None:
            out.add((allowed.state.code, allowed.state.system))
    return out


def _extract_coding_pair(value_cc: Any) -> Optional[Tuple[str, str]]:
    """Pull ``(code, system)`` from a valueCodeableConcept dict.

    Returns ``None`` if the shape isn't a CodeableConcept with at least one
    coding entry. Tolerates both camelCase (FHIR) and snake_case (legacy).
    """
    if not isinstance(value_cc, dict):
        return None
    coding = value_cc.get("coding") or value_cc.get("coding")
    if not isinstance(coding, list) or not coding:
        return None
    first = coding[0]
    if not isinstance(first, dict):
        return None
    code = first.get("code")
    system = first.get("system")
    if code is None or system is None:
        return None
    return (code, system)


def _check_state_membership(
    biomarker_slug: str,
    value_cc: Any,
    allowed: AllowedStateSet,
    *,
    context: str = "value",
) -> None:
    """Verify a valueCodeableConcept carries a coding from ``allowed``."""
    pair = _extract_coding_pair(value_cc)
    if pair is None:
        raise InvalidObservationValue(
            f"STATE biomarker {biomarker_slug!r} {context} "
            f"valueCodeableConcept.coding[0].{{code,system}} missing or malformed"
        )
    if pair not in set(allowed):
        raise InvalidObservationValue(
            f"STATE biomarker {biomarker_slug!r} {context} "
            f"coding {pair!r} not in allowed_states"
        )


def _normalize_component_value(comp: Any) -> Tuple[Optional[Any], Optional[Any]]:
    """Extract ``(code, valueCodeableConcept)`` from a FHIR component entry.

    Tolerates both camelCase (``valueCodeableConcept`` — FHIR R4 wire shape)
    and snake_case (``value_codeable_concept`` — ORM shape).
    """
    if not isinstance(comp, dict):
        return (None, None)
    code = comp.get("code")
    value_cc = (
        comp.get("valueCodeableConcept")
        if comp.get("valueCodeableConcept") is not None
        else comp.get("value_codeable_concept")
    )
    return (code, value_cc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_observation_value(
    biomarker: Optional[BiomarkerDefinition],
    *,
    value_quantity: Optional[Dict[str, Any]] = None,
    value_string: Optional[str] = None,
    value_codeable_concept: Optional[Dict[str, Any]] = None,
    component: Optional[List[Any]] = None,
) -> None:
    """Validate the value[x] shape against ``biomarker``'s value_type contract.

    Raises :class:`InvalidObservationValue` on contract violation. No-op when
    ``biomarker is None`` (e.g. the auto-create path before biomarker
    resolution, or observations without a biomarker_id).
    """
    if biomarker is None:
        return

    vt = biomarker.value_type

    # ----- QUANTITY -------------------------------------------------------
    if vt == BiomarkerValueType.QUANTITY:
        # A QUANTITY biomarker should not carry a top-level valueCodeableConcept
        # — that's the STATE shape and indicates a misroute.
        if value_codeable_concept is not None and not value_quantity:
            raise InvalidObservationValue(
                f"QUANTITY biomarker {biomarker.slug!r} expects value_quantity; "
                f"got valueCodeableConcept (did you mean value_type=STATE?)"
            )
        # value_string + value_quantity both absent is allowed at the validator
        # level — the FHIR write-time gate (assert_valid_fhir) handles the
        # stricter "exactly one value[x]" rule. We only enforce the
        # biomarker-typed contract here.
        return

    # ----- STATE ----------------------------------------------------------
    allowed = _resolve_state_set(biomarker)
    if not allowed:
        # Should be unreachable (Pydantic + endpoint enforce ≥1 on write) but
        # defend against manual DB edits.
        raise InvalidObservationValue(
            f"STATE biomarker {biomarker.slug!r} has no allowed_states configured"
        )

    # STATE biomarkers never accept numeric or free-text shapes.
    if value_quantity is not None:
        raise InvalidObservationValue(
            f"STATE biomarker {biomarker.slug!r} does not accept value_quantity; "
            f"use valueCodeableConcept"
        )
    if value_string is not None:
        raise InvalidObservationValue(
            f"STATE biomarker {biomarker.slug!r} does not accept value_string; "
            f"use valueCodeableConcept"
        )

    if biomarker.supports_multi_state:
        # ----- STATE + multi-state: component[] required --------------------
        if value_codeable_concept is not None:
            raise InvalidObservationValue(
                f"Multi-state biomarker {biomarker.slug!r} must use component[] "
                f"exclusively (no top-level valueCodeableConcept)"
            )
        if not component or len(component) < 2:
            raise InvalidObservationValue(
                f"Multi-state biomarker {biomarker.slug!r} requires component[] "
                f"with >=2 entries; got {len(component or [])}"
            )
        for i, comp in enumerate(component):
            comp_code, comp_value = _normalize_component_value(comp)
            if comp_code is None:
                raise InvalidObservationValue(
                    f"Multi-state biomarker {biomarker.slug!r} "
                    f"component[{i}] missing code"
                )
            if comp_value is None:
                raise InvalidObservationValue(
                    f"Multi-state biomarker {biomarker.slug!r} "
                    f"component[{i}] missing valueCodeableConcept"
                )
            _check_state_membership(
                biomarker.slug, comp_value, allowed, context=f"component[{i}]"
            )
    else:
        # ----- STATE single-state: top-level valueCodeableConcept ----------
        # Check ``component`` FIRST — a single-state biomarker with component[]
        # is a clearer semantic error than "missing valueCodeableConcept".
        if component:
            raise InvalidObservationValue(
                f"Single-state biomarker {biomarker.slug!r} does not accept "
                f"component[] (set supports_multi_state=True for panel-style "
                f"observations)"
            )
        if value_codeable_concept is None:
            raise InvalidObservationValue(
                f"STATE biomarker {biomarker.slug!r} requires valueCodeableConcept"
            )
        _check_state_membership(
            biomarker.slug, value_codeable_concept, allowed
        )


def validate_observation_payload(
    biomarker: Optional[BiomarkerDefinition], payload: Dict[str, Any]
) -> None:
    """Convenience wrapper: extract value[x] from an ORM-shape dict and validate.

    Handles both snake_case (``value_codeable_concept`` — ORM/REST create) and
    camelCase (``valueCodeableConcept`` — FHIR import) keys, plus the
    ``component`` list. Use this from call sites that already have a payload
    dict; prefer :func:`validate_observation_value` for new explicit call sites.
    """
    if biomarker is None:
        return
    value_cc = payload.get("value_codeable_concept")
    if value_cc is None:
        value_cc = payload.get("valueCodeableConcept")
    component = payload.get("component")
    validate_observation_value(
        biomarker,
        value_quantity=payload.get("value_quantity"),
        value_string=payload.get("value_string"),
        value_codeable_concept=value_cc,
        component=component,
    )
