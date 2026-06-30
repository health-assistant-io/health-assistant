"""Regression tests for F6: spec-compliant FHIR R4 pagination.

The bug (audit F6): ``build_search_bundle`` emitted pagination links with
``_offset=<n>`` (non-standard) but ``parse_search_params`` only handled
``page`` — never ``_offset``. Following the ``next`` link always landed on
page 1, so any non-trivial dataset was unreadable via the standard
pagination links.

The fix:
- ``_with_page`` now emits ``page=N`` (1-based, spec-standard).
- ``parse_search_params`` accepts ``_offset`` on input as a tolerant alias
  so legacy links keep working.

These tests verify both directions (output and parsing) plus a full
round-trip that simulates following the ``next`` link repeatedly.
"""

from urllib.parse import parse_qs, urlparse

from app.facade.bundle import build_search_bundle, _with_page
from app.facade.search_params import parse_search_params


# ---------------------------------------------------------------------------
# Output side — links must carry `page=N`, not `_offset=N`
# ---------------------------------------------------------------------------

def test_pagination_links_emit_page_not_offset():
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"_count=2",
        resources=[{"resourceType": "Patient", "id": "a"}],
        total=5,
        offset=0,
        count=2,
    )
    # The first/last/previous/next links must use page=N, never _offset=N.
    # (`self` preserves whatever the client sent — not a generated cursor.)
    cursor_rels = {"first", "last", "previous", "next"}
    for link in bundle["link"]:
        if link["relation"] not in cursor_rels:
            continue
        qs = parse_qs(urlparse(link["url"]).query)
        assert "_offset" not in qs, f"link {link['relation']} carries _offset"
        assert "page" in qs, f"link {link['relation']} missing page"


def test_pagination_next_link_points_to_page_2():
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"_count=2",
        resources=[{"resourceType": "Patient", "id": "a"}],
        total=5,
        offset=0,
        count=2,
    )
    next_link = next(l for l in bundle["link"] if l["relation"] == "next")
    qs = parse_qs(urlparse(next_link["url"]).query)
    assert qs["page"] == ["2"]


def test_pagination_last_link_points_to_final_page():
    # 5 results, 2 per page → 3 pages (offsets 0, 2, 4). Last page = 3.
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"_count=2",
        resources=[{"resourceType": "Patient", "id": "a"}],
        total=5,
        offset=0,
        count=2,
    )
    last_link = next(l for l in bundle["link"] if l["relation"] == "last")
    qs = parse_qs(urlparse(last_link["url"]).query)
    assert qs["page"] == ["3"]


def test_pagination_previous_link_on_page_3_points_to_page_2():
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"_count=2&page=3",
        resources=[{"resourceType": "Patient", "id": "a"}],
        total=5,
        offset=4,
        count=2,
    )
    prev_link = next(l for l in bundle["link"] if l["relation"] == "previous")
    qs = parse_qs(urlparse(prev_link["url"]).query)
    assert qs["page"] == ["2"]


def test_with_page_replaces_existing_offset_param():
    """A legacy link carrying _offset=4 (count=2 → page 3) must be rewritten
    to page=3 with no _offset left in the URL."""
    pairs = [("_count", "2"), ("_offset", "4")]
    out = _with_page(pairs, offset=4, count=2)
    assert ("_offset", "4") not in out
    assert ("page", "3") in out


def test_with_page_replaces_existing_page_param():
    pairs = [("_count", "2"), ("page", "1")]
    out = _with_page(pairs, offset=4, count=2)
    # Old page=1 replaced with page=3.
    assert ("page", "1") not in out
    assert ("page", "3") in out


def test_with_page_offset_zero_is_page_one():
    out = _with_page([("_count", "10")], offset=0, count=10)
    assert ("page", "1") in out


# ---------------------------------------------------------------------------
# Input side — parser accepts both `page` and `_offset`
# ---------------------------------------------------------------------------

def test_parse_search_params_accepts_offset_alias():
    """Legacy clients (and the previous version of this server) emit
    _offset=N. The parser must honor it."""
    p = parse_search_params("Patient", [("_count", "10"), ("_offset", "20")])
    assert p.offset == 20


