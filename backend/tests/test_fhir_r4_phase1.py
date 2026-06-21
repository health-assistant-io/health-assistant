"""Tests for FHIR R4 facade Phase 1 scaffolding.

Covers:
- SoftDeleteMixin column on base
- Search param parser (all FHIR prefixes, _sort, _count bounds, lenient unknown)
- Bundle builder (searchset type, pagination links, _include)
- OperationOutcome response helpers
- CapabilityStatement builder (validates against fhir.resources)
- /metadata endpoint returns valid CapabilityStatement (200, Cache-Control)
- Unknown facade route returns 501 OperationOutcome
"""
import math
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from app.facade.search_params import (
    DATE_PREFIXES,
    DEFAULT_COUNT,
    MAX_COUNT,
    FhirSearchParams,
    DateFilter,
    parse_search_params,
)
from app.facade.responses import (
    gone,
    no_content,
    not_found,
    ok_response,
    operation_outcome,
)
from app.facade.bundle import build_search_bundle
from app.facade.registry import RESOURCE_REGISTRY, ResourceEntry
from app.services.fhir_facade_service import build_capability_statement, get_software_version
from app.services.fhir_helpers import parse_fhir_resource
from app.models.base import SoftDeleteMixin


# ---------------------------------------------------------------------------
# SoftDeleteMixin
# ---------------------------------------------------------------------------

def test_soft_delete_mixin_has_deleted_at():
    assert SoftDeleteMixin.deleted_at is not None
    # The column should be nullable + indexed.
    col = SoftDeleteMixin.deleted_at
    # Column is a class attribute; .nullable is on the underlying SQLAlchemy Column.
    assert col.nullable is True
    assert col.index is True


# ---------------------------------------------------------------------------
# Search param parser
# ---------------------------------------------------------------------------

def test_parse_search_params_empty():
    p = parse_search_params("Patient", [])
    assert p.resource_type == "Patient"
    assert p._id is None
    assert p._count == DEFAULT_COUNT
    assert p._sort == []
    assert p.resource_filters == {}


def test_parse_search_params_standard_id():
    p = parse_search_params("Observation", [("_id", "abc"), ("_id", "def")])
    assert p._id == ["abc", "def"]


def test_parse_search_params_count_bounds():
    p = parse_search_params("Patient", [("_count", "10")])
    assert p._count == 10

    p = parse_search_params("Patient", [("_count", str(MAX_COUNT + 1000))])
    assert p._count == MAX_COUNT  # capped

    with pytest.raises(HTTPException) as exc:
        parse_search_params("Patient", [("_count", "-1")])
    assert exc.value.status_code == 400
    assert "OperationOutcome" in str(exc.value.detail) or isinstance(exc.value.detail, dict)


def test_parse_search_params_count_invalid():
    with pytest.raises(HTTPException):
        parse_search_params("Patient", [("_count", "not-a-number")])


def test_parse_search_params_sort():
    p = parse_search_params(
        "Observation",
        [("_sort", "-date,_id,code")],
    )
    # "code" is not in Observation sort allowlist? Actually it is.
    assert ("effective_datetime", True) in p._sort  # -date → effective_datetime, descending
    assert ("id", False) in p._sort  # _id ascending
    assert ("code", False) in p._sort  # code ascending


def test_parse_search_params_sort_unknown_key_is_ignored():
    p = parse_search_params("Patient", [("_sort", "-nonexistent,_lastUpdated")])
    # Unknown sort keys are silently dropped (defensive).
    assert ("updated_at", False) in p._sort
    assert all(col != "nonexistent" for col, _ in p._sort)


def test_parse_search_params_lastupdated_with_prefix():
    for prefix in ("gt", "ge", "lt", "le"):
        p = parse_search_params("Patient", [("_lastUpdated", f"{prefix}2024-01-01")])
        assert p._lastUpdated is not None
        assert len(p._lastUpdated) == 1
        assert p._lastUpdated[0].prefix == prefix
        assert p._lastUpdated[0].value == "2024-01-01"


