"""The central route policy resolver — completeness, class match, stability.

ADR 0054 Phase 1b: ``route_policy.resolve_route_policy`` is the declaration
of record. These tests pin, over the real FastAPI app:

1. every enumerated route resolves (the resolver is complete);
2. the resolved class equals the inventory class (no drift);
3. session routes resolve to a permission + required scope, or to an
   explicit auth-only self-service rule;
4. non-session classes never carry a permission;
5. an unknown route/class fails closed (``None`` = deny);
6. the generated review table (``docs/dev/AUTHZ_ROUTE_POLICIES.md``) is
   stable — the file on disk equals a fresh render.
"""

import pytest

from app.core.authz.permissions import Permission
from app.core.authz.policy import RouteClass, policy, route_policy_of
from app.core.authz.roles import permissions_for, role_has, scope_rule
from app.core.authz.route_gate import (
    EnumeratedRoute,
    check_inventory,
    enumerate_routes,
    load_inventory,
)
from app.core.authz.route_policy import (
    RULES,
    resolve_route_policy,
    resolve_route_rule,
)
from app.core.authz.scopes import AccessScope, scope_covers
from app.models.enums import Role
from scripts.authz_inventory import POLICIES_PATH, render_policies_document


@pytest.fixture(scope="module")
def enumerated() -> list[EnumeratedRoute]:
    from app.main import app

    return list(enumerate_routes(app))


@pytest.fixture(scope="module")
def inventory() -> dict:
    return load_inventory()


def _class_of(route: EnumeratedRoute, inventory: dict) -> str:
    if route.declared is not None:
        return route.declared.route_class.value
    entries = {entry["route"]: entry for entry in inventory["entries"]}
    return entries[route.key]["route_class"]


def test_every_route_resolves_with_its_inventory_class(enumerated, inventory):
    """The resolver is complete: no route may resolve to None, and the
    resolved class must equal the reviewed inventory class."""
    failures = []
    for route in enumerated:
        route_class = _class_of(route, inventory)
        resolved = resolve_route_policy(route.method, route.path, route_class)
        if resolved is None:
            failures.append(f"{route.key}: unresolved (class {route_class}) — would deny")
        elif resolved.route_class.value != route_class:
            failures.append(
                f"{route.key}: resolved {resolved.route_class.value}, inventory {route_class}"
            )
    assert failures == [], "\n".join(failures)


def test_session_routes_enforce_a_permission_and_scope_or_are_auth_only(enumerated, inventory):
    """Every session route either carries permission+scope at the gate or
    matches an explicit auth-only self-service rule — never a bare skip."""
    failures = []
    for route in enumerated:
        if _class_of(route, inventory) != RouteClass.SESSION:
            continue
        rule = resolve_route_rule(route.method, route.path, RouteClass.SESSION)
        resolved = resolve_route_policy(route.method, route.path, RouteClass.SESSION)
        if resolved.permission is None:
            if rule is None or not rule.auth_only:
                failures.append(
                    f"{route.key}: permission-less session route without an explicit rule"
                )
            if resolved.scope is not None:
                failures.append(f"{route.key}: auth-only route carries a scope")
        elif resolved.scope is None:
            failures.append(f"{route.key}: {resolved.permission.value} without a scope")
    assert failures == [], "\n".join(failures)


def test_non_session_classes_never_carry_a_permission(enumerated, inventory):
    """Token principals and public/auth surfaces are enforced by their own
    mechanisms — a permission there would silently double-gate or miss."""
    for route in enumerated:
        route_class = _class_of(route, inventory)
        if route_class == RouteClass.SESSION:
            continue
        resolved = resolve_route_policy(route.method, route.path, route_class)
        assert resolved is not None, route.key
        assert resolved.permission is None, f"{route.key}: {resolved.permission} on {route_class}"


def test_unknown_session_route_fails_closed():
    """A session path with no rule resolves to nothing — the gate denies."""
    assert resolve_route_policy("GET", "/api/v1/does-not-exist", RouteClass.SESSION) is None
    assert (
        resolve_route_policy("GET", "/api/v1/patients/{patient_id}/unknown-sub", RouteClass.SESSION)
        is None
    )
    assert resolve_route_policy("GET", "/api/v1/patients", None) is None


def test_unknown_class_fails_closed():
    assert resolve_route_policy("GET", "/api/v1/patients", "carrier_pigeon") is None


def test_head_requests_are_judged_as_get():
    """HEAD normalizes to GET so a probe cannot bypass method-group rules."""
    read = resolve_route_policy("GET", "/api/v1/patients/{patient_id}", RouteClass.SESSION)
    head = resolve_route_policy("HEAD", "/api/v1/patients/{patient_id}", RouteClass.SESSION)
    assert head is not None and read is not None
    assert head.permission == read.permission
    assert head.scope == read.scope


