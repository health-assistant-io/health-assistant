"""The route gate — inventory coverage, drift, and monotonic shrink (ADR 0054).

Enumerates the real FastAPI app and asserts:

1. every route declares a policy or holds a reviewed inventory entry;
2. no stale entries (an entry whose route vanished fails CI);
3. no retired key returns (entries can only be removed, never re-added);
4. entry classes stay inside the closed :class:`RouteClass` vocabulary;
5. a declared policy never keeps a redundant allowlist entry;
6. the actual served dependency graph/mechanism matches the entry's class
   (the introspection gate — the /auth/validate misclassification class).
"""

import inspect
import re

import pytest

from app.core.authz.policy import RouteClass, route_policy_of
from app.core.authz.route_gate import (
    ROUTE_CLASS_DEPENDENCIES,
    EnumeratedRoute,
    _walk_routes,
    check_inventory,
    enumerate_routes,
    load_inventory,
)


@pytest.fixture(scope="module")
def enumerated() -> list[EnumeratedRoute]:
    from app.main import app

    return list(enumerate_routes(app))


@pytest.fixture(scope="module")
def inventory() -> dict:
    return load_inventory()


def test_inventory_carries_every_route(enumerated, inventory):
    violations = check_inventory(enumerated, inventory)
    assert violations == [], "\n".join(violations)


def test_inventory_has_no_stale_entries(enumerated, inventory):
    live = {route.key for route in enumerated}
    stale = [entry["route"] for entry in inventory["entries"] if entry["route"] not in live]
    assert stale == []


def test_retired_keys_never_return(enumerated, inventory):
    retired = set(inventory["retired"])
    entry_keys = {entry["route"] for entry in inventory["entries"]}
    assert retired & entry_keys == set()
    for key in retired:
        method, _, path = key.partition(" ")
        assert method and path.startswith("/")


def test_entry_classes_are_closed_vocabulary(inventory):
    vocabulary = {cls.value for cls in RouteClass}
    for entry in inventory["entries"]:
        assert entry["route_class"] in vocabulary, entry
        assert entry["owner"], entry


def test_special_routes_are_pinned_to_their_principal_classes(inventory):
    entries = {entry["route"]: entry["route_class"] for entry in inventory["entries"]}
    assert entries["GET /health"] == "public"
    assert entries["POST /api/v1/oauth/token"] == "token_oauth"
    assert entries["GET /api/v1/fhir/R4/{resource_type}"] == "token_fhir"
    assert entries["GET /api/v1/documents/{document_id}/download"] == "token_presign"
    assert (
        entries["POST /api/v1/integrations/{domain}/webhook/{integration_id}"] == "tokenless_hmac"
    )
    assert entries["WS /api/v1/ws/tasks"] == "websocket"
    assert entries["POST /api/v1/auth/login"] == "public_auth"
    # Passkey sign-in and the OIDC entry points are auth bootstrap, not
    # session: a re-generated inventory must keep them public_auth.
    assert entries["POST /api/v1/auth/passkeys/options"] == "public_auth"
    assert entries["POST /api/v1/auth/passkeys/verify"] == "public_auth"


def test_public_auth_bootstrap_paths_stay_public_on_regeneration():
    """The proposal heuristic (what ``generate`` writes) must classify them.

    An unauthenticated passkey assertion *is* the sign-in, so a heuristic
    that fell through to ``session`` would generate an inventory entry the
    gate then demands a session dependency for — and the login would be
    broken by a "fix".
    """
    from scripts.authz_inventory import _proposed_class

    def route(method: str, path: str) -> EnumeratedRoute:
        return EnumeratedRoute(method=method, path=path, name="probe", module="probe")

    public_auth = [
        ("POST", "/api/v1/auth/passkeys/options"),
        ("POST", "/api/v1/auth/passkeys/verify"),
        ("POST", "/api/v1/auth/sso/status"),
        ("POST", "/api/v1/auth/sso/authorize"),
        ("GET", "/api/v1/auth/sso/callback"),
    ]
    for method, path in public_auth:
        assert _proposed_class(route(method, path)) is RouteClass.PUBLIC_AUTH, (method, path)

    # The passkey *step-up* ceremony is a session route (it needs a family).
    assert _proposed_class(route("POST", "/api/v1/auth/step-up/passkeys/verify")) is (
        RouteClass.SESSION
    )


