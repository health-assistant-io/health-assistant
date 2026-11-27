import pytest

from app.services.biomarkers.unit_conversion import (
    canonicalize_measurement,
    normalize_unit,
    units_are_compatible,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10^9/L", "10^9/L"),
        ("x10^9/L", "10^9/L"),
        ("10^3/uL", "10^9/L"),
        ("K/uL", "10^9/L"),
        ("K/mm3", "10^9/L"),
        ("10^6/mL", "10^9/L"),
        ("x10^6/mL", "10^9/L"),
        ("10^12/L", "10^12/L"),
        ("x10^12/L", "10^12/L"),
        ("10^6/uL", "10^12/L"),
        ("M/uL", "10^12/L"),
        ("10^9/mL", "10^12/L"),
        ("x10^9/mL", "10^12/L"),
    ],
)
def test_normalize_hematology_units(raw: str, expected: str) -> None:
    assert normalize_unit(raw) == expected


def test_per_ml_units_not_scaled_by_1000x() -> None:
    assert normalize_unit("10^3/mL") != "10^9/L"
    assert normalize_unit("x10^3/mL") != "10^9/L"
    assert not units_are_compatible("10^3/mL", "10^9/L")

    assert units_are_compatible("10^6/mL", "10^9/L")
    assert not units_are_compatible("10^6/mL", "10^12/L")
    assert units_are_compatible("10^9/mL", "10^12/L")


def test_canonicalize_measurement_does_not_relabel_mismatched_ml_units() -> None:
    val_wbc, unit_wbc = canonicalize_measurement(
        slug="wbc",
        value=5.0,
        unit="10^3/mL",
        preferred_unit="10^9/L",
    )
    assert val_wbc == 5.0
    assert unit_wbc != "10^9/L"

    val_rbc, unit_rbc = canonicalize_measurement(
        slug="rbc",
        value=5.0,
        unit="10^6/mL",
        preferred_unit="10^12/L",
    )
    assert val_rbc == 5.0
    assert unit_rbc == "10^9/L"
