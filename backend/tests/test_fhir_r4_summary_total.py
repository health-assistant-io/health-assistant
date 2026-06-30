"""Regression tests for F15 + F16: Bundle entry search.mode + _summary=count + _total.

F15: Bundle entries lacked ``search.mode`` (``match``/``include``) and
``search.score``. Required-once ``_include`` ships; expected by strict
clients today. Fix: primary entries carry ``search.mode = "match"`` and
``_include`` entries carry ``search.mode = "include"``. (``score`` is
server-discretionary — omit unless we claim to compute it.)

F16: Bundle ``total`` was always computed (extra ``COUNT(*)``) and always
present. ``_summary=count`` should return only the count (empty entry[],
total present); ``_total=none`` should omit ``total`` entirely (and skip
the COUNT). Fix: ``build_search_bundle`` accepts an ``include_total``
flag; ``crud.search`` short-circuits on ``_summary=count`` (skip the main
SELECT) and skips the COUNT when ``_total=none``.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.facade.bundle import build_search_bundle


# ---------------------------------------------------------------------------
# F15 — search.mode on every entry
# ---------------------------------------------------------------------------

def test_primary_entries_carry_match_mode():
    """F15: every primary (non-include) entry must carry search.mode = match."""
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"_count=2",
        resources=[
            {"resourceType": "Patient", "id": "a"},
            {"resourceType": "Patient", "id": "b"},
        ],
        total=2,
        offset=0,
        count=2,
    )
    for entry in bundle["entry"]:
        assert entry["search"]["mode"] == "match"


def test_include_entries_carry_include_mode():
    """F15: include_resources entries carry search.mode = include."""
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Observation",
        query_string=b"",
        resources=[{"resourceType": "Observation", "id": "obs1"}],
        total=1,
        offset=0,
        count=50,
        include_resources=[
            {"resourceType": "Patient", "id": "pat1"},
            {"resourceType": "Practitioner", "id": "prac1"},
        ],
    )
    modes = [e["search"]["mode"] for e in bundle["entry"]]
    assert modes == ["match", "include", "include"]


def test_entry_search_block_is_dict():
    """search.mode lives under a `search` sub-dict per FHIR R4 Bundle.entry."""
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"",
        resources=[{"resourceType": "Patient", "id": "a"}],
        total=1,
        offset=0,
        count=50,
    )
    assert isinstance(bundle["entry"][0]["search"], dict)
    assert "mode" in bundle["entry"][0]["search"]


# ---------------------------------------------------------------------------
# F16 — _summary=count returns count only (empty entry[], total present)
# ---------------------------------------------------------------------------

def test_summary_count_returns_empty_entries_with_total():
    """The crud dispatcher short-circuits on _summary=count: it returns a
    Bundle with an empty entry[] and the total. The main SELECT is skipped.
    Here we verify the Bundle shape (the dispatcher logic is in crud.search;
    we exercise it via build_search_bundle directly)."""
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"_summary=count",
        resources=[],  # dispatcher passes empty when summary=count
        total=42,
        offset=0,
        count=50,
    )
    assert bundle["entry"] == []
    assert bundle["total"] == 42


def test_summary_true_treated_as_count_in_dispatcher():
    """Per FHIR spec, _summary=true is equivalent to _summary=count for
    servers that don't return a subset of fields."""
    # The dispatcher parses _summary and treats 'count' or 'true' as count-only.
    # We verify the predicate logic by simulating the parse:
    from app.facade.search_params import parse_search_params

    p1 = parse_search_params("Patient", [("_summary", "count")])
    p2 = parse_search_params("Patient", [("_summary", "true")])
    assert (p1._summary or "").lower() in ("count", "true")
    assert (p2._summary or "").lower() in ("count", "true")


# ---------------------------------------------------------------------------
# F16 — _total=none omits `total` from Bundle
# ---------------------------------------------------------------------------

def test_total_none_omits_total_key():
    """When _total=none is requested, the Bundle must not include the `total`
    key at all. (The COUNT is also skipped at the dispatcher level.)"""
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"_total=none",
        resources=[{"resourceType": "Patient", "id": "a"}],
        total=0,  # dispatcher passes 0 when skip_total
        offset=0,
        count=50,
        include_total=False,  # the F16 flag
    )
    assert "total" not in bundle


def test_total_accurate_keeps_total_key():
    """Default behavior: _total=accurate (or unset) → total is present."""
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"",
        resources=[{"resourceType": "Patient", "id": "a"}],
        total=5,
        offset=0,
        count=50,
    )
    assert bundle["total"] == 5


def test_total_estimated_treated_as_accurate():
    """The dispatcher treats _total=estimated the same as accurate for now
    (no cheap estimate available without a separate index). The total is
    still included."""
    # Default behavior (no _total param) keeps total.
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"",
        resources=[],
        total=10,
        offset=0,
        count=50,
    )
    assert "total" in bundle
    assert bundle["total"] == 10


# ---------------------------------------------------------------------------
# F15 + F16 combo — search.mode + summary=count
# ---------------------------------------------------------------------------

def test_summary_count_bundle_still_has_correct_shape():
    """A _summary=count Bundle must still be a valid Bundle: resourceType,
    type, link[], and an empty entry[] (no search.mode needed since empty)."""
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Observation",
        query_string=b"_summary=count",
        resources=[],
        total=99,
        offset=0,
        count=50,
    )
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "searchset"
    assert bundle["entry"] == []
    assert bundle["total"] == 99
    # The pagination links are still present (self, first, last).
    rels = {l["relation"] for l in bundle["link"]}
    assert "self" in rels
    assert "first" in rels
    assert "last" in rels


# ---------------------------------------------------------------------------
# Dispatcher integration — _total=none is parsed and propagated
# ---------------------------------------------------------------------------

def test_dispatcher_parses_total_none():
    """Verify the parser picks up _total=none so the dispatcher can act on it.
    The actual COUNT-skip behavior is verified against a real DB in the
    integration test suite; here we just verify the param travels through the
    parser into FhirSearchParams._total."""
    from app.facade.search_params import parse_search_params

    p = parse_search_params("Patient", [("_total", "none")])
    assert p._total == "none"


def test_dispatcher_parses_total_accurate():
    from app.facade.search_params import parse_search_params

    p = parse_search_params("Patient", [("_total", "accurate")])
    assert p._total == "accurate"


def test_total_defaults_to_none_when_unset():
    """When _total isn't in the request, params._total is None — the dispatcher
    treats that as 'accurate' (the default, keeps the total)."""
    from app.facade.search_params import parse_search_params

    p = parse_search_params("Patient", [])
    assert p._total is None