def test_unauthenticated_data_routes_are_public(inventory):
    """S1 follow-ups: routes the introspection gate proved carry no auth
    mechanism are `public`, never a credential-adjacent class. `/config/public`
    (non-credential instance metadata), the BYOK preset surface ("public
    metadata only" per its contract), the closed edge vocabulary, the VAPID
    public key and the FHIR CapabilityStatement ("no auth — spec
    requirement") are data/metadata, and `POST /auth/mfa/enroll` is the
    challenge-token auth bootstrap (public_auth), not a session route."""
    entries = {entry["route"]: entry["route_class"] for entry in inventory["entries"]}
    for route in (
        "GET /api/v1/config/public",
        "GET /api/v1/ai-config/provider-presets",
        "GET /api/v1/concept-edges/schema",
        "GET /api/v1/notifications/vapid-public-key",
        "GET /api/v1/fhir/R4/metadata",
    ):
        assert entries[route] == "public", route
    assert entries["POST /api/v1/auth/mfa/enroll"] == "public_auth"


def test_auth_validate_is_a_session_route(inventory):
    """S1: /auth/validate reads the caller's session claims
    (``Depends(get_current_user)``) — it is session, never public_auth."""
    entries = {entry["route"]: entry["route_class"] for entry in inventory["entries"]}
    assert entries["GET /api/v1/auth/validate"] == "session"


def test_every_route_class_maps_to_its_expected_dependency():
    """The class → auth-dependency pin is exhaustive and exact, so a new
    inventory entry is reviewed against the dependency it implies."""
    expected = {
        RouteClass.SESSION: "get_current_user",
        RouteClass.PUBLIC_AUTH: "(none — unauthenticated, rate-limited auth bootstrap)",
        RouteClass.PUBLIC: "(none — unauthenticated metadata)",
        RouteClass.TOKEN_FHIR: "get_api_principal + require_fhir_scopes",
        RouteClass.TOKEN_OAUTH: "OAuth2 client credentials (client secret)",
        RouteClass.TOKEN_PRESIGN: "verify_presigned_token",
        RouteClass.TOKENLESS_HMAC: "verify_hmac_signature",
        RouteClass.TOKEN_SCIM: "get_scim_principal (tenant-bounded hashed SCIM bearer)",
        RouteClass.WEBSOCKET: "get_session_user_ws",
    }
    assert expected == ROUTE_CLASS_DEPENDENCIES
    assert set(ROUTE_CLASS_DEPENDENCIES) == set(RouteClass)


def test_duplicate_entries_fail_the_gate():
    """Two identical entries are a data bug: the gate reports the duplicate."""
    enumerated = [EnumeratedRoute(method="GET", path="/x", name="x", module="m")]
    inventory = {
        "schema": 1,
        "entries": [
            {"route": "GET /x", "route_class": "session", "owner": "m"},
            {"route": "GET /x", "route_class": "session", "owner": "m"},
        ],
        "retired": [],
    }
    violations = check_inventory(enumerated, inventory)
    assert any(v.startswith("duplicate entry: GET /x") for v in violations)


def test_walker_raises_on_unknown_route_nodes():
    """An unclassifiable node type stops the walk loudly (class name in the
    error) instead of being skipped into an incomplete inventory."""

    class SomethingNew:
        pass

    with pytest.raises(TypeError, match="SomethingNew"):
        list(_walk_routes([SomethingNew()], ""))


def test_websocket_and_http_routes_compose_nested_include_prefixes():
    """WS and HTTP routes under nested ``include_router`` prefixes get their
    served paths composed down the tree (WS keeps the ``WS`` pseudo-method)."""
    from fastapi import APIRouter, FastAPI, WebSocket

    inner = APIRouter()

    @inner.websocket("/ws/inner")
    async def inner_ws(websocket: WebSocket):
        await websocket.accept()

    @inner.get("/inner/thing")
    async def inner_thing():
        return {}

    middle = APIRouter(prefix="/nested")
    middle.include_router(inner, prefix="/layer")
    app = FastAPI()
    app.include_router(middle, prefix="/api/v2")

    keys = {route.key for route in enumerate_routes(app)}

    assert "WS /api/v2/nested/layer/ws/inner" in keys
    assert "GET /api/v2/nested/layer/inner/thing" in keys


