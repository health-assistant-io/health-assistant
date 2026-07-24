"""Unit tests for ``app/services/fhir_extensions.py`` + Patient.extensions
serialization (no DB).

Covers:
-registry sanity (4 supported keys, URLs),
- ``validate_patient_extensions`` accepts/cleans all keys, drops None,
  rejects unsupported keys, rejects malformed values,
- ``to_fhir_extension_list`` builds canonical FHIR shape,
- ``from_fhir_extension_list`` inverts back to local-keyed shape,
- ``Patient.to_fhir_dict()`` surfaces the canonical ``extension[]`` array
  and ``assert_valid_fhir`` accepts it (round-trip).
"""
import uuid

import pytest

from app.models.enums import Gender
from app.models.fhir.patient import Patient
from app.services.fhir_extensions import (
    INSURANCE_PROVIDER_URL,
    PREFERRED_LANGUAGE_URL,
    SUPPORTED_PATIENT_EXTENSIONS,
    US_CORE_ETHNICITY_URL,
    US_CORE_RACE_URL,
    from_fhir_extension_list,
    supported_patient_keys,
    to_fhir_extension_list,
    validate_patient_extensions,
)


def test_registry_lists_four_supported_extensions():
    keys = supported_patient_keys()
    assert set(keys) == {
        "race",
        "ethnicity",
        "preferred_language",
        "insurance_provider",
    }
    urls = {e.url for e in SUPPORTED_PATIENT_EXTENSIONS}
    assert US_CORE_RACE_URL in urls
    assert US_CORE_ETHNICITY_URL in urls
    assert PREFERRED_LANGUAGE_URL in urls
    assert INSURANCE_PROVIDER_URL in urls


def test_validate_returns_none_for_none():
    assert validate_patient_extensions(None) is None


def test_validate_returns_none_for_empty_after_dropping():
    assert validate_patient_extensions({"race": None}) is None


def test_validate_race_with_only_text_accepted():
    cleaned = validate_patient_extensions({"race": {"text": "Black"}})
    assert cleaned == {"race": {"text": "Black"}}


def test_validate_race_with_omb_category_normalized():
    cleaned = validate_patient_extensions(
        {"race": {"ombCategory": {"code": "2054-5", "display": "Black"}}}
    )
    assert cleaned["race"]["ombCategory"]["system"] is not None
    assert cleaned["race"]["ombCategory"]["code"] == "2054-5"
    assert cleaned["race"]["ombCategory"]["display"] == "Black"


def test_validate_rejects_unsupported_key():
    with pytest.raises(ValueError):
        validate_patient_extensions({"not_a_real_extension": "x"})


def test_validate_rejects_non_dict_race():
    with pytest.raises(ValueError):
        validate_patient_extensions({"race": "asian"})


def test_validate_rejects_empty_race():
    with pytest.raises(ValueError):
        validate_patient_extensions({"race": {}})


def test_validate_preferred_language_normalizes():
    assert validate_patient_extensions({"preferred_language": "EL"}) == {
        "preferred_language": "el"
    }


def test_validate_rejects_preferred_language_non_string():
    with pytest.raises(ValueError):
        validate_patient_extensions({"preferred_language": 5})


def test_validate_rejects_blank_insurance_provider():
    with pytest.raises(ValueError):
        validate_patient_extensions({"insurance_provider": "   "})


def test_to_fhir_extension_list_builds_canonical_shape():
    cleaned = validate_patient_extensions(
        {
            "race": {
                "ombCategory": {"code": "2054-5", "display": "Black"},
                "text": "Black",
            },
            "preferred_language": "el",
            "insurance_provider": "Acme Health",
        }
    )
    out = to_fhir_extension_list(cleaned)
    by_url = {entry["url"]: entry for entry in out}
    assert US_CORE_RACE_URL in by_url
    sub = by_url[US_CORE_RACE_URL]["extension"]
    sub_by_url = {s["url"]: s for s in sub}
    assert sub_by_url["ombCategory"]["valueCoding"]["code"] == "2054-5"
    assert sub_by_url["text"]["valueString"] == "Black"
    assert by_url[PREFERRED_LANGUAGE_URL]["valueCode"] == "el"
    assert by_url[INSURANCE_PROVIDER_URL]["valueString"] == "Acme Health"


def test_to_fhir_extension_list_empty_returns_empty_list():
    assert to_fhir_extension_list(None) == []
    assert to_fhir_extension_list({}) == []


def test_from_fhir_extension_list_round_trip():
    original = validate_patient_extensions(
        {
            "race": {
                "ombCategory": {"code": "2054-5", "display": "Black"},
                "text": "Black",
            },
            "ethnicity": {"text": "Not Hispanic or Latino"},
            "preferred_language": "el",
            "insurance_provider": "Acme Health",
        }
    )
    canonical = to_fhir_extension_list(original)
    parsed = from_fhir_extension_list(canonical)
    assert parsed["race"]["ombCategory"]["code"] == "2054-5"
    assert parsed["race"]["text"] == "Black"
    assert parsed["ethnicity"]["text"] == "Not Hispanic or Latino"
    assert parsed["preferred_language"] == "el"
    assert parsed["insurance_provider"] == "Acme Health"


def test_from_fhir_extension_list_ignores_unknown_urls():
    parsed = from_fhir_extension_list(
        [
            {"url": "urn:unsupported", "valueString": "x"},
            {"url": PREFERRED_LANGUAGE_URL, "valueCode": "el"},
        ]
    )
    assert parsed == {"preferred_language": "el"}


# ---------- Patient.to_fhir_dict() integration with extensions ----------


def _patient(extensions=None):
    return Patient(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name=[{"given": ["A"], "family": "B"}],
        gender=Gender.MALE,
        extensions=extensions,
    )


def test_to_fhir_dict_empty_extension_list_when_none():
    fhir = _patient().to_fhir_dict()
    assert fhir["extension"] == []


def test_to_fhir_dict_surfaces_canonical_extension_list():
    cleaned = validate_patient_extensions(
        {
            "race": {
                "ombCategory": {"code": "2054-5", "display": "Black"},
                "text": "Black",
            },
            "preferred_language": "el",
        }
    )
    fhir = _patient(extensions=cleaned).to_fhir_dict()
    urls = [e["url"] for e in fhir["extension"]]
    assert US_CORE_RACE_URL in urls
    assert PREFERRED_LANGUAGE_URL in urls


def test_assert_valid_fhir_accepts_patient_with_extensions():
    """The write-time gate must accept a Patient carrying the canonical
    extension[] array (US Core complex extension + simple ``valueCode``)."""
    from app.services.fhir_helpers import assert_valid_fhir

    cleaned = validate_patient_extensions(
        {
            "race": {
                "ombCategory": {"code": "2054-5", "display": "Black"},
                "text": "Black",
            },
            "ethnicity": {"text": "Not Hispanic or Latino"},
            "preferred_language": "el",
            "insurance_provider": "Acme Health",
        }
    )
    patient = _patient(extensions=cleaned)
    fhir = assert_valid_fhir(patient)
    assert fhir["resourceType"] == "Patient"
    assert len(fhir["extension"]) == 4


def test_to_dict_includes_extensions_field():
    cleaned = validate_patient_extensions({"preferred_language": "el"})
    p = _patient(extensions=cleaned)
    out = p.to_dict()
    assert out["extensions"] == cleaned
    assert out["extensions"]["preferred_language"] == "el"