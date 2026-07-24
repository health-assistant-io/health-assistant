"""FHIR R4 extension registry for Patient (and later Doctor/Organization).

The application stores FHIR extensions on entity rows as a **local-keyed**
JSONB map for ORM + frontend convenience::

    {
        "race":                {"ombCategory": {"system": "urn:oid:2.16.840.1.113883.6.18", "code": "2054-5", "display": "Black"}, "text": "Black"},
        "ethnicity":           {"ombCategory": {"system": "urn:oid:2.16.840.1.113883.6.18", "code": "2135-2", "display": "Hispanic or Latino"}, "text": "Hispanic or Latino"},
        "preferred_language":  "el",
        "insurance_provider":  "Acme Health Insurance"
    }

Canonical FHIR R4 shape (used by `to_fhir_dict()` / imports) is a flat
``extension[]`` array of ``{url, value[x], extension[]}`` objects::

    [
        {"url": "urn:oid:2.16.840.1.113883.4.642.40.46|race",
         "extension": [{"url": "ombCategory", "valueCoding": {...}}, {"url": "text", "valueString": "..."}]},
        {"url": "urn:oid:2.16.840.1.113883.4.642.40.46|ethnicity", ...},
        {"url": "urn:oid:1.3.6.1.4.1.19376.1.5.3.1.4.51", "valueCode": "el"},
        {"url": "urn:healthassistant:insurance-provider", "valueString": "Acme Health Insurance"}
    ]

This module is the single authority for the supported extension catalog and
the two-way conversion. Anything not in SUPPORTED_*_EXTENSIONS raises
ValueError on write (defence in depth; not the front-line security gate —
the API schema layer validates first).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

US_CORE_RACE_URL = "urn:oid:2.16.840.1.113883.4.642.40.46|race"
US_CORE_ETHNICITY_URL = "urn:oid:2.16.840.1.113883.4.642.40.46|ethnicity"
PREFERRED_LANGUAGE_URL = "urn:oid:1.3.6.1.4.1.19376.1.5.3.1.4.51"
INSURANCE_PROVIDER_URL = "urn:healthassistant:insurance-provider"

OMB_CATEGORY_SYSTEM = "urn:oid:2.16.840.1.113883.6.18"


def _build_omb_category(value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not value:
        return None
    coding = {}
    code = value.get("ombCategory")
    if code and isinstance(code, dict):
        coding = {
            "system": code.get("system") or OMB_CATEGORY_SYSTEM,
            "code": code.get("code"),
            "display": code.get("display"),
        }
        coding = {k: v for k, v in coding.items() if v is not None}
    if not coding:
        return None
    return {"url": "ombCategory", "valueCoding": coding}


def _parse_complex_codeable(ext_sublist: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not ext_sublist:
        return out
    for sub in ext_sublist:
        url = sub.get("url")
        if url == "ombCategory":
            coding = sub.get("valueCoding") or {}
            out["ombCategory"] = {
                "system": coding.get("system"),
                "code": coding.get("code"),
                "display": coding.get("display"),
            }
        elif url == "text":
            out["text"] = sub.get("valueString")
    return out


def _validate_string(value: Any) -> str:
    if value is None:
        raise ValueError("string extension value must not be None")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("string extension value must be a non-empty string")
    return value.strip()


def _validate_preferred_language(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("preferred_language must be an ISO-639-1 code string")
    code = value.strip().lower()
    if len(code) < 2 or len(code) > 8:
        raise ValueError("preferred_language must be a 2–8 char ISO/BCP-47 code")
    return code


def _build_simple_extension(url: str, fhir_field: str, value: Any) -> Dict[str, Any]:
    return {"url": url, fhir_field: value}


def _parse_simple_extension(ext_obj: Dict[str, Any], fhir_field: str) -> Optional[Any]:
    return ext_obj.get(fhir_field)


def _build_omb_extension(url: str, value: Dict[str, Any]) -> Dict[str, Any]:
    sub: List[Dict[str, Any]] = []
    cat = _build_omb_category(value)
    if cat:
        sub.append(cat)
    if value.get("text"):
        sub.append({"url": "text", "valueString": str(value["text"])})
    return {"url": url, "extension": sub or []}


@dataclass(frozen=True)
class ExtensionDefinition:
    key: str
    url: str
    title_i18n_key: str
    cardinality: str
    build: Callable[[Any], Dict[str, Any]]
    parse: Callable[[Dict[str, Any]], Any]
    validate: Callable[[Any], Any]


def _race_validate(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("race extension must be an object {ombCategory?, text?}")
    out: Dict[str, Any] = {}
    cat = value.get("ombCategory")
    if cat is not None:
        if not isinstance(cat, dict) or not cat.get("code"):
            raise ValueError("race.ombCategory must include a code")
        out["ombCategory"] = {
            "system": cat.get("system") or OMB_CATEGORY_SYSTEM,
            "code": cat["code"],
            "display": cat.get("display"),
        }
    text = value.get("text")
    if text is not None:
        if not isinstance(text, str):
            raise ValueError("race.text must be a string")
        out["text"] = text.strip()
    if not out:
        raise ValueError("race extension must include ombCategory and/or text")
    return out


def _ethnicity_validate(value: Any) -> Dict[str, Any]:
    return _race_validate(value)


SUPPORTED_PATIENT_EXTENSIONS: Tuple[ExtensionDefinition, ...] = (
    ExtensionDefinition(
        key="race",
        url=US_CORE_RACE_URL,
        title_i18n_key="patient.setup.extension.race",
        cardinality="0..1",
        build=lambda v: _build_omb_extension(US_CORE_RACE_URL, v),
        parse=lambda e: _parse_complex_codeable(e.get("extension")),
        validate=_race_validate,
    ),
    ExtensionDefinition(
        key="ethnicity",
        url=US_CORE_ETHNICITY_URL,
        title_i18n_key="patient.setup.extension.ethnicity",
        cardinality="0..1",
        build=lambda v: _build_omb_extension(US_CORE_ETHNICITY_URL, v),
        parse=lambda e: _parse_complex_codeable(e.get("extension")),
        validate=_ethnicity_validate,
    ),
    ExtensionDefinition(
        key="preferred_language",
        url=PREFERRED_LANGUAGE_URL,
        title_i18n_key="patient.setup.extension.preferred_language",
        cardinality="0..1",
        build=lambda v: _build_simple_extension(PREFERRED_LANGUAGE_URL, "valueCode", v),
        parse=lambda e: _parse_simple_extension(e, "valueCode"),
        validate=_validate_preferred_language,
    ),
    ExtensionDefinition(
        key="insurance_provider",
        url=INSURANCE_PROVIDER_URL,
        title_i18n_key="patient.setup.extension.insurance_provider",
        cardinality="0..1",
        build=lambda v: _build_simple_extension(INSURANCE_PROVIDER_URL, "valueString", v),
        parse=lambda e: _parse_simple_extension(e, "valueString"),
        validate=_validate_string,
    ),
)

_PATIENT_BY_KEY: Dict[str, ExtensionDefinition] = {
    ext.key: ext for ext in SUPPORTED_PATIENT_EXTENSIONS
}
_PATIENT_BY_URL: Dict[str, ExtensionDefinition] = {
    ext.url: ext for ext in SUPPORTED_PATIENT_EXTENSIONS
}


def supported_patient_keys() -> Tuple[str, ...]:
    return tuple(_PATIENT_BY_KEY.keys())


def validate_patient_extensions(extensions: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if extensions is None:
        return None
    if not isinstance(extensions, dict):
        raise ValueError("extensions must be a JSON object keyed by extension key")
    cleaned: Dict[str, Any] = {}
    for key, value in extensions.items():
        if value is None:
            continue
        if key not in _PATIENT_BY_KEY:
            raise ValueError(f"unsupported patient extension: {key}")
        cleaned[key] = _PATIENT_BY_KEY[key].validate(value)
    return cleaned or None


def to_fhir_extension_list(extensions: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not extensions:
        return []
    out: List[Dict[str, Any]] = []
    for key, value in extensions.items():
        if value is None or key not in _PATIENT_BY_KEY:
            continue
        out.append(_PATIENT_BY_KEY[key].build(value))
    return out


def from_fhir_extension_list(ext_list: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    if not ext_list:
        return {}
    out: Dict[str, Any] = {}
    for ext_obj in ext_list:
        url = ext_obj.get("url")
        if not url:
            continue
        definition = _PATIENT_BY_URL.get(url)
        if not definition:
            continue
        try:
            parsed = definition.parse(ext_obj)
        except Exception:
            continue
        if parsed is not None:
            out[definition.key] = parsed
    return out