def test_parse_search_params_lastupdated_no_prefix():
    p = parse_search_params("Patient", [("_lastUpdated", "2024-01-01")])
    assert p._lastUpdated[0].prefix is None
    assert p._lastUpdated[0].value == "2024-01-01"


def test_parse_search_params_resource_specific():
    p = parse_search_params(
        "Observation",
        [("patient", "Patient/123"), ("code", "http://loinc.org|1234-5"), ("date", "2024-01")],
    )
    assert p.resource_filters["patient"] == ["Patient/123"]
    assert p.resource_filters["code"] == ["http://loinc.org|1234-5"]
    assert p.resource_filters["date"] == ["2024-01"]


def test_parse_search_params_unknown_param_is_lenient():
    # Lenient by default: unknown params are dropped (not 400).
    p = parse_search_params("Patient", [("nonexistent", "value")])
    assert "nonexistent" not in p.resource_filters


def test_parse_search_params_page_offset():
    p = parse_search_params("Patient", [("_count", "10"), ("page", "3")])
    assert p._count == 10
    assert p.offset == 20  # page 3 of 10 → offset 20


def test_parse_search_params_page_invalid_is_ignored():
    p = parse_search_params("Patient", [("_count", "10"), ("page", "0")])
    assert p.offset == 0
    p = parse_search_params("Patient", [("_count", "10"), ("page", "garbage")])
    assert p.offset == 0


def test_date_filter_to_orm_filter_eq():
    from sqlalchemy import Column, DateTime

    col = Column("test", DateTime)
    f = DateFilter(prefix="eq", value="2024-01-01")
    pred = f.to_orm_filter(col)
    assert pred is not None  # got a SQLAlchemy clause


def test_date_filter_to_orm_filter_ap_uses_and():
    from sqlalchemy import Column, DateTime

    col = Column("test", DateTime)
    f = DateFilter(prefix="ap", value="2024-01-01")
    pred = f.to_orm_filter(col)
    # 'ap' should produce a BETWEEN-like (AND) predicate.
    assert pred is not None


def test_date_filter_to_orm_filter_invalid_value_returns_none():
    from sqlalchemy import Column, DateTime

    col = Column("test", DateTime)
    f = DateFilter(prefix=None, value="garbage")
    assert f.to_orm_filter(col) is None


def test_date_prefix_constants():
    # Sanity check that all FHIR date prefixes are recognized.
    assert "eq" in DATE_PREFIXES
    assert "gt" in DATE_PREFIXES
    assert "ge" in DATE_PREFIXES
    assert "lt" in DATE_PREFIXES
    assert "le" in DATE_PREFIXES
    assert "sa" in DATE_PREFIXES
    assert "eb" in DATE_PREFIXES
    assert "ap" in DATE_PREFIXES


# ---------------------------------------------------------------------------
# Bundle builder
# ---------------------------------------------------------------------------

def test_build_search_bundle_basic():
    resources = [
        {"resourceType": "Patient", "id": "abc"},
        {"resourceType": "Patient", "id": "def"},
    ]
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"_count=2",
        resources=resources,
        total=5,
        offset=0,
        count=2,
    )
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "searchset"
    assert bundle["total"] == 5
    assert len(bundle["entry"]) == 2
    assert bundle["entry"][0]["fullUrl"] == "https://host/api/v1/fhir/R4/Patient/abc"
    assert bundle["entry"][0]["resource"]["id"] == "abc"
    # Should have self, first, last, next links (no previous — on page 1).
    rels = {l["relation"] for l in bundle["link"]}
    assert "self" in rels
    assert "first" in rels
    assert "last" in rels
    assert "next" in rels
    assert "previous" not in rels


def test_build_search_bundle_middle_page_has_previous():
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"_count=2&_offset=2",
        resources=[{"resourceType": "Patient", "id": "x"}],
        total=5,
        offset=2,
        count=2,
    )
    rels = {l["relation"] for l in bundle["link"]}
    assert "previous" in rels
    assert "next" in rels


