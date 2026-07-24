"""Tests for the Phase 3.4 ObservationBuilder hardening:

* ``effective_datetime`` is **required** — ``build()`` raises if unset (was a
  silent "now" default, a data-correctness bug).
* ``reset()`` clears conditionally-set fields so a reused builder doesn't leak
  a previous record's reference range / interpretation into the next.
* ``set_reference_range(low=None, high=None)`` clears the range (was setting
  ``{}`` and tripping the truthiness branch).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from integrations.sdk.observation_builder import ObservationBuilder

TENANT = uuid4()
PATIENT = uuid4()


def _a_tz() -> datetime:
    return datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


def _base() -> ObservationBuilder:
    return ObservationBuilder(TENANT, PATIENT)


# ---------------------------------------------------------------------------
# effective_datetime is now required
# ---------------------------------------------------------------------------


def test_build_raises_when_effective_date_unset():
    """A clinical observation with no timestamp is a data-correctness bug."""
    b = _base().set_biomarker("8867-4", "Heart rate").set_value(72.0, "bpm")
    with pytest.raises(ValueError, match="effective_datetime is required"):
        b.build()


def test_build_succeeds_when_effective_date_set():
    obs = (
        _base()
        .set_biomarker("8867-4", "Heart rate")
        .set_value(72.0, "bpm")
        .set_effective_date(_a_tz())
        .build()
    )
    assert obs.effective_datetime == _a_tz()


# ---------------------------------------------------------------------------
# reset() prevents state leakage across reused builders
# ---------------------------------------------------------------------------


def test_reset_clears_conditionally_set_fields():
    """The original bug: record 1 sets a reference range; record 2 omits it
    but inherits record 1's range because the builder is mutated in place."""
    b = _base()
    first = (
        b.set_biomarker("8867-4", "Heart rate")
        .set_value(72.0, "bpm")
        .set_reference_range(low=60.0, high=100.0)
        .set_effective_date(_a_tz())
        .build()
    )
    assert first.lab_reference_range == {"low": 60.0, "high": 100.0}

    b.reset()
    second = (
        b.set_biomarker("2345-7", "Glucose")
        .set_value(5.5, "mmol/L")
        .set_effective_date(_a_tz())
        .build()
    )
    # Without reset, second would have inherited {"low": 60.0, "high": 100.0}.
    assert second.lab_reference_range is None


def test_reset_clears_interpretation():
    b = _base()
    first = (
        b.set_biomarker("c", "n")
        .set_value(1.0, "u")
        .set_interpretation("high")
        .set_effective_date(_a_tz())
        .build()
    )
    assert first.interpretation == "high"

    b.reset()
    second = (
        b.set_biomarker("c2", "n2")
        .set_value(2.0, "u")
        .set_effective_date(_a_tz())
        .build()
    )
    assert second.interpretation is None


def test_reset_keeps_tenant_and_patient():
    b = _base()
    b.reset()
    assert b.tenant_id == TENANT
    assert b.patient_id == PATIENT


# ---------------------------------------------------------------------------
# set_reference_range both-None clears the range
# ---------------------------------------------------------------------------


def test_set_reference_range_both_none_clears_range():
    """Previously set_reference_range(low=None, high=None) set ``{}`` which
    tripped the truthiness branch in build() and produced relative_score=0.5
    for a record that intended to have no range."""
    b = _base()
    b.set_reference_range(low=60.0, high=100.0)
    b.set_reference_range(low=None, high=None)
    assert b._reference_range is None


def test_set_reference_range_one_bound_kept():
    b = _base().set_reference_range(low=60.0)
    assert b._reference_range == {"low": 60.0}
