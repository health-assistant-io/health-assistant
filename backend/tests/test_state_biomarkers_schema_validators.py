"""Pydantic schema tests for the state biomarker discriminator
(plan Step 4).

Exercises every cross-field invariant the schema layer enforces, plus the
valid paths (QUANTITY default + STATE happy-path). DB-level integration is
covered by ``test_state_biomarkers_schema.py`` (model + CHECK constraints).
"""
import pytest
from pydantic import ValidationError

from app.models.enums import BiomarkerValueType
from app.schemas.biomarker import (
    AllowedStateSpec,
    BiomarkerCreate,
    BiomarkerUpdate,
)


def _state(**overrides):
    """Build a minimal valid STATE biomarker create payload."""
    base = {
        "slug": "test-state-bio",
        "name": "Test State Bio",
        "value_type": BiomarkerValueType.STATE,
        "allowed_states": [
            AllowedStateSpec(state_slug="positive", is_normal=False),
            AllowedStateSpec(state_slug="negative", is_normal=True),
        ],
    }
    base.update(overrides)
    return BiomarkerCreate(**base)


def _quantity(**overrides):
    """Build a minimal valid QUANTITY biomarker create payload."""
    base = {"slug": "test-qty-bio", "name": "Test Quantity Bio"}
    base.update(overrides)
    return BiomarkerCreate(**base)


# ----------------------------------------------------------------------------
# Happy paths
# ----------------------------------------------------------------------------


def test_quantity_create_defaults_to_quantity():
    """A biomarker with no value_type is QUANTITY — the legacy shape."""
    b = _quantity()
    assert b.value_type is BiomarkerValueType.QUANTITY
    assert b.supports_multi_state is False
    assert b.allowed_states == []


def test_state_create_happy_path():
    """STATE biomarker with allowed_states validates cleanly."""
    b = _state()
    assert b.value_type is BiomarkerValueType.STATE
    assert len(b.allowed_states) == 2
    # The normal-set flag is preserved per-state.
    normal_slugs = {
        s.state_slug for s in b.allowed_states if s.is_normal
    }
    assert normal_slugs == {"negative"}


def test_state_with_multi_state_flag():
    """supports_multi_state is accepted on STATE biomarkers."""
    b = _state(supports_multi_state=True)
    assert b.supports_multi_state is True


# ----------------------------------------------------------------------------
# STATE invariants (reject)
# ----------------------------------------------------------------------------


def test_state_rejects_telemetry():
    with pytest.raises(ValidationError) as exc:
        _state(is_telemetry=True)
    assert "telemetry" in str(exc.value).lower()


def test_state_rejects_unit_id():
    from uuid import uuid4

    with pytest.raises(ValidationError) as exc:
        _state(preferred_unit_id=uuid4())
    assert "unit" in str(exc.value).lower()


def test_state_rejects_unit_symbol():
    with pytest.raises(ValidationError) as exc:
        _state(preferred_unit_symbol="mg/dL")
    assert "unit" in str(exc.value).lower()


def test_state_rejects_empty_allowed_states():
    with pytest.raises(ValidationError) as exc:
        _state(allowed_states=[])
    assert "allowed_state" in str(exc.value).lower()


def test_state_rejects_numeric_reference_ranges():
    """STATE biomarkers use the is_normal flag on allowed_states — numeric
    reference_range_min/max don't apply."""
    with pytest.raises(ValidationError) as exc:
        _state(reference_range_min=1.0, reference_range_max=2.0)
    assert "reference_range" in str(exc.value).lower()


# ----------------------------------------------------------------------------
# QUANTITY invariants (reject)
# ----------------------------------------------------------------------------


def test_quantity_rejects_allowed_states():
    with pytest.raises(ValidationError) as exc:
        _quantity(allowed_states=[AllowedStateSpec(state_slug="positive")])
    assert "allowed_state" in str(exc.value).lower() or "state" in str(
        exc.value
    ).lower()


def test_quantity_rejects_supports_multi_state():
    with pytest.raises(ValidationError) as exc:
        _quantity(supports_multi_state=True)
    assert "multi_state" in str(exc.value).lower() or "state" in str(
        exc.value
    ).lower()


# ----------------------------------------------------------------------------
# PATCH invariants
# ----------------------------------------------------------------------------


def test_patch_value_type_is_rejected():
    """Flipping value_type is a destructive operation (strands observations);
    rejected on PATCH. Drop + recreate the definition instead."""
    with pytest.raises(ValidationError) as exc:
        BiomarkerUpdate(value_type=BiomarkerValueType.STATE)
    assert "value_type" in str(exc.value).lower()


def test_patch_supports_multi_state_with_telemetry_rejected():
    """A patch setting supports_multi_state=True + is_telemetry=True is
    contradictory."""
    with pytest.raises(ValidationError):
        BiomarkerUpdate(supports_multi_state=True, is_telemetry=True)


def test_patch_allows_unsetting_fields():
    """A no-op PATCH (everything None) is valid."""
    update = BiomarkerUpdate()
    assert update.value_type is None
    assert update.allowed_states is None