def test_api_prefixes_are_stripped_for_v1_and_v2():
    for prefix in ("/api/v1", "/api/v2"):
        resolved = resolve_route_policy("GET", f"{prefix}/patients", RouteClass.SESSION)
        assert resolved is not None
        assert resolved.permission.value == "patient:read"
        assert resolved.scope.value == "own"


def test_every_rule_name_is_unique():
    """Rule names label the generated table — duplicates would be ambiguous."""
    names = [rule.name for rule in RULES]
    assert len(names) == len(set(names))


def test_list_vs_patient_scoped_split_is_pinned():
    """The reviewed matrix split (Phase 6B correction): the collection list
    needs only OWN (the handler filters USER to ``Patient.user_id``; ADMIN/
    MANAGER hold the permission at tenant, which covers own) while the
    patient-scoped detail keeps GRANT (the resource layer owns the rows)."""
    collection = resolve_route_policy("GET", "/api/v1/patients", RouteClass.SESSION)
    assert (collection.permission.value, collection.scope.value) == ("patient:read", "own")
    detail = resolve_route_policy("GET", "/api/v1/patients/{patient_id}", RouteClass.SESSION)
    assert (detail.permission.value, detail.scope.value) == ("patient:read", "grant")
    assert collection.scope.value == "own" and detail.scope.value == "grant"


def test_role_matrix_predictions_are_self_consistent():
    """For every rule: SYSTEM_ADMIN satisfies every session policy, USER
    satisfies only GRANT/OWN ones it holds — the resolver stays inside the
    role defaults by construction."""
    for rule in RULES:
        if rule.route_class is not RouteClass.SESSION or rule.auth_only:
            continue
        for group in (rule.read, rule.write, rule.delete):
            if group is None:
                continue
            permission, required = group
            assert role_satisfies(Role.SYSTEM_ADMIN, permission, required), rule.name
            if required in (AccessScope.GRANT, AccessScope.OWN):
                assert role_satisfies(Role.USER, permission, required) == (
                    permission in permissions_for(Role.USER)
                ), rule.name


def role_satisfies(role: Role, permission: Permission, required: AccessScope) -> bool:
    """Whether ``role`` holds ``permission`` at a scope covering ``required``."""
    if not role_has(role, permission):
        return False
    granted = scope_rule(role, permission)
    return granted is not None and scope_covers(required, granted)


def test_generated_policies_document_is_stable(enumerated, inventory):
    """The review artifact on disk equals a fresh render — the table drifts
    only when the resolver or the inventory does, never by accident."""
    rendered = render_policies_document(enumerated, inventory)
    on_disk = POLICIES_PATH.read_text(encoding="utf-8")
    assert on_disk == rendered


def test_gate_flags_an_unresolved_policy():
    """A session inventory entry whose path no rule covers fails the gate."""
    enumerated = [EnumeratedRoute(method="GET", path="/api/v1/brand-new", name="n", module="m")]
    inventory = {
        "schema": 1,
        "entries": [{"route": "GET /api/v1/brand-new", "route_class": "session", "owner": "m"}],
        "retired": [],
    }
    violations = check_inventory(enumerated, inventory)
    assert any(v.startswith("unresolved policy: GET /api/v1/brand-new") for v in violations)


def test_gate_flags_class_drift():
    """A route whose inventory class contradicts the resolver fails the gate.

    ``GET /health`` is public; filing it as session makes the resolver's
    class-default path disagree with the inventory row.
    """
    enumerated = [EnumeratedRoute(method="GET", path="/health", name="h", module="main")]
    inventory = {
        "schema": 1,
        "entries": [{"route": "GET /health", "route_class": "session", "owner": "main"}],
        "retired": [],
    }
    violations = check_inventory(enumerated, inventory)
    assert any("unresolved policy: GET /health" in v for v in violations)


def test_gate_flags_a_declared_permission_on_a_token_class():
    """``@policy(TOKEN_FHIR, permission=...)`` is a declaration bug."""
    endpoint = policy(RouteClass.TOKEN_FHIR, permission=Permission.PATIENT_READ)(lambda: None)
    enumerated = [
        EnumeratedRoute(
            method="GET",
            path="/api/v1/fhir/R4/Patient",
            name="f",
            module="fhir_r4",
            declared=route_policy_of(endpoint),
        )
    ]
    inventory = {"schema": 1, "entries": [], "retired": []}
    violations = check_inventory(enumerated, inventory)
    assert any("declared permission on a non-session class" in v for v in violations)
