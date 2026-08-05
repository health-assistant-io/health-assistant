"""Table-driven tests for the Observation value-shape validator
(plan Step 5).

Covers every (value_type, supports_multi_state, payload) combination from
Decision §5's contract matrix — both pass and fail cases. Pure unit tests:
no DB, no async — the validator takes a plain ``BiomarkerDefinition`` and
plain value[x] kwargs.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.observation_value_validator import (
    InvalidObservationValue,
    validate_observation_value,
)


# ---------------------------------------------------------------------------
# Fixtures: lightweight BiomarkerDefinition doubles
# ---------------------------------------------------------------------------


def _state(slug, code, system):
    """A BiomarkerState double."""
    return SimpleNamespace(code=code, system=system, display=slug)


def _allowed(state, is_normal=False, sort_order=0):
    """A BiomarkerAllowedState double (join row pointing at a state)."""
    return SimpleNamespace(state=state, is_normal=is_normal, sort_order=sort_order)


def quantity_bio(**kw):
    """A QUANTITY BiomarkerDefinition double."""
    base = dict(slug="glucose", value_type="quantity", supports_multi_state=False,
                allowed_states=[])
    base.update(kw)
    return SimpleNamespace(**base)


def state_bio(allowed, **kw):
    """A STATE BiomarkerDefinition double with the given allowed states."""
    base = dict(slug="sars-cov-2-pcr", value_type="state",
                supports_multi_state=False, allowed_states=allowed)
    base.update(kw)
    return SimpleNamespace(**base)


# Two-state catalog: POS (abnormal) + NEG (normal)
POS_NEG = [
    _allowed(_state("positive", "POS", "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"), is_normal=False),
    _allowed(_state("negative", "NEG", "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"), is_normal=True),
]
POS_NEG_PAIRS = {("POS", "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"),
                 ("NEG", "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation")}

V3 = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"


def cc(code, system=V3, display=None):
    """A FHIR CodeableConcept shape."""
    coding = [{"code": code, "system": system}]
    if display:
        coding[0]["display"] = display
    return {"coding": coding}


def component(code, value_cc):
    """A FHIR Observation.component entry."""
    return {"code": {"coding": [{"code": code}]}, "valueCodeableConcept": value_cc}


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------


def test_no_biomarker_is_noop():
    """When biomarker is None (auto-create path), nothing is enforced."""
    validate_observation_value(None, value_quantity={"value": 1, "unit": "mg/dL"})
    validate_observation_value(None, value_codeable_concept=cc("POS"))
    # No exception raised.


# ---------------------------------------------------------------------------
# QUANTITY biomarker
# ---------------------------------------------------------------------------


def test_quantity_with_value_quantity_passes():
    validate_observation_value(quantity_bio(), value_quantity={"value": 5.5, "unit": "mmol/L"})


def test_quantity_with_value_string_passes():
    """Free-text results on a QUANTITY biomarker (legacy untyped) tolerated."""
    validate_observation_value(quantity_bio(), value_string="sample hemolyzed")


def test_quantity_with_value_codeable_concept_rejected():
    """A QUANTITY biomarker with a top-level valueCodeableConcept is a misroute."""
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(quantity_bio(), value_codeable_concept=cc("POS"))
    assert "QUANTITY" in str(exc.value)


# ---------------------------------------------------------------------------
# STATE biomarker — single-state
# ---------------------------------------------------------------------------


def test_state_single_with_matching_code_passes():
    validate_observation_value(state_bio(POS_NEG), value_codeable_concept=cc("POS"))
    validate_observation_value(state_bio(POS_NEG), value_codeable_concept=cc("NEG"))


def test_state_single_with_non_matching_code_rejected():
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(
            state_bio(POS_NEG),
            value_codeable_concept=cc("WITHIN_LIMITS", system="urn:uuid:health-assistant:custom-state"),
        )
    assert "allowed_states" in str(exc.value)


def test_state_single_missing_value_codeable_concept_rejected():
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(state_bio(POS_NEG))
    assert "valueCodeableConcept" in str(exc.value)


def test_state_single_with_value_quantity_rejected():
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(
            state_bio(POS_NEG), value_quantity={"value": 1.0}
        )
    assert "value_quantity" in str(exc.value)


def test_state_single_with_value_string_rejected():
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(state_bio(POS_NEG), value_string="Positive")
    assert "value_string" in str(exc.value)


def test_state_single_with_component_rejected():
    """Single-state biomarkers reject component[] (use supports_multi_state=True)."""
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(
            state_bio(POS_NEG),
            component=[component("org1", cc("POS")), component("org2", cc("NEG"))],
        )
    assert "supports_multi_state" in str(exc.value) or "component" in str(exc.value)


def test_state_value_cc_missing_coding_rejected():
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(
            state_bio(POS_NEG),
            value_codeable_concept={"text": "Positive"},  # no coding[]
        )
    assert "missing" in str(exc.value).lower() or "malformed" in str(exc.value).lower()


def test_state_value_cc_coding_missing_system_rejected():
    """A coding without ``system`` is ambiguous — reject."""
    with pytest.raises(InvalidObservationValue):
        validate_observation_value(
            state_bio(POS_NEG),
            value_codeable_concept={"coding": [{"code": "POS"}]},  # no system
        )


def test_state_no_allowed_states_configured_rejected():
    """Defensive: a STATE biomarker with empty allowed_states (should be
    unreachable via Pydantic but possible via direct DB edit) is rejected."""
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(
            state_bio([]),
            value_codeable_concept=cc("POS"),
        )
    assert "allowed_states" in str(exc.value)


# ---------------------------------------------------------------------------
# STATE biomarker — multi-state (component[])
# ---------------------------------------------------------------------------


def test_state_multi_with_valid_components_passes():
    bio = state_bio(POS_NEG, supports_multi_state=True)
    validate_observation_value(
        bio,
        component=[
            component("staph-aureus", cc("POS")),
            component("e-coli", cc("NEG")),
        ],
    )


def test_state_multi_with_single_component_rejected():
    """Multi-state requires >=2 component entries."""
    bio = state_bio(POS_NEG, supports_multi_state=True)
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(bio, component=[component("org1", cc("POS"))])
    assert ">=2" in str(exc.value) or "component" in str(exc.value)


def test_state_multi_with_no_components_rejected():
    bio = state_bio(POS_NEG, supports_multi_state=True)
    with pytest.raises(InvalidObservationValue):
        validate_observation_value(bio, component=[])


def test_state_multi_with_top_level_value_cc_rejected():
    """Multi-state biomarkers forbid a top-level valueCodeableConcept — all
    values live under component[]."""
    bio = state_bio(POS_NEG, supports_multi_state=True)
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(bio, value_codeable_concept=cc("POS"))
    assert "component[]" in str(exc.value)


def test_state_multi_component_missing_code_rejected():
    bio = state_bio(POS_NEG, supports_multi_state=True)
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(
            bio,
            component=[
                {"valueCodeableConcept": cc("POS")},  # missing code
                component("org2", cc("NEG")),
            ],
        )
    assert "code" in str(exc.value)


def test_state_multi_component_missing_value_cc_rejected():
    bio = state_bio(POS_NEG, supports_multi_state=True)
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(
            bio,
            component=[
                {"code": {"coding": [{"code": "org1"}]}},  # missing valueCC
                component("org2", cc("NEG")),
            ],
        )
    assert "valueCodeableConcept" in str(exc.value)


def test_state_multi_component_with_non_matching_code_rejected():
    bio = state_bio(POS_NEG, supports_multi_state=True)
    with pytest.raises(InvalidObservationValue) as exc:
        validate_observation_value(
            bio,
            component=[
                component("org1", cc("POS")),
                component("org2", cc("WITHIN_LIMITS", system="urn:uuid:health-assistant:custom-state")),
            ],
        )
    assert "allowed_states" in str(exc.value)


def test_state_multi_snake_case_value_codeable_concept_supported():
    """The validator tolerates ``value_codeable_concept`` (snake/ORM-shape)
    in addition to ``valueCodeableConcept`` (camelCase FHIR)."""
    bio = state_bio(POS_NEG, supports_multi_state=True)
    component_snake = {
        "code": {"coding": [{"code": "org1"}]},
        "value_codeable_concept": cc("POS"),
    }
    validate_observation_value(
        bio,
        component=[component_snake, component("org2", cc("NEG"))],
    )


# ---------------------------------------------------------------------------
# Cross-system code disambiguation
# ---------------------------------------------------------------------------


def test_same_code_in_different_systems_disambiguated():
    """A code is only valid in its declared code system. ``POS`` in v3-OI is
    valid; ``POS`` in a custom urn is a different concept."""
    allowed = [_allowed(_state("positive", "POS", V3))]
    bio = state_bio(allowed)
    validate_observation_value(bio, value_codeable_concept=cc("POS"))  # OK
    with pytest.raises(InvalidObservationValue):
        validate_observation_value(
            bio, value_codeable_concept=cc("POS", system="urn:uuid:health-assistant:custom-state")
        )