def test_parse_search_params_offset_zero():
    p = parse_search_params("Patient", [("_count", "10"), ("_offset", "0")])
    assert p.offset == 0


def test_parse_search_params_negative_offset_ignored():
    p = parse_search_params("Patient", [("_count", "10"), ("_offset", "-5")])
    assert p.offset == 0  # ignored → stays at default


def test_parse_search_params_garbage_offset_ignored():
    p = parse_search_params("Patient", [("_count", "10"), ("_offset", "abc")])
    assert p.offset == 0


def test_parse_search_params_page_still_works():
    """The standard `page=N` input is unaffected."""
    p = parse_search_params("Patient", [("_count", "10"), ("page", "3")])
    assert p.offset == 20  # page 3 of 10 → offset 20


def test_parse_search_params_hapi_style_underscore_page():
    """HAPI-style ``_page=N`` is accepted as an alias of ``page=N``."""
    p = parse_search_params("Patient", [("_count", "10"), ("_page", "3")])
    assert p.offset == 20  # page 3 of 10 → offset 20


def test_parse_search_params_three_cursor_forms_equivalent():
    """All three pagination cursor forms (page, _page, _offset) produce the
    same offset. Per the FHIR R4 spec, the cursor param name is server-
    chosen (pagination is driven by Bundle.link[] relations, not a mandated
    query param)."""
    p1 = parse_search_params("Patient", [("_count", "10"), ("page", "2")])
    p2 = parse_search_params("Patient", [("_count", "10"), ("_page", "2")])
    p3 = parse_search_params("Patient", [("_count", "10"), ("_offset", "10")])
    assert p1.offset == p2.offset == p3.offset == 10


# ---------------------------------------------------------------------------
# Round-trip — following the `next` link lands on the next page
# ---------------------------------------------------------------------------

def test_pagination_round_trip_walks_all_pages():
    """The headline F6 bug: following `next` always landed on page 1.
    Now it must walk through every page.

    Simulates a 7-row dataset with _count=2 → pages 1, 2, 3, 4 (last page
    has 1 row). Starting from page 1, follow `next` until absent.
    """
    total = 7
    count = 2

    visited_pages: list[int] = []
    current_qs = b"_count=2"

    # Iterate like a real FHIR client would.
    for _ in range(10):  # safety cap
        params = parse_search_params(
            "Patient",
            [(k, v) for k, v in parse_qs(current_qs.decode("utf-8")).items() for v in v],
        )
        visited_pages.append((params.offset // count) + 1)

        bundle = build_search_bundle(
            base_url="https://host/api/v1/fhir/R4",
            path="/Patient",
            query_string=current_qs,
            resources=[{"resourceType": "Patient", "id": "x"}],
            total=total,
            offset=params.offset,
            count=count,
        )
        next_links = [l for l in bundle["link"] if l["relation"] == "next"]
        if not next_links:
            break
        # Take the next link's query string as the new current_qs.
        next_url = next_links[0]["url"]
        current_qs = urlparse(next_url).query.encode("utf-8")

    # Must have walked pages 1..4 in order — the bug would loop on page 1.
    assert visited_pages == [1, 2, 3, 4]


def test_pagination_round_trip_offset_alias_works():
    """A legacy client that sends _offset=N (and receives page=N+1 links
    back) must still advance correctly."""
    total = 5
    count = 2

    # Start with the legacy _offset=2 (page 2).
    params = parse_search_params(
        "Patient",
        [("_count", "2"), ("_offset", "2")],
    )
    assert params.offset == 2

    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"_count=2&_offset=2",
        resources=[{"resourceType": "Patient", "id": "x"}],
        total=total,
        offset=params.offset,
        count=count,
    )

    # The `next` link should target page 3 (offset 4), not page 1.
    next_link = next(l for l in bundle["link"] if l["relation"] == "next")
    qs = parse_qs(urlparse(next_link["url"]).query)
    assert qs["page"] == ["3"]
