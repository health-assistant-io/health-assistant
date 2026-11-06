# ruff: noqa: E501 -- ternary and long lines; reflow when touched
"""Observation-status helpers (strict whitelist + state mapping)."""

from app.services.analytics.types import (
    Any,
    Observation,
)


async def _get_observation_status(
    name: str,
    val: Any,
    obs: Observation,
    ref_min: float | None = None,
    ref_max: float | None = None,
) -> str:
    """Helper to determine the status of an observation using the new relative_score or interpretation"""
    # 0. STATE biomarker branch (plan state-biomarkers Step 7): categorical
    # values never reach the numeric pipeline. Resolve the valueCodeableConcept
    # against the biomarker's ``allowed_states.is_normal`` set. Multi-state
    # observations (component[]) defer to the first component's value.
    biomarker = getattr(obs, "biomarker", None)
    if biomarker is not None and getattr(biomarker, "value_type", None) == "state":
        return _state_observation_status(biomarker, obs)

    # Status is always recomputed from the value + reference range so the
    # backend stays consistent with the frontend (getFinalStatus /
    # computeStatus). The FHIR ``interpretation`` field is intentionally
    # not consulted — it was an input that the frontend already
    # ignores (and the manual Low/Normal/High toggle has been removed).

    # 1. Use relative score (clamped to [0.0, 1.0] by ObservationBuilder).
    # A strictly-interior score (0 < score < 1) genuinely means the value
    # sits inside the reference range, so Normal is correct there. Boundary
    # values (exactly 0.0 or 1.0) are ambiguous after clamping: the value
    # could be exactly at the bound (still normal) or beyond it (abnormal).
    # Rather than guess, fall through to the explicit reference-range
    # comparison in step 2 which has the raw value + bounds to decide.
    if getattr(obs, "relative_score", None) is not None and 0.0 < obs.relative_score < 1.0:
        return "Normal"
        # score at boundary (0.0 or 1.0) — defer to the range check below.

    # 2. Use provided ranges or fallback to parsing FHIR reference_range
    status = "Normal"
    try:
        num_val = float(val)
        low = ref_min
        high = ref_max

        # If not provided as arguments, try to get from observation
        if low is None and high is None:
            ref_range = obs.reference_range
            if ref_range and isinstance(ref_range, list) and len(ref_range) > 0:
                low = ref_range[0].get("low", {}).get("value")
                high = ref_range[0].get("high", {}).get("value")

        if low is not None and num_val < float(low):
            status = "Low"
        elif high is not None and num_val > float(high):
            status = "High"
        elif low is None and high is None:
            # Fallback to standard ranges for data that wasn't processed with the new engine
            STANDARD_RANGES = {
                "glucose": {"min": 3.9, "max": 5.6},
                "cholesterol": {"min": 0, "max": 5.2},
                "hdl": {"min": 1.0, "max": 999},
                "ldl": {"min": 0, "max": 3.0},
                "hemoglobin": {"min": 120, "max": 180},
                "white_blood_cells": {"min": 4.0, "max": 11.0},
                "platelets": {"min": 150, "max": 450},
                "creatinine": {"min": 60, "max": 110},
                "tsh": {"min": 0.4, "max": 4.0},
                "vitamin_d": {"min": 50, "max": 125},
                "systolic": {"min": 90, "max": 130},
                "diastolic": {"min": 60, "max": 85},
            }

            lower_name = name.lower()
            for key, r in STANDARD_RANGES.items():
                # Flexible name matching for standard ranges
                if (
                    key in lower_name
                    or key.replace("_", " ") in lower_name
                    or key.replace("_", "") in lower_name
                ):
                    if num_val < r["min"]:
                        status = "Low"
                    elif num_val > r["max"]:
                        status = "High"
                    break
    except (ValueError, TypeError):
        pass
    return status


def _state_observation_status(biomarker, obs) -> str:
    """Compute Normal/Abnormal for a STATE biomarker observation.

    Uses the biomarker's ``allowed_states.is_normal`` set as the equivalent
    of a numeric reference range. Returns:
      - ``"Normal"``   — the observation's valueCodeableConcept coding is in
                          the normal set.
      - ``"Abnormal"`` — the coding is in the allowed set but not normal.
      - ``"Unknown"`` — the coding couldn't be resolved (defensive; the
                          write-time validator should make this unreachable).

    For multi-state observations (``component[]``) this returns Abnormal if
    ANY component is non-normal — the conservative choice for a single-badge
    summary.
    """
    from app.services.biomarkers.observation_value_validator import _extract_coding_pair

    normal_pairs = {
        (allowed.state.code, allowed.state.system)
        for allowed in (biomarker.allowed_states or [])
        if allowed.is_normal and allowed.state is not None
    }
    if not normal_pairs:
        # No normal set configured — every value is "Normal" by default
        # (avoids flagging unknown/neutral states as abnormal).
        return "Normal"

    components = getattr(obs, "component", None) or []
    if components:
        # Multi-state: Abnormal if any component is non-normal.
        any_normal = False
        any_abnormal = False
        for comp in components:
            value_cc = (comp.get("valueCodeableConcept") if isinstance(comp, dict) else None) or (
                comp.get("value_codeable_concept") if isinstance(comp, dict) else None
            )
            pair = _extract_coding_pair(value_cc)
            if pair is None:
                continue
            if pair in normal_pairs:
                any_normal = True
            else:
                any_abnormal = True
        if any_abnormal:
            return "Abnormal"
        return "Normal" if any_normal else "Unknown"

    pair = _extract_coding_pair(obs.value_codeable_concept)
    if pair is None:
        return "Unknown"
    return "Normal" if pair in normal_pairs else "Abnormal"
