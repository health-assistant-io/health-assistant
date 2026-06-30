"""I6/7: Observation.component round-trip (prereq for H2 push).

The FHIR R4 ``Observation.component`` (``0..*``) stores multi-component
observations like blood pressure (systolic + diastolic) and panels (BMP, CBC).
Without an ORM column, the push path (H2) cannot emit ``component[]`` even
after the SDK mapper is fixed, because push reads ``obs.to_fhir_dict()``.
"""
from uuid import uuid4

from app.models.fhir.patient import Observation


def test_observation_with_component_round_trips_via_orm():
    """A multi-component BP observation round-trips through the ORM + to_fhir_dict."""
    bp_component = [
        {
            "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}], "text": "Systolic"},
            "valueQuantity": {"value": 120, "unit": "mmHg"},
        },
        {
            "code": {"coding": [{"system": "http://loinc.org", "code": "8462-4"}], "text": "Diastolic"},
            "valueQuantity": {"value": 80, "unit": "mmHg"},
        },
    ]
    obs = Observation(
        id=uuid4(),
        status="final",
        code={"coding": [{"system": "http://loinc.org", "code": "85354-9"}], "text": "Blood pressure"},
        subject={"reference": "Patient/test"},
        component=bp_component,
    )
    fhir_dict = obs.to_fhir_dict()
    assert fhir_dict["component"] == bp_component
    # The FHIR validator accepted it (to_fhir_dict raises on invalid)


def test_observation_component_null_when_not_set():
    obs = Observation(
        id=uuid4(),
        status="final",
        code={"text": "Glucose"},
        subject={"reference": "Patient/test"},
    )
    fhir_dict = obs.to_fhir_dict()
    # component is omitted (None) — the validator strips it
    assert fhir_dict.get("component") is None


def test_observation_to_dict_emits_component():
    """The frontend projection (to_dict) also carries component."""
    component = [{"code": {"text": "Systolic"}, "valueQuantity": {"value": 120}}]
    obs = Observation(
        id=uuid4(),
        status="final",
        code={"text": "BP"},
        subject={"reference": "Patient/test"},
        component=component,
    )
    d = obs.to_dict()
    assert d["component"] == component


def test_import_bundle_preserves_component():
    """The fhir_to_observation_orm converter passes component through."""
    from app.services.fhir_converter import fhir_to_observation_orm

    component = [{"code": {"coding": [{"code": "8480-6"}]}, "valueQuantity": {"value": 120}}]
    out = fhir_to_observation_orm({
        "resourceType": "Observation",
        "status": "final",
        "code": {"text": "BP"},
        "subject": {"reference": "Patient/x"},
        "component": component,
    })
    assert out["component"] == component