def test_gate_flags_undeclared_and_stale_and_retired():
    enumerated = [
        EnumeratedRoute(method="GET", path="/x", name="x", module="m"),
        EnumeratedRoute(method="GET", path="/z", name="z", module="m"),
    ]
    inventory = {
        "schema": 1,
        "entries": [
            {"route": "GET /y", "route_class": "session", "owner": "m"},
            {"route": "GET /z", "route_class": "session", "owner": "m"},
        ],
        "retired": ["GET /z"],
    }
    violations = check_inventory(enumerated, inventory)
    assert any(v.startswith("undeclared route: GET /x") for v in violations)
    assert any(v.startswith("stale entry: GET /y") for v in violations)
    assert any(v.startswith("retired entry resurrected: GET /z") for v in violations)


def test_gate_flags_unknown_class_and_redundant_entry():
    from app.core.authz.policy import policy

    declared_endpoint = policy(RouteClass.SESSION)(lambda: None)
    enumerated = [
        EnumeratedRoute(method="GET", path="/a", name="a", module="m"),
        EnumeratedRoute(
            method="GET",
            path="/b",
            name="b",
            module="m",
            declared=route_policy_of(declared_endpoint),
        ),
    ]
    inventory = {
        "schema": 1,
        "entries": [
            {"route": "GET /a", "route_class": "session", "owner": "m"},
            {"route": "GET /b", "route_class": "carrier_pigeon", "owner": "m"},
        ],
        "retired": [],
    }
    violations = check_inventory(enumerated, inventory)
    assert any("unknown route_class" in v for v in violations)
    assert any(v.startswith("redundant entry: GET /b") for v in violations)


def test_enumeration_excludes_framework_surfaces():
    from app.main import app

    keys = {route.key for route in enumerate_routes(app)}
    assert "/docs" not in {key.split(" ", 1)[1] for key in keys}
    assert any(key.startswith("GET /api/v1/") for key in keys)


# --- Dependency introspection (the /auth/validate misclassification class) ---

#: Dependency names that authenticate the caller, as FastAPI dependencies.
_AUTH_DEPENDENCIES = frozenset(
    {
        "get_current_user",
        "get_api_principal",
        "get_session_user_ws",
    }
)

#: In-handler session mechanism (documents preview resolves the bearer
#: itself instead of carrying ``get_current_user``).
_IN_HANDLER_SESSION = "authenticate_session_token"

#: HMAC verification tokens (bare body-MAC and the canonical replay-proof
#: scheme), possibly behind a module-level wrapper.
_HMAC_TOKENS = ("verify_hmac_signature", "verify_canonical_signature")


def _source_of(fn: object) -> str:
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        return ""


