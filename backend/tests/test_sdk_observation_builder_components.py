"""Tests for the ObservationBuilder structural setters (Phase 4.1).

The inbound FHIR converter (``fhir_observation_to_create``) already preserves
``component[]``, ``note``, ``performer``, and ``category``, but the outbound
builder had no setters for any of them — a wearable pulling blood pressure
from a non-FHIR source couldn't construct a proper systolic/diastolic
observation. These tests pin the new setters and the value[x]/component
mutual exclusion (FHIR R4 §3.1.1).
"""
import datetime

from integrations.sdk.observation_builder import ObservationBuilder
from app.models.enums import CodingSystem

TENANT = "00000000-0000-0000-0000-000000000001"
PATIENT = "00000000-0000-0000-0000-000000000002"
NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


def _builder() -> ObservationBuilder:
    return ObservationBuilder(tenant_id=TENANT, patient_id=PATIENT)


# ---------------------------------------------------------------------------
# Blood pressure — the canonical multi-component case
# ---------------------------------------------------------------------------


def test_blood_pressure_two_components_no_parent_value():
    """A BP observation has systolic + diastolic under one panel code, with
    NO parent value[x] (FHIR R4 §3.1.1)."""
    obs = (
        _builder()
        .set_biomarker("85354-9", "Blood pressure panel")
        .add_component("8480-6", "Systolic", 120, "mmHg")
        .add_component("8462-4", "Diastolic", 80, "mmHg")
        .set_effective_date(NOW)
        .build()
    )
    assert obs.component is not None and len(obs.component) == 2
    systolic = obs.component[0]
    assert systolic["code"]["coding"][0]["code"] == "8480-6"
    assert systolic["valueQuantity"]["value"] == 120
    assert systolic["valueQuantity"]["unit"] == "mmHg"
    # Parent has no value[x] — the panel itself carries no scalar.
    assert obs.value_quantity is None
    assert obs.value_string is None
    assert obs.value_codeable_concept is None
    assert obs.raw_value is None


def test_component_uses_specified_coding_system():
    """A SNOMED-coded component round-trips its system URL."""
    obs = (
        _builder()
        .set_biomarker("85354-9", "BP")
        .add_component("8480-6", "Systolic", 120, "mmHg",
                       coding_system=CodingSystem.SNOMED)
        .set_effective_date(NOW)
        .build()
    )
    assert obs.component[0]["code"]["coding"][0]["system"] == "http://snomed.info/sct"


def test_adding_component_clears_parent_value():
    """Setting a value then adding a component drops the parent value[x]
    (component observations have no parent value)."""
    obs = (
        _builder()
        .set_biomarker("8867-4", "Heart rate")
        .set_value(72, "bpm")
        .add_component("8480-6", "Systolic", 120, "mmHg")
        .set_effective_date(NOW)
        .build()
    )
    assert obs.value_quantity is None
    assert obs.component is not None and len(obs.component) == 1


def test_setting_value_clears_components():
    """The reverse: adding components then setting a value drops them."""
    obs = (
        _builder()
        .set_biomarker("8867-4", "Heart rate")
        .add_component("8480-6", "Systolic", 120, "mmHg")
        .set_value(72, "bpm")
        .set_effective_date(NOW)
        .build()
    )
    assert obs.component is None
    assert obs.value_quantity is not None
    assert obs.value_quantity["value"] == 72


# ---------------------------------------------------------------------------
# category / performer / note
# ---------------------------------------------------------------------------


def test_add_category_single():
    obs = (
        _builder()
        .set_biomarker("8867-4", "Heart rate")
        .set_value(72, "bpm")
        .add_category("vital-signs",
                      system="http://terminology.hl7.org/CodeSystem/observation-category")
        .set_effective_date(NOW)
        .build()
    )
    assert obs.category is not None and len(obs.category) == 1
    assert obs.category[0]["coding"][0]["code"] == "vital-signs"


def test_add_category_multiple():
    """Multiple categories accumulate (BP is vital-signs + can be grouped)."""
    obs = (
        _builder()
        .set_biomarker("85354-9", "BP")
        .add_component("8480-6", "Systolic", 120, "mmHg")
        .add_category("vital-signs")
        .add_category("clinic-portal", display="Clinic Portal")
        .set_effective_date(NOW)
        .build()
    )
    assert obs.category is not None and len(obs.category) == 2


def test_set_performer():
    obs = (
        _builder()
        .set_biomarker("8867-4", "Heart rate")
        .set_value(72, "bpm")
        .set_performer("Acme Lab", reference="Organization/abc",
                       performer_type="Organization")
        .set_effective_date(NOW)
        .build()
    )
    assert obs.performer is not None and len(obs.performer) == 1
    assert obs.performer[0]["display"] == "Acme Lab"
    assert obs.performer[0]["reference"] == "Organization/abc"
    assert obs.performer[0]["type"] == "Organization"


def test_add_note_joins_multiple():
    obs = (
        _builder()
        .set_biomarker("8867-4", "Heart rate")
        .set_value(72, "bpm")
        .add_note("Reading taken at rest.")
        .add_note("Patient reported caffeine intake.")
        .set_effective_date(NOW)
        .build()
    )
    assert obs.comment == "Reading taken at rest. Patient reported caffeine intake."


def test_add_note_single():
    obs = (
        _builder()
        .set_biomarker("8867-4", "Heart rate")
        .set_value(72, "bpm")
        .add_note("Resting reading.")
        .set_effective_date(NOW)
        .build()
    )
    assert obs.comment == "Resting reading."


# ---------------------------------------------------------------------------
# reset() clears the new fields
# ---------------------------------------------------------------------------


def test_reset_clears_structural_fields():
    """A reused builder must not leak components/performer/category/comment
    from the previous record into the next."""
    b = _builder()
    (
        b.set_biomarker("85354-9", "BP")
        .add_component("8480-6", "Systolic", 120, "mmHg")
        .add_category("vital-signs")
        .set_performer("Acme")
        .add_note("note one")
        .set_effective_date(NOW)
        .build()
    )
    b.reset()
    obs = (
        b.set_biomarker("8867-4", "Heart rate")
        .set_value(72, "bpm")
        .set_effective_date(NOW)
        .build()
    )
    assert obs.component is None
    assert obs.performer is None
    assert obs.category is None
    assert obs.comment is None


def test_existing_scalar_build_unchanged():
    """A plain quantitative observation still builds with no structural
    fields set (regression: the new setters must not perturb the legacy path)."""
    obs = (
        _builder()
        .set_biomarker("8867-4", "Heart rate")
        .set_value(72, "bpm", "{beats}/min")
        .set_effective_date(NOW)
        .build()
    )
    assert obs.value_quantity["value"] == 72
    assert obs.component is None
    assert obs.performer is None
    assert obs.category is None
    assert obs.comment is None
