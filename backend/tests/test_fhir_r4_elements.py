"""Regression tests for F14: _elements honored + _format=xml explicit reject.

The audit: ``_summary=count``, ``_elements``, ``_include``, ``_revinclude``,
``_format=xml`` were all parsed but not honored. ``_elements`` was in
``STANDARD_PARAMS`` but not in the parser's ``if/elif`` chain — silently
dropped. ``_summary=count`` returns the full bundle. ``_include``/
``_revinclude`` never fetch include-resources. ``_format=xml`` always
returns JSON.

This phase closes:
- ``_elements``: parsed + applied as a post-serialization projection.
- ``_format=xml``: explicitly rejected with a 400 OperationOutcome
  (previously silently returned JSON — misleading).

Deferred (Phase 9): ``_include`` / ``_revinclude`` chained search params
and full XML serialization (requires ``lxml`` dependency).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.facade.search_params import parse_search_params


# ---------------------------------------------------------------------------
# _elements parsing
# ---------------------------------------------------------------------------

def test_elements_parsed_as_list():
    """_elements is a comma-separated list of top-level fields."""
    p = parse_search_params("Patient", [("_elements", "name,birthDate")])
    assert p._elements == ["name", "birthDate"]


def test_elements_strips_whitespace_and_drops_empty():
    p = parse_search_params(
        "Patient", [("_elements", " name , birthDate , ")]
    )
    assert p._elements == ["name", "birthDate"]


def test_elements_single_field():
    p = parse_search_params("Patient", [("_elements", "name")])
    assert p._elements == ["name"]


def test_elements_unset_defaults_to_none():
    p = parse_search_params("Patient", [])
    assert p._elements is None


def test_elements_empty_string_returns_none():
    """An empty _elements value (e.g. `?_elements=`) means no projection."""
    p = parse_search_params("Patient", [("_elements", "")])
    assert p._elements is None


# ---------------------------------------------------------------------------
# _elements projection (applied in crud.search post-serialization)
# ---------------------------------------------------------------------------

def test_elements_projection_keeps_requested_plus_always_present():
    """The _elements projection keeps the requested fields plus resourceType,
    id, and meta (always-present per FHIR R4 spec)."""
    from app.facade.crud import _project_elements

    resource = {
        "resourceType": "Patient",
        "id": "abc",
        "meta": {"versionId": "1", "lastUpdated": "2024-01-01T00:00:00Z"},
        "name": [{"family": "Doe"}],
        "birthDate": "1990-01-01",
        "address": [{"line": ["123 Main"]}],
        "telecom": [{"system": "phone", "value": "555-1234"}],
    }

    projected = _project_elements(resource, ["name", "birthDate"])

    # Requested fields kept.
    assert projected["name"] == resource["name"]
    assert projected["birthDate"] == resource["birthDate"]
    # Always-present fields kept.
    assert projected["resourceType"] == "Patient"
    assert projected["id"] == "abc"
    assert "meta" in projected
    # Non-requested fields dropped.
    assert "address" not in projected
    assert "telecom" not in projected


def test_elements_projection_none_returns_resource_unchanged():
    """When _elements is None, no projection is applied — the resource is
    returned unchanged."""
    from app.facade.crud import _project_elements

    resource = {"resourceType": "Patient", "id": "abc", "name": [], "address": []}
    assert _project_elements(resource, None) is resource
    assert _project_elements(resource, []) is resource


def test_elements_projection_when_field_missing_in_resource():
    """If the resource doesn't have a requested field, the projected dict just
    omits it (no error, no None injection)."""
    from app.facade.crud import _project_elements

    resource = {"resourceType": "Patient", "id": "abc"}
    projected = _project_elements(resource, ["name", "birthDate"])
    assert projected == {"resourceType": "Patient", "id": "abc"}


# ---------------------------------------------------------------------------
# _format=xml explicit reject (and other unsupported formats)
# ---------------------------------------------------------------------------

def test_format_json_accepted():
    p = parse_search_params("Patient", [("_format", "json")])
    assert p._format == "json"


def test_format_application_fhir_json_accepted():
    p = parse_search_params("Patient", [("_format", "application/fhir+json")])
    assert p._format == "application/fhir+json"


def test_format_application_json_accepted():
    p = parse_search_params("Patient", [("_format", "application/json")])
    assert p._format == "application/json"


def test_format_xml_rejected_with_400():
    """F14: _format=xml is not supported. Reject with a 400 OperationOutcome
    rather than silently returning JSON (the previous behavior)."""
    with pytest.raises(HTTPException) as exc_info:
        parse_search_params("Patient", [("_format", "xml")])
    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    assert detail["resourceType"] == "OperationOutcome"
    assert detail["issue"][0]["severity"] == "fatal"
    assert "xml" in detail["issue"][0]["diagnostics"].lower()


def test_format_application_fhir_xml_rejected_with_400():
    with pytest.raises(HTTPException) as exc_info:
        parse_search_params("Patient", [("_format", "application/fhir+xml")])
    assert exc_info.value.status_code == 400


def test_format_rdf_rejected_with_400():
    """Non-JSON, non-XML formats are also rejected."""
    with pytest.raises(HTTPException) as exc_info:
        parse_search_params("Patient", [("_format", "application/rdf+turtle")])
    assert exc_info.value.status_code == 400


def test_format_case_insensitive():
    """_format values are matched case-insensitively (XML / Xml / xMl all rejected)."""
    with pytest.raises(HTTPException):
        parse_search_params("Patient", [("_format", "XML")])
    with pytest.raises(HTTPException):
        parse_search_params("Patient", [("_format", "Application/fhir+Xml")])


def test_format_default_unset():
    """When no _format is provided, the param stays None (dispatcher defaults
    to JSON)."""
    p = parse_search_params("Patient", [])
    assert p._format is None


# ---------------------------------------------------------------------------
# _include / _revinclude — documented as parsed-but-not-honored
# ---------------------------------------------------------------------------

def test_include_parsed_but_not_honored():
    """F14 sub-point: _include is parsed into params._include (so the
    dispatcher knows it was requested) but the actual included-resources
    fetch is deferred (Phase 9). This test just verifies the parse — no
    assertion that included resources appear in the Bundle."""
    p = parse_search_params(
        "Observation", [("_include", "Observation:subject")]
    )
    assert p._include == ["Observation:subject"]


def test_revinclude_parsed():
    p = parse_search_params(
        "Patient", [("_revinclude", "Observation:subject")]
    )
    assert p._revinclude == ["Observation:subject"]