def _introspection_sources(route: EnumeratedRoute) -> str:
    """Endpoint source + dependency sources + one level of module helpers.

    The helper level is what sees a route delegating its mechanism to a
    module function (the webhook's ``_verify_webhook_signature`` wraps
    ``verify_hmac_signature``); it stays shallow so a mechanism mentioned
    anywhere in a big module cannot vouch for a route that never reaches it.
    """
    dependency_sources: list[str] = []
    if route.dependant is not None:
        stack = [route.dependant]
        while stack:
            current = stack.pop()
            for dependency in current.dependencies:
                dependency_sources.append(_source_of(dependency.call))
                stack.append(dependency)
    endpoint = route.endpoint
    if endpoint is None:
        return "\n".join(dependency_sources)
    endpoint_source = _source_of(endpoint)
    module = inspect.getmodule(endpoint)
    helpers: list[str] = []
    if module is not None:
        for name in set(re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", endpoint_source)):
            candidate = getattr(module, name, None)
            if callable(candidate) and getattr(candidate, "__module__", None) == module.__name__:
                helpers.append(_source_of(candidate))
    return "\n".join([endpoint_source, *dependency_sources, *helpers])


def _dependency_names(route: EnumeratedRoute) -> set[str]:
    """Every dependency call name the route serves, router-level included."""
    names: set[str] = set()
    if route.dependant is not None:
        stack = [route.dependant]
        while stack:
            current = stack.pop()
            for dependency in current.dependencies:
                names.add(getattr(dependency.call, "__name__", str(dependency.call)))
                stack.append(dependency)
    for dependency in route.dependencies:
        names.add(getattr(dependency.dependency, "__name__", str(dependency.dependency)))
    return names


def _class_of(route: EnumeratedRoute, inventory: dict) -> str:
    declared = route_policy_of(route.endpoint) if route.endpoint else None
    if declared is not None:
        return declared.route_class.value
    entry = {item["route"]: item for item in inventory["entries"]}.get(route.key)
    return entry["route_class"] if entry else "unclassified"


def _introspection_failures(enumerated: list[EnumeratedRoute], inventory: dict) -> list[str]:
    """The introspection gate body — every class ↔ mechanism rule in one place.

    Returns one line per violating route (empty = green).
    """
    failures: list[str] = []
    for route in enumerated:
        route_class = _class_of(route, inventory)
        names = _dependency_names(route)
        source = _introspection_sources(route)
        if route_class == "session":
            ok = "get_current_user" in names or _IN_HANDLER_SESSION in source
            mechanism = "get_current_user reachable or in-handler session resolution"
        elif route_class in ("public", "public_auth"):
            ok = not (names & _AUTH_DEPENDENCIES)
            mechanism = "no auth dependency"
        elif route_class == "token_fhir":
            ok = "get_api_principal" in names
            mechanism = "get_api_principal (require_fhir_scopes resolves it)"
        elif route_class == "token_oauth":
            ok = "_extract_client_credentials" in source or "get_current_user" in names
            mechanism = "client-credentials exchange or session-identified revocation"
        elif route_class == "token_presign":
            ok = "verify_presigned_token" in source
            mechanism = "verify_presigned_token"
        elif route_class == "tokenless_hmac":
            ok = any(token in source for token in _HMAC_TOKENS)
            mechanism = "HMAC signature verification"
        elif route_class == "token_scim":
            ok = "get_scim_principal" in names or "get_scim_principal" in source
            mechanism = "get_scim_principal (SCIM bearer resolution)"
        elif route_class == "websocket":
            ok = route.key.startswith("WS ") and "get_session_user_ws" in source
            mechanism = "get_session_user_ws over the WS handshake"
        else:
            failures.append(f"{route.key}: unknown class {route_class!r}")
            continue
        if not ok:
            failures.append(f"{route.key}: classified {route_class!r} but {mechanism} is missing")
    return failures


def test_every_route_serves_the_mechanism_its_class_implies(enumerated, inventory):
    """The introspection gate — the class ↔ mechanism pin, executed.

    For every inventory entry, walk the actual served dependency graph (the
    composed router-level dependencies included) and the handler's source,
    and assert the class's auth mechanism is really there — session routes
    authenticate (``get_current_user`` or the in-handler session
    resolution), unauthenticated classes carry no auth dependency, and each
    token class serves its own mechanism. Failing names the offending route.

    ``token_oauth`` has two honest shapes: the client-credentials exchange
    validates the client secret in-handler (no auth dependency), and RFC
    7009 ``/oauth/revoke`` resolves the caller via ``get_current_user`` to
    scope the revocation.
    """
    failures = _introspection_failures(enumerated, inventory)
    assert failures == [], (
        "route classes must match the served auth mechanism "
        "(reclassify the route or fix its dependencies):\n" + "\n".join(failures)
    )


def test_introspection_gate_catches_a_session_route_without_mechanism(enumerated, inventory):
    """Non-vacuity: a session-classified row with no auth mechanism at all
    must fail the gate with that route named (config/public was exactly
    this before its reclassification)."""
    fake = [
        *enumerated,
        EnumeratedRoute(
            method="GET",
            path="/api/v1/fake/anonymous",
            name="fake",
            module="fake_module",
            declared=None,
        ),
    ]
    entry_rows = [*inventory["entries"]]
    entry_rows.append(
        {"route": "GET /api/v1/fake/anonymous", "route_class": "session", "owner": "x"}
    )
    failures = _introspection_failures(fake, {"entries": entry_rows})
    assert any("GET /api/v1/fake/anonymous" in failure for failure in failures)