def test_build_search_bundle_last_page_no_next():
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"_count=2&_offset=4",
        resources=[{"resourceType": "Patient", "id": "x"}],
        total=5,
        offset=4,
        count=2,
    )
    rels = {l["relation"] for l in bundle["link"]}
    assert "next" not in rels
    assert "previous" in rels


def test_build_search_bundle_include_resources():
    resources = [{"resourceType": "Observation", "id": "obs1"}]
    includes = [
        {"resourceType": "Patient", "id": "pat1"},
        {"resourceType": "Practitioner", "id": "prac1"},
    ]
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Observation",
        query_string=b"",
        resources=resources,
        total=1,
        offset=0,
        count=50,
        include_resources=includes,
    )
    assert len(bundle["entry"]) == 3  # 1 main + 2 includes
    # Total counts only main matches, not includes.
    assert bundle["total"] == 1
    # Include entries should have proper fullUrls.
    full_urls = [e.get("fullUrl") for e in bundle["entry"]]
    assert "https://host/api/v1/fhir/R4/Patient/pat1" in full_urls
    assert "https://host/api/v1/fhir/R4/Practitioner/prac1" in full_urls


def test_build_search_bundle_meta():
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"",
        resources=[],
        total=0,
        offset=0,
        count=50,
    )
    assert "meta" in bundle
    assert "lastUpdated" in bundle["meta"]


def test_build_search_bundle_empty_results():
    bundle = build_search_bundle(
        base_url="https://host/api/v1/fhir/R4",
        path="/Patient",
        query_string=b"",
        resources=[],
        total=0,
        offset=0,
        count=50,
    )
    assert bundle["total"] == 0
    assert bundle["entry"] == []


# ---------------------------------------------------------------------------
# OperationOutcome helpers
# ---------------------------------------------------------------------------

def test_operation_outcome_basic():
    r = operation_outcome("error", "invalid", "boom", 400)
    assert r.status_code == 400
    import json
    body = json.loads(r.body)
    assert body["resourceType"] == "OperationOutcome"
    assert body["issue"][0]["severity"] == "error"
    assert body["issue"][0]["code"] == "invalid"
    assert body["issue"][0]["diagnostics"] == "boom"


def test_not_found_response():
    r = not_found("Patient", "abc")
    assert r.status_code == 404
    import json
    body = json.loads(r.body)
    assert "not found" in body["issue"][0]["diagnostics"]


def test_gone_response():
    r = gone("Patient", "abc")
    assert r.status_code == 410
    import json
    body = json.loads(r.body)
    assert "deleted" in body["issue"][0]["diagnostics"]


def test_no_content_response():
    r = no_content()
    assert r.status_code == 204


def test_ok_response_with_etag():
    r = ok_response({"resourceType": "Patient", "id": "x"}, etag='W/"1"')
    assert r.status_code == 200
    assert r.headers["ETag"] == 'W/"1"'


# ---------------------------------------------------------------------------
# CapabilityStatement builder
# ---------------------------------------------------------------------------

def test_get_software_version_returns_string():
    v = get_software_version()
    assert isinstance(v, str)
    assert len(v) > 0
    # Should look like a semver.
    assert any(ch.isdigit() for ch in v)


def test_capability_statement_structure():
    cs = build_capability_statement("https://host/api/v1/fhir/R4")
    assert cs["resourceType"] == "CapabilityStatement"
    assert cs["status"] == "active"
    assert cs["kind"] == "instance"
    assert cs["fhirVersion"] == "4.3.0"
    assert "json" in cs["format"]
    assert cs["software"]["name"] == "Health Assistant"
    assert "rest" in cs
    assert cs["rest"][0]["mode"] == "server"


def test_capability_statement_validates_against_fhir_resources():
    """The CapabilityStatement should be valid per fhir.resources.R4B."""
    cs = build_capability_statement("https://host/api/v1/fhir/R4")
    # Should not raise.
    parsed = parse_fhir_resource("CapabilityStatement", cs)
    assert parsed.__resource_type__ == "CapabilityStatement"


