"""H2 + H6: multi-component observations + multi-range referenceRange in outbound push.

H2: ``fhir_observation_to_create`` previously returned ``None`` for multi-component
observations (blood pressure, panels) — they have no ``valueQuantity``/``valueString``.
This silently dropped the most common vital (BP).

H6: ``_convert_reference_range`` previously read only ``referenceRange[0]`` and
flattened to ``{min, max}`` — multi-range observations (age-stratified) lost data.
"""
from uuid import uuid4

from integrations.sdk.fhir import fhir_observation_to_create


def _bp_obs():
    """A canonical blood-pressure observation (systolic + diastolic components)."""
    return {
        "resourceType": "Observation",
        "status": "final",
        "code": {
            "coding": [{"system": "http://loinc.org", "code": "85354-9"}],
            "text": "Blood pressure",
        },
        "subject": {"reference": "Patient/REMOTE-1"},
        "effectiveDateTime": "2026-06-01T10:00:00Z",
        "component": [
            {
                "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}], "text": "Systolic"},
                "valueQuantity": {"value": 120, "unit": "mmHg"},
            },
            {
                "code": {"coding": [{"system": "http://loinc.org", "code": "8462-4"}], "text": "Diastolic"},
                "valueQuantity": {"value": 80, "unit": "mmHg"},
            },
        ],
    }


def test_bp_observation_with_component_not_dropped():
    """H2 headline bug: BP observations (component-only, no valueQuantity) survive."""
    created = fhir_observation_to_create(_bp_obs(), tenant_id=uuid4(), patient_id=uuid4())
    assert created is not None
    assert created.component is not None
    assert len(created.component) == 2
    assert created.component[0]["code"]["text"] == "Systolic"


def test_observation_with_value_quantity_and_component_both_emitted():
    """An observation with BOTH valueQuantity and component keeps both."""
    obs = _bp_obs()
    obs["valueQuantity"] = {"value": 1, "unit": "test"}
    created = fhir_observation_to_create(obs, tenant_id=uuid4(), patient_id=uuid4())
    assert created is not None
    assert created.value_quantity is not None
    assert created.component is not None


def test_observation_note_preserved():
    """H2: note[] is now mapped (was dropped)."""
    obs = {
        "resourceType": "Observation", "status": "final",
        "code": {"text": "HR"}, "subject": {"reference": "Patient/x"},
        "valueQuantity": {"value": 72, "unit": "bpm"},
        "note": [{"text": "Measured at rest"}],
    }
    created = fhir_observation_to_create(obs, tenant_id=uuid4(), patient_id=uuid4())
    assert created is not None
    assert created.comment == "Measured at rest"


def test_observation_without_value_or_component_still_returns_none():
    """An observation with no valueQuantity, valueString, or component is still dropped."""
    obs = {
        "resourceType": "Observation", "status": "final",
        "code": {"text": "Unknown"}, "subject": {"reference": "Patient/x"},
    }
    created = fhir_observation_to_create(obs, tenant_id=uuid4(), patient_id=uuid4())
    assert created is None


def test_multi_range_reference_range_preserved():
    """H6: the full referenceRange[] list is preserved (was flattened to [0] only)."""
    ranges = [
        {"low": {"value": 70, "unit": "mg/dL"}, "high": {"value": 99, "unit": "mg/dL"},
         "type": {"coding": [{"code": "normal"}]}, "text": "Adult"},
        {"low": {"value": 60, "unit": "mg/dL"}, "high": {"value": 90, "unit": "mg/dL"},
         "type": {"coding": [{"code": "normal"}]}, "text": "Pediatric"},
    ]
    obs = {
        "resourceType": "Observation", "status": "final",
        "code": {"text": "Glucose"}, "subject": {"reference": "Patient/x"},
        "valueQuantity": {"value": 85, "unit": "mg/dL"},
        "referenceRange": ranges,
    }
    created = fhir_observation_to_create(obs, tenant_id=uuid4(), patient_id=uuid4())
    assert created is not None
    # Both ranges survive (was only [0])
    assert created.reference_range == ranges


def test_reference_range_type_appliesto_text_survive():
    """H6: the full structure of each range is preserved (type, appliesTo, text, units)."""
    ranges = [
        {"low": {"value": 3.5, "system": "http://unitsofmeasure.org", "code": "10*9/L"},
         "high": {"value": 5.0, "system": "http://unitsofmeasure.org", "code": "10*9/L"},
         "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/referencerange-meaning", "code": "normal"}]},
         "appliesTo": [{"coding": [{"code": "male"}]}],
         "text": "Adult male"},
    ]
    obs = {
        "resourceType": "Observation", "status": "final",
        "code": {"text": "WBC"}, "subject": {"reference": "Patient/x"},
        "valueQuantity": {"value": 4.2, "unit": "10*9/L"},
        "referenceRange": ranges,
    }
    created = fhir_observation_to_create(obs, tenant_id=uuid4(), patient_id=uuid4())
    assert created is not None
    assert created.reference_range[0]["text"] == "Adult male"
    assert created.reference_range[0]["appliesTo"] == [{"coding": [{"code": "male"}]}]
