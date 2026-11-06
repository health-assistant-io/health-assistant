"""Canonical unit handling for telemetry ingest and analytics.

One normalization/aliasing table, one conversion table. Telemetry stores the
canonical measurement (``value``/``unit``) plus the untouched source
measurement (``raw_value``/``raw_unit``), so changing a conversion re-derives
the canonical pair from provenance instead of asking devices to re-send.

Conversions are deliberately explicit: a pair is converted only when the
table declares it (temperature, common mass/length units, and glucose's
mass↔molar pair). Anything else keeps the source unit as its canonical unit —
guessing would silently corrupt aggregation.
"""

from __future__ import annotations

import re

_CANONICAL_ALIASES: dict[str, str] = {
    "bpm": "bpm",
    "beats/min": "bpm",
    "{beats}/min": "bpm",
    "count": "count",
    "steps": "count",
    "%": "%",
    "percent": "%",
    "c": "°C",
    "°c": "°C",
    "celsius": "°C",
    "f": "°F",
    "°f": "°F",
    "fahrenheit": "°F",
    "kg": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "km": "km",
    "kilometer": "km",
    "kilometers": "km",
    "mi": "mi",
    "mile": "mi",
    "miles": "mi",
    "m": "m",
    "meter": "m",
    "meters": "m",
    "cm": "cm",
    "centimeter": "cm",
    "centimeters": "cm",
    "in": "in",
    "inch": "in",
    "inches": "in",
    "mg/dl": "mg/dL",
    "mmol/l": "mmol/L",
    "g/dl": "g/dL",
    "g/l": "g/L",
    "µg/dl": "µg/dL",
    "ug/dl": "µg/dL",
    "mcg/dl": "µg/dL",
    "µmol/l": "µmol/L",
    "umol/l": "µmol/L",
    "miu/l": "mIU/L",
    "u/l": "U/L",
    "10^9/l": "10^9/L",
    "10^12/l": "10^12/L",
}

#: (source unit, canonical unit) → (multiplier, offset): ``v * m + o``.
_LINEAR_CONVERSIONS: dict[tuple[str, str], tuple[float, float]] = {
    ("°F", "°C"): (5.0 / 9.0, -32.0 * 5.0 / 9.0),
    ("°C", "°F"): (9.0 / 5.0, 32.0),
    ("lb", "kg"): (0.45359237, 0.0),
    ("kg", "lb"): (1.0 / 0.45359237, 0.0),
    ("mi", "km"): (1.609344, 0.0),
    ("km", "mi"): (1.0 / 1.609344, 0.0),
    ("m", "cm"): (100.0, 0.0),
    ("cm", "m"): (0.01, 0.0),
    ("in", "cm"): (2.54, 0.0),
    ("cm", "in"): (1.0 / 2.54, 0.0),
    ("ft", "m"): (0.3048, 0.0),
    ("m", "ft"): (1.0 / 0.3048, 0.0),
}

#: Glucose mass↔molar conversion (molecular weight dependent, so scoped to the
#: glucose family instead of applied to every mg/dL measurement).
_GLUCOSE_MGDL_PER_MMOLL = 18.0182


def normalize_unit(unit: str | None) -> str:
    """Normalize a unit symbol to its canonical spelling.

    Lower-cases, strips formatting noise (``^``, ``*``, parentheses), then
    resolves the alias table. Unknown symbols come back lower-cased so they
    still compare stably.
    """
    if not unit:
        return ""
    raw = unit.strip()
    lowered = raw.lower()
    if lowered in _CANONICAL_ALIASES:
        return _CANONICAL_ALIASES[lowered]
    compact = re.sub(r"[\s^*()]", "", lowered)
    if compact in _CANONICAL_ALIASES:
        return _CANONICAL_ALIASES[compact]
    if any(x in compact for x in ("109/l", "x109/l", "billion/l")):
        return "10^9/L"
    if any(x in compact for x in ("103/ul", "x103/ul", "103/ml", "x103/ml", "k/ul", "k/mm3")):
        return "10^9/L"
    if any(x in compact for x in ("1012/l", "x1012/l", "trillion/l")):
        return "10^12/L"
    if any(x in compact for x in ("106/ul", "x106/ul", "106/ml", "x106/ml", "m/ul")):
        return "10^12/L"
    if compact in {"g/dl", "gr/dl"}:
        return "g/dL"
    if compact in {"mg/dl", "mgr/dl"}:
        return "mg/dL"
    return compact


def units_are_compatible(unit1: str | None, unit2: str | None) -> bool:
    """Return True when two unit strings normalize to the same symbol."""
    if not unit1 or not unit2:
        return False
    return normalize_unit(unit1) == normalize_unit(unit2)


def _glucose_conversion(source: str, canonical: str) -> tuple[float, float] | None:
    if source == "mg/dL" and canonical == "mmol/L":
        return (1.0 / _GLUCOSE_MGDL_PER_MMOLL, 0.0)
    if source == "mmol/L" and canonical == "mg/dL":
        return (_GLUCOSE_MGDL_PER_MMOLL, 0.0)
    return None


def canonicalize_measurement(
    *,
    slug: str | None,
    value: float,
    unit: str | None,
    preferred_unit: str | None,
) -> tuple[float, str | None]:
    """Return ``(canonical_value, canonical_unit)`` for one ingest row.

    ``preferred_unit`` is the biomarker definition's preferred unit symbol.
    When it is absent, or no declared conversion reaches it, the normalized
    source unit becomes canonical and the value is unchanged.
    """
    source = normalize_unit(unit)
    target = normalize_unit(preferred_unit) if preferred_unit else source
    if not source:
        return value, target or None
    if not target or source == target:
        return value, target or None

    conversion = _LINEAR_CONVERSIONS.get((source, target))
    if conversion is None and slug and slug.lower().startswith("glucose"):
        conversion = _glucose_conversion(source, target)
    if conversion is None:
        return value, source

    multiplier, offset = conversion
    return value * multiplier + offset, target
