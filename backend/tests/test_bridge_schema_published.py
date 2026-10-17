"""The bridge sub-API schema + fixture wall (plan 32 D11 — schema-first SDKs).

The committed schema artifact (extracted from the provider's live route
table + payload models by ``scripts/export_bridge_schema.py``) is pinned
four ways: the publish route serves exactly the committed bytes; the
schema's route table matches ``_ROUTES``; the cross-SDK fixtures validate
against the schema's components; and the generated python-sdk model layer
(plus the legacy hand-written models) round-trip the fixture payloads —
the search-providers wall: implementations may differ, outputs may not.
"""

import importlib.util
import json
import pathlib
import sys
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

SCHEMA_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "app"
    / "extensions"
    / "builtin"
    / "health_assistant_bridge"
    / "schema"
    / "bridge-api.schema.json"
)
FIXTURES_PATH = SCHEMA_PATH.with_name("fixtures.json")
GENERATED_MODELS_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "integrations"
    / "health-assistant-bridge"
    / "python-sdk"
    / "health_assistant_bridge"
    / "generated_models.py"
)
LEGACY_MODELS_PATH = GENERATED_MODELS_PATH.with_name("models.py")


def _load_module(path: pathlib.Path, name: str, pkg=None):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if pkg is not None:
        pkg.__dict__[name.rsplit(".", 1)[1]] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text())


@pytest.fixture(scope="module")
def fixtures():
    return json.loads(FIXTURES_PATH.read_text())


@pytest.fixture(scope="module")
def generated_models():
    return _load_module(GENERATED_MODELS_PATH, "bridge_generated_models_testhaul")


@pytest.fixture(scope="module")
def legacy_models():
    return _load_module(LEGACY_MODELS_PATH, "bridge_legacy_models_testhaul")


# ---------------------------------------------------------------------------
# A tiny draft-2020-12 subset validator (types/required/enum/items/refs)
# ---------------------------------------------------------------------------


def _resolve(ref, schema):
    assert ref.startswith("#/components/"), f"unsupported ref {ref}"
    return schema["components"][ref.rsplit("/", 1)[1]]


def _validate(value, prop, schema, path):
    if "$ref" in prop:
        _validate(value, _resolve(prop["$ref"], schema), schema, path)
        return
    if "anyOf" in prop:
        problems = []
        for branch in prop["anyOf"]:
            try:
                _validate(value, branch, schema, path)
                return
            except AssertionError as e:
                problems.append(str(e))
        raise AssertionError(f"{path}: matches no anyOf branch ({problems})")
    if value is None:
        types_ = prop.get("type")
        nullable = types_ == "null" or (isinstance(types_, list) and "null" in types_)
        assert nullable, f"{path}: unexpected null"
        return
    t = prop.get("type")
    t = t[0] if isinstance(t, list) else t
    if "enum" in prop:
        assert value in prop["enum"], f"{path}: {value!r} not in {prop['enum']}"
    if t == "string":
        assert isinstance(value, str), f"{path}: expected string, got {type(value).__name__}"
    elif t == "integer":
        assert isinstance(value, int) and not isinstance(value, bool), f"{path}: expected integer"
    elif t == "number":
        assert isinstance(value, (int, float)) and not isinstance(value, bool), (
            f"{path}: expected number"
        )
    elif t == "boolean":
        assert isinstance(value, bool), f"{path}: expected boolean"
    elif t == "array":
        assert isinstance(value, list), f"{path}: expected array"
        for i, item in enumerate(value):
            _validate(item, prop.get("items", {}), schema, f"{path}[{i}]")
    elif t == "object":
        assert isinstance(value, dict), f"{path}: expected object"
        for req in prop.get("required", []):
            assert req in value, f"{path}: missing required '{req}'"
        for key, sub in prop.get("properties", {}).items():
            if key in value:
                _validate(value[key], sub, schema, f"{path}.{key}")
        additional = prop.get("additionalProperties")
        if isinstance(additional, dict):
            for key, item in value.items():
                if key not in prop.get("properties", {}):
                    _validate(item, additional, schema, f"{path}.{key}")


# ---------------------------------------------------------------------------
# The publish route
# ---------------------------------------------------------------------------


class MockUser:
    def __init__(self):
        self.id = uuid.uuid4()
        self.user_id = self.id
        self.role = "user"
        self.tenant_id = uuid.uuid4()


