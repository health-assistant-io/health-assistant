"""I6: Observation.interpretation round-trip preserves the canonical FHIR list shape.

Previously the import path collapsed the FHIR CodeableConcept list to a single
display string, losing the LOINC/OBSINT coding on re-export. These tests verify
the list shape survives: import → ORM → to_fhir_dict → validator.
"""
from uuid import uuid4

import pytest

from app.models.fhir.patient import Observation
from app.services.fhir_helpers import _flatten_interpretation, _normalize_interpretation


# ---------- _normalize_interpretation helper ----------

def test_normalize_none_returns_none():
    assert _normalize_interpretation(None) is None


def test_normalize_string_wraps_in_list():
    assert _normalize_interpretation("High") == [{"text": "High"}]


def test_normalize_empty_string_returns_none():
    assert _normalize_interpretation("") is None
    assert _normalize_interpretation("   ") is None


def test_normalize_list_passthrough():
    canonical = [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": "H", "display": "High"}]}]
    assert _normalize_interpretation(canonical) is canonical


def test_normalize_empty_list_returns_none():
    assert _normalize_interpretation([]) is None


# ---------- _flatten_interpretation (read-side helper) ----------

def test_flatten_extracts_display_from_coding():
    interp = [{"coding": [{"system": "...", "code": "H", "display": "High"}]}]
    assert _flatten_interpretation(interp) == "High"


def test_flatten_falls_back_to_code_when_no_display():
    interp = [{"coding": [{"code": "H"}]}]
    assert _flatten_interpretation(interp) == "H"


def test_flatten_falls_back_to_text():
    interp = [{"text": "Borderline"}]
    assert _flatten_interpretation(interp) == "Borderline"


def test_flatten_none_returns_none():
    assert _flatten_interpretation(None) is None


def test_flatten_empty_list_returns_none():
    assert _flatten_interpretation([]) is None


def test_flatten_string_passthrough():
    assert _flatten_interpretation("High") == "High"


# ---------- ORM round-trip ----------

def test_observation_to_fhir_dict_preserves_canonical_coding():
    """The headline I6 bug: a canonical coding list survives the ORM round-trip."""
    canonical = [
        {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                    "code": "H",
                    "display": "High",
                }
            ],
            "text": "High",
        }
    ]
    obs = Observation(
        id=uuid4(),
        status="final",
        code={"text": "Glucose"},
        subject={"reference": "Patient/test"},
        interpretation=canonical,
    )
    fhir_dict = obs.to_fhir_dict()
    assert fhir_dict["interpretation"] == canonical


def test_observation_to_fhir_dict_normalizes_legacy_string():
    """A bare string (in-memory construction or pre-migration data) is normalized on read."""
    obs = Observation(
        id=uuid4(),
        status="final",
        code={"text": "Glucose"},
        subject={"reference": "Patient/test"},
        interpretation="High",
    )
    fhir_dict = obs.to_fhir_dict()
    assert fhir_dict["interpretation"] == [{"text": "High"}]


def test_observation_to_dict_flattens_for_frontend():
    """to_dict() (frontend projection) returns the display string, not the list."""
    obs = Observation(
        id=uuid4(),
        status="final",
        code={"text": "Glucose"},
        subject={"reference": "Patient/test"},
        interpretation=[{"coding": [{"display": "High"}]}],
    )
    d = obs.to_dict()
    assert d["interpretation"] == "High"


def test_observation_to_dict_none_interpretation():
    obs = Observation(
        id=uuid4(),
        status="final",
        code={"text": "Glucose"},
        subject={"reference": "Patient/test"},
        interpretation=None,
    )
    assert obs.to_dict()["interpretation"] is None