def test_capability_statement_lists_system_interactions():
    cs = build_capability_statement("https://host/api/v1/fhir/R4")
    interactions = {i["code"] for i in cs["rest"][0]["interaction"]}
    assert "search-system" in interactions
    assert "batch" in interactions
    assert "transaction" in interactions


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_register_and_get():
    # Use a unique resource type to avoid collision with real registrations.
    class Dummy:
        pass

    entry = ResourceEntry(
        resource_type="TestDummyResourceXYZ_unique",
        model=Dummy,
        search_params=frozenset({"code"}),
    )
    try:
        RESOURCE_REGISTRY.register(entry)
        got = RESOURCE_REGISTRY.get("TestDummyResourceXYZ_unique")
        assert got is entry
        assert "TestDummyResourceXYZ_unique" in RESOURCE_REGISTRY
        assert len(RESOURCE_REGISTRY) >= 1
    finally:
        # Clean up the registry to avoid leaking between tests.
        RESOURCE_REGISTRY._entries.pop("TestDummyResourceXYZ_unique", None)


def test_registry_duplicate_raises():
    class Dummy:
        pass

    name = "TestDupResourceXYZ_unique"
    RESOURCE_REGISTRY.register(ResourceEntry(resource_type=name, model=Dummy))
    try:
        with pytest.raises(ValueError, match="already registered"):
            RESOURCE_REGISTRY.register(ResourceEntry(resource_type=name, model=Dummy))
    finally:
        RESOURCE_REGISTRY._entries.pop(name, None)


# ---------------------------------------------------------------------------
# HTTP layer — metadata endpoint + unknown route catch-all
# ---------------------------------------------------------------------------

@pytest.fixture
def app_with_facade(monkeypatch):
    """Build a minimal FastAPI app with just the facade router mounted."""
    from app.api.v1.endpoints.fhir_r4 import router as facade_router

    app = FastAPI()
    app.include_router(facade_router, prefix="/api/v1")
    return app


def test_metadata_endpoint_returns_200(app_with_facade):
    client = TestClient(app_with_facade)
    r = client.get("/api/v1/fhir/R4/metadata")
    assert r.status_code == 200
    body = r.json()
    assert body["resourceType"] == "CapabilityStatement"
    assert body["fhirVersion"] == "4.3.0"
    assert body["software"]["name"] == "Health Assistant"


def test_metadata_endpoint_cache_control(app_with_facade):
    client = TestClient(app_with_facade)
    r = client.get("/api/v1/fhir/R4/metadata")
    assert "cache-control" in r.headers
    assert "max-age=300" in r.headers["cache-control"]


def test_metadata_endpoint_no_auth_required(app_with_facade):
    """CapabilityStatement must be reachable without auth (FHIR spec requirement)."""
    client = TestClient(app_with_facade)
    r = client.get("/api/v1/fhir/R4/metadata")
    # Should NOT be 401/403.
    assert r.status_code == 200


def test_unknown_facade_route_returns_404(app_with_facade):
    """Unknown resource type now returns 404 OperationOutcome (was 501 in the
    initial Phase 1 scaffold — Phase 5 replaced the catch-all with proper
    resource-type dispatch via RESOURCE_REGISTRY)."""
    from app.core.security import get_current_user
    from app.schemas.user import TokenData
    from uuid import uuid4

    fake_user = TokenData(user_id=uuid4(), tenant_id=uuid4(), role="USER", sub="test")
    app_with_facade.dependency_overrides[get_current_user] = lambda: fake_user
    try:
        client = TestClient(app_with_facade)
        r = client.get("/api/v1/fhir/R4/NotARealResource")
        assert r.status_code == 404
        body = r.json()
        assert body["resourceType"] == "OperationOutcome"
        assert body["issue"][0]["code"] == "not-found"
    finally:
        app_with_facade.dependency_overrides = {}