@pytest.mark.asyncio
async def test_schema_route_serves_committed_schema(async_client: AsyncClient):
    from app.core.security import get_current_user
    from app.main import app

    manifest = json.loads((SCHEMA_PATH.parent.parent / "manifest.json").read_text())
    app.dependency_overrides[get_current_user] = lambda: MockUser()
    try:
        with patch("app.api.v1.endpoints.integrations.integration_registry") as mock_registry:
            mock_registry.get_all_manifests.return_value = [manifest]
            mock_registry.base_dir.side_effect = lambda domain: str(SCHEMA_PATH.parent.parent)
            response = await async_client.get("/api/v1/integrations/health_assistant_bridge/schema")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == json.loads(SCHEMA_PATH.read_text())
    assert response.json()["schemaVersion"] == 1
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.asyncio
async def test_schema_route_requires_auth(async_client: AsyncClient):
    response = await async_client.get("/api/v1/integrations/health_assistant_bridge/schema")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_schema_route_unknown_domain_404(async_client: AsyncClient):
    from app.core.security import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: MockUser()
    try:
        response = await async_client.get("/api/v1/integrations/nope/schema")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_schema_route_404_without_schema(async_client: AsyncClient):
    from app.core.security import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: MockUser()
    try:
        with patch("app.api.v1.endpoints.integrations.integration_registry") as mock_registry:
            mock_registry.get_all_manifests.return_value = [
                {"id": "dev-dummy", "capabilities": ["inbound-webhook"]}
            ]
            mock_registry.base_dir.return_value = "/nonexistent"
            response = await async_client.get("/api/v1/integrations/dev_dummy/schema")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Schema vs the live provider surface (belt+braces beside the CI --check gate)
# ---------------------------------------------------------------------------


def test_schema_route_table_matches_provider(schema):
    from app.extensions.builtin.health_assistant_bridge.provider import _ROUTES

    def _template(pattern):
        import re as _re

        if isinstance(pattern, str):
            return pattern
        return (
            _re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", pattern.pattern)
            .removeprefix("^")
            .removesuffix("$")
        )

    live = {(m, _template(p)) for m, p, _h in _ROUTES}
    committed = {(r["method"], r["path"]) for r in schema["routes"]}
    assert live == committed
    assert len(schema["routes"]) == len(_ROUTES)


def test_schema_feature_flags_match_provider_constant(schema):
    from app.extensions.builtin.health_assistant_bridge.provider import BRIDGE_FEATURES

    assert schema["handshake"]["featureFlags"]["vocabulary"] == list(BRIDGE_FEATURES)


def test_schema_error_codes_match_live_vocabulary(schema):
    from app.extensions.builtin.health_assistant_bridge.provider import (
        HealthAssistantBridgeProvider,
    )
    from scripts.export_bridge_schema import _bridge_error_vocabulary

    live = _bridge_error_vocabulary(HealthAssistantBridgeProvider)
    assert schema["errors"]["codes"] == live
    assert "validation" in live and "not_found" in live


def test_schema_sse_vocabulary_matches_flow_events(schema):
    from app.ai.flow_events_payload import FlowEvent

    assert schema["sse"]["eventNames"] == [e.value for e in FlowEvent]
    assert "flow_interrupted" not in schema["sse"]["eventNames"]


# ---------------------------------------------------------------------------
# The fixture wall
# ---------------------------------------------------------------------------


def test_fixtures_validate_against_components(schema, fixtures):
    components = schema["components"]
    for route, entry in fixtures["routes"].items():
        method, path = route.split(" ", 1)
        routed = next(r for r in schema["routes"] if r["method"] == method and r["path"] == path)
        assert entry["auth"] == routed["auth"], route
        if "requestExample" in entry:
            ref = routed.get("request", {}).get("$ref")
            if ref:
                _validate(
                    entry["requestExample"],
                    _resolve(ref, schema),
                    schema,
                    f"{route}.request",
                )
    for entry in fixtures["componentExamples"]:
        comp = components[entry["responseComponent"]]
        assert comp.get("examples"), f"{entry['responseComponent']} carries no example"
        _validate(comp["examples"][0], comp, schema, entry["responseComponent"])


