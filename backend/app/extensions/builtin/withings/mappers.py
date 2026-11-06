"""Inbound Withings adapters for the ``withings`` connector.

Measure-group → FHIR Observation adapters that ride the existing mapping
layer: the output is a plain FHIR R4 Observation dict which converts
through :func:`app.extensions.sdk.fhir.fhir_observation_to_create` — the
same entry the FHIR Client connector uses — so the ingest funnel owns the
biomarker mapping, the telemetry split, canonical units and device
registration exactly as it does for every other connector.

Mapping policy (closed, documented):

- only the measure types listed in :data:`MEASURE_TYPES` are mapped —
  each carries its LOINC code and UCUM unit, and the ingest funnel
  resolves the catalog ``BiomarkerDefinition`` from the LOINC code (the
  ``code.text`` display seeds the auto-create path when the catalog has
  no definition yet);
- a measure of an **unmapped** type is still emitted (Withings system
  code, no text) so the funnel stores it raw and records the mapping gap
  — never dropped silently;
- height (type 4) is a static anthropometric, not a time series — it is
  excluded deliberately and counted, not gap-recorded on every group;
- Withings scales values as ``value * 10**unit`` (weight ``7950`` with
  ``unit=-2`` is ``79.5 kg``) — the adapter unscales before emitting.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

#: A single inbound measure group larger than this is treated as hostile
#: and rejected before any mapping work (same bound as the FHIR client).
MAX_MEASUREGRP_JSON_CHARS = 400_000

WITHINGS_CODE_SYSTEM = "https://wbsapi.withings.net/measure-types"
LOINC_SYSTEM = "http://loinc.org"

#: Withings measure type → (LOINC code, display, UCUM unit).
MEASURE_TYPES: dict[int, tuple[str, str, str]] = {
    1: ("29463-7", "Body weight", "kg"),
    6: ("41982-0", "Body fat percentage", "%"),
    9: ("8462-4", "Diastolic blood pressure", "mmHg"),
    10: ("8480-6", "Systolic blood pressure", "mmHg"),
    11: ("8867-4", "Heart rate", "bpm"),
    12: ("8310-5", "Body temperature", "°C"),
    54: ("59408-5", "Oxygen saturation", "%"),
    71: ("8310-5", "Body temperature", "°C"),
}

#: Static anthropometrics excluded on purpose (counted, never gap-recorded).
EXCLUDED_MEASURE_TYPES = frozenset({4})


def measuregrp_is_hostile(grp: Any) -> str | None:
    """Typed rejection reason for a measure group the connector refuses to
    map: ``None`` acceptable, ``"wrong_type"`` when the entry is not a JSON
    object, ``"oversized"`` when its serialized form exceeds
    :data:`MAX_MEASUREGRP_JSON_CHARS`."""
    if not isinstance(grp, dict):
        return "wrong_type"
    if len(json.dumps(grp, default=str)) > MAX_MEASUREGRP_JSON_CHARS:
        return "oversized"
    return None


def _group_datetime(grp: dict[str, Any]) -> datetime | None:
    """The measurement instant: the group's ``date`` epoch (UTC)."""
    date = grp.get("date")
    if not isinstance(date, (int, float)) or isinstance(date, bool):
        return None
    try:
        return datetime.fromtimestamp(int(date), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _unscaled_value(measure: dict[str, Any]) -> float | None:
    """``value * 10**unit`` with strict typing — a non-numeric value is a
    rejected measure, never a best-effort coercion."""
    value = measure.get("value")
    unit = measure.get("unit", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(unit, bool) or not isinstance(unit, (int, float)):
        return None
    return float(value) * (10.0 ** int(unit))


def measuregrp_to_observations(
    grp: dict[str, Any],
    *,
    patient_id: Any,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """One Withings measure group → FHIR Observation dicts + typed counters.

    Returns ``(observations, counters)``. Counters (all zero-initialized on
    a clean group): ``excluded_height``, ``unmapped``, ``bad_measures``. A
    group without a parseable date yields no observations (the caller sees
    an empty list) — time-attribution is not fabricatable.
    """
    counters = {"excluded_height": 0, "unmapped": 0, "bad_measures": 0}
    effective = _group_datetime(grp)
    if effective is None:
        counters["bad_measures"] += 1
        return [], counters
    measures = grp.get("measures")
    if not isinstance(measures, list):
        counters["bad_measures"] += 1
        return [], counters

    out: list[dict[str, Any]] = []
    for measure in measures:
        if not isinstance(measure, dict):
            counters["bad_measures"] += 1
            continue
        mtype = measure.get("type")
        if isinstance(mtype, bool) or not isinstance(mtype, int):
            counters["bad_measures"] += 1
            continue
        value = _unscaled_value(measure)
        if value is None:
            counters["bad_measures"] += 1
            continue
        if mtype in EXCLUDED_MEASURE_TYPES:
            counters["excluded_height"] += 1
            continue
        mapped = MEASURE_TYPES.get(mtype)
        if mapped is None:
            counters["unmapped"] += 1
            out.append(
                {
                    "resourceType": "Observation",
                    "status": "final",
                    "code": {
                        "coding": [
                            {
                                "system": WITHINGS_CODE_SYSTEM,
                                "code": f"withings-measure-type-{mtype}",
                            }
                        ]
                    },
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "effectiveDateTime": effective.isoformat(),
                    "valueQuantity": {"value": value},
                }
            )
            continue
        loinc, display, unit = mapped
        out.append(
            {
                "resourceType": "Observation",
                "status": "final",
                "code": {
                    "coding": [{"system": LOINC_SYSTEM, "code": loinc, "display": display}],
                    "text": display,
                },
                "subject": {"reference": f"Patient/{patient_id}"},
                "effectiveDateTime": effective.isoformat(),
                "valueQuantity": {
                    "value": value,
                    "unit": unit,
                    "system": "http://unitsofmeasure.org",
                    "code": unit,
                },
            }
        )
    return out, counters


def group_created_epoch(grp: dict[str, Any]) -> int | None:
    """The group's ``created`` epoch — the Withings delta-sync key."""
    created = grp.get("created")
    if isinstance(created, bool) or not isinstance(created, (int, float)):
        return None
    return int(created)


__all__ = [
    "EXCLUDED_MEASURE_TYPES",
    "MAX_MEASUREGRP_JSON_CHARS",
    "MEASURE_TYPES",
    "WITHINGS_CODE_SYSTEM",
    "group_created_epoch",
    "measuregrp_is_hostile",
    "measuregrp_to_observations",
]