def test_fixture_stream_frames_validate(schema, fixtures):
    components = schema["components"]
    names = set(schema["sse"]["eventNames"])
    for frame in fixtures["stream"]["frames"]:
        if frame["event"] == "keep-alive":
            assert frame["line"].startswith(":")
            continue
        assert frame["event"] in names, frame["event"]
        assert frame["line"] == "data: " + json.dumps(
            frame["payload"], separators=(",", ":"), default=str
        )
        component = next(
            c
            for n, c in components.items()
            if n.endswith("Frame")
            and c.get("examples")
            and c["examples"][0].get("event") == frame["event"]
        )
        _validate(frame["payload"], component, schema, f"stream.{frame['event']}")


def test_fixture_errors_cover_the_closed_vocabulary(schema, fixtures):
    codes = schema["errors"]["codes"]
    fixture_codes = {e["body"]["detail"]["code"] for e in fixtures["errors"] if e["typed"]}
    assert set(codes) <= fixture_codes
    for entry in fixtures["errors"]:
        if entry["typed"]:
            assert entry["status"] in codes[entry["body"]["detail"]["code"]]
            assert set(entry["body"]["detail"]) == {"code", "message"}
    untyped = [e for e in fixtures["errors"] if not e["typed"]]
    assert any(e["status"] == 401 for e in untyped)
    assert any(isinstance(e["body"]["detail"], str) for e in untyped)


# ---------------------------------------------------------------------------
# Generated + legacy python-sdk models against the same fixtures
# ---------------------------------------------------------------------------


def test_generated_models_roundtrip_fixture_payloads(schema, fixtures, generated_models):
    components = schema["components"]
    sdk_names = {n for n, c in components.items() if c.get("x-sdk")}
    assert sdk_names <= set(vars(generated_models)), "generated layer misses x-sdk models"
    route_examples = fixtures["routes"]
    covered = 0
    for entry in fixtures["componentExamples"]:
        if entry["responseComponent"] not in sdk_names:
            continue
        covered += 1
        model = getattr(generated_models, entry["responseComponent"])
        comp = components[entry["responseComponent"]]
        example = comp["examples"][0]
        model.model_validate(example)
        route = route_examples.get(entry["route"])
        if route and "requestComponent" in entry and entry["requestComponent"] in sdk_names:
            request_model = getattr(generated_models, entry["requestComponent"])
            request_model.model_validate(route["requestExample"])
    assert covered >= 7, "generated-layer fixture coverage regressed"


def test_generated_stream_event_types_match_fixtures(schema, fixtures, generated_models):
    import typing

    events = set(typing.get_args(generated_models.STREAM_EVENT_NAMES))
    codes = set(typing.get_args(generated_models.BridgeErrorCode))
    fixture_events = {f["event"] for f in fixtures["stream"]["frames"]} - {"keep-alive"}
    assert fixture_events <= events
    assert set(schema["errors"]["codes"]) <= codes
    flags = schema["handshake"]["featureFlags"]["vocabulary"]
    assert flags == generated_models.BRIDGE_FEATURE_FLAGS


def _example_projected(dump, example):
    """Project a parsed dump onto the example's key structure — the wall
    compares what the wire carries, not client-side default filling for
    fields the payload omits."""
    if isinstance(example, dict):
        return {k: _example_projected(dump[k], v) for k, v in example.items() if k in dump}
    if isinstance(example, list):
        return [_example_projected(d, e) for d, e in zip(dump, example, strict=True)]
    return dump


def test_legacy_sdk_models_agree_with_generated(schema, fixtures, legacy_models, generated_models):
    """The hand-written layer is pinned to the schema-derived layer on every
    payload it models — neither may drift from the fixtures."""
    overlapping = {
        "BridgeStatus": legacy_models.BridgeStatus,
        "MetricMappingRequest": legacy_models.MetricMappingRequest,
        "MapRequest": legacy_models.MapRequestPayload,
        "MapResponse": legacy_models.MapResponsePayload,
        "MappedMetric": legacy_models.MappedMetric,
        "ClientRecord": legacy_models.ClientRecord,
        "ClientExaminationRecord": legacy_models.ClientExaminationRecord,
        "SyncRequest": legacy_models.SyncPayload,
        "SyncResponse": legacy_models.SyncResponse,
    }
    components = schema["components"]
    for name, legacy in overlapping.items():
        generated = getattr(generated_models, name)
        example = components[name]["examples"][0]
        legacy_out = _example_projected(legacy.model_validate(example).model_dump(), example)
        generated_out = _example_projected(generated.model_validate(example).model_dump(), example)
        assert legacy_out == generated_out, f"{name} diverged from the generated layer"
