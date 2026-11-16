"""The route gate — every route declares a policy or holds an allowlist entry.

The gate is a CI test (:mod:`tests.test_authz_route_gate`) plus a CLI
(``scripts/authz_inventory.py``). Rules:

1. **Coverage** — every enumerated route either declares a
   :class:`~app.core.authz.policy.RoutePolicy` on its endpoint or has an
   entry in :data:`INVENTORY_PATH`. A new route with neither fails the gate.
2. **No drift** — every entry must match exactly one enumerated route; an
   entry whose route is gone (renamed, method changed) fails the gate.
3. **Monotonic shrink** — ``retired`` records every removed entry key; an
   entry (or declaration-less resurrection) of a retired key fails the gate.
   Entries may only be removed, never added; the migration from entry to
   declared policy removes the entry and records it in ``retired``.
4. **Closed vocabulary** — entry ``route_class`` values are
   :class:`~app.core.authz.policy.RouteClass` members; the dependency each
   class implies is pinned in :data:`ROUTE_CLASS_DEPENDENCIES`.
5. **No redundancy** — a route that declares a policy must not also hold an
   entry; migrating a route means removing its entry (rule 3 applies).
6. **Loud classification** — the walk knows FastAPI's route types
   (``APIRoute``, ``APIWebSocketRoute``, Starlette ``Route``/``WebSocketRoute``)
   and lazy ``_IncludedRouter`` nodes; any other node type (a ``Mount``, a
   custom class) raises with the class name instead of being skipped into a
   silently incomplete inventory.
7. **Central resolution** — every route must resolve under the policy
   resolver (``route_policy.resolve_route_policy``): session routes to a
   permission + required scope (or an explicit self-service auth-only
   rule), token/public classes to their class with no permission, and the
   resolved class must equal the inventory class (ADR 0054, Phase 1b — the
   resolver is the declaration; the inventory is the class baseline).

Framework doc surfaces (``/docs``, ``/openapi.json``, ``/redoc`` and
``/docs/oauth2-redirect``) are excluded from enumeration: their presence is
an environment toggle, not an application policy.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Route, WebSocketRoute

from app.core.authz.policy import RouteClass, RoutePolicy, route_policy_of
from app.core.authz.route_policy import resolve_route_policy

INVENTORY_PATH = Path(__file__).resolve().parent / "route_inventory.json"

FRAMEWORK_ROUTE_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})

INVENTORY_SCHEMA_VERSION = 1

ROUTE_CLASS_DEPENDENCIES: dict[RouteClass, str] = {
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
"""The auth dependency each :class:`RouteClass` implies (closed pin).

Entry ``route_class`` values are not guesses: this table names the
dependency mechanism a route of that class must carry. The test
``tests.test_authz_route_gate`` pins it exhaustively, so a new class member
must declare its dependency here in the same change.
"""


@dataclass(frozen=True)
class EnumeratedRoute:
    """One route as FastAPI serves it (one row per HTTP method).

    The identification fields (method/path/name/module/declared) drive the
    gate; the introspection fields (``dependencies`` — the include-context
    router-level dependencies composed down the tree, ``dependant`` — the
    resolved endpoint dependency graph, ``endpoint`` — the handler) let
    tests verify the class against the actual mechanism without a second
    walker.
    """

    method: str
    path: str
    name: str
    module: str
    declared: RoutePolicy | None = None
    dependencies: tuple = ()
    dependant: object | None = None
    endpoint: object | None = None

    @property
    def key(self) -> str:
        """The stable inventory key: ``METHOD path`` (``WS path`` for sockets)."""
        return f"{self.method} {self.path}"

    @property
    def owner(self) -> str:
        """The owning endpoint module (last dotted segment of ``__module__``)."""
        return self.module.rsplit(".", 1)[-1]


def route_key(method: str, path: str) -> str:
    """Build the inventory key for a method/path pair."""
    return f"{method.upper()} {path}"


def load_inventory(path: Path = INVENTORY_PATH) -> dict:
    """Load and shape-check the inventory data file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") != INVENTORY_SCHEMA_VERSION:
        raise ValueError(f"inventory schema mismatch: {raw.get('schema')!r}")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not all(
        isinstance(item, dict) and "route" in item and "route_class" in item and "owner" in item
        for item in entries
    ):
        raise ValueError("inventory entries must be objects with route/route_class/owner")
    if not isinstance(raw.get("retired"), list):
        raise ValueError("inventory must carry a retired list (monotonic shrink)")
    return raw


def inventory_index(inventory: dict) -> dict[str, dict]:
    """Entry objects keyed by route key (last entry wins is a gate violation)."""
    return {entry["route"]: entry for entry in inventory["entries"]}


def enumerate_routes(routes: object) -> Iterator[EnumeratedRoute]:
    """Enumerate :class:`EnumeratedRoute` rows from the FastAPI route tree.

    Accepts the FastAPI ``app`` (or any router / plain route list). FastAPI
    (0.141.1 in this repo) keeps ``include_router`` results lazy as
    ``_IncludedRouter`` nodes whose served paths only exist under the
    accumulated include prefixes, so the walk composes
    ``include_context.prefix`` down the tree — and the include contexts'
    router-level ``dependencies`` with it, so each row carries the full
    served auth story. WebSocket routes never appear as effective contexts
    and are composed the same way, recorded with the ``WS`` pseudo-method.
    Any other node type is a gate bug — the walk raises with the node's
    class name instead of silently skipping it.
    Framework doc surfaces (``/docs``, ``/openapi.json``, ``/redoc``) are
    skipped — their presence is an environment toggle, not an application
    policy.
    """
    if hasattr(routes, "routes"):
        routes = routes.routes
    yield from _walk_routes(list(routes), "")


def _walk_routes(
    routes: list[object], prefix: str, inherited_dependencies: tuple = ()
) -> Iterator[EnumeratedRoute]:
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            child_prefix = prefix + (route.include_context.prefix or "")
            child_dependencies = inherited_dependencies + tuple(
                route.include_context.dependencies or ()
            )
            yield from _walk_routes(
                list(route.original_router.routes), child_prefix, child_dependencies
            )
            continue
        if not isinstance(route, (APIRoute, APIWebSocketRoute, Route, WebSocketRoute)):
            raise TypeError(
                f"route gate cannot classify route node {type(route).__name__!r} — "
                "extend _walk_routes for it explicitly"
            )
        if route.path in FRAMEWORK_ROUTE_PATHS:
            continue
        endpoint = route.endpoint
        declared = route_policy_of(endpoint) if endpoint is not None else None
        module = _module_of(endpoint)
        name = route.name or ""
        methods = getattr(route, "methods", None)
        if not methods:
            yield EnumeratedRoute(
                method="WS",
                path=prefix + route.path,
                name=name,
                module=module,
                declared=declared,
                dependencies=inherited_dependencies,
                dependant=getattr(route, "dependant", None),
                endpoint=endpoint,
            )
            continue
        for method in sorted(methods):
            yield EnumeratedRoute(
                method=method,
                path=prefix + route.path,
                name=name,
                module=module,
                declared=declared,
                dependencies=inherited_dependencies,
                dependant=getattr(route, "dependant", None),
                endpoint=endpoint,
            )


def _module_of(endpoint: object) -> str:
    return getattr(endpoint, "__module__", "") or ""


def check_inventory(
    enumerated: Iterable[EnumeratedRoute],
    inventory: dict,
) -> list[str]:
    """Run all gate rules; return human-readable violations (empty = green).

    Pure: takes the enumerated routes and the parsed inventory, returns
    findings. The test and the CLI share this so they can never disagree.

    Beyond the allowlist rules above, every route must also resolve under
    the central policy resolver (``route_policy.resolve_route_policy``) —
    the declaration of record since Phase 1b (ADR 0054): session routes
    resolve to a permission + scope (or an explicit self-service auth-only
    rule), token/public classes to their class with no permission, and a
    declared ``@policy`` may never attach a permission to a non-session
    class.
    """
    violations: list[str] = []
    entries = inventory_index(inventory)
    retired = set(inventory["retired"])

    seen_keys: set[str] = set()
    for route in enumerated:
        seen_keys.add(route.key)
        declared = route.declared
        entry = entries.get(route.key)
        if declared is None and entry is None:
            violations.append(
                f"undeclared route: {route.key} (owner module {route.owner or '?'}) — "
                "declare @policy(...) or add a reviewed inventory entry"
            )
            continue
        if declared is not None and entry is not None:
            violations.append(
                f"redundant entry: {route.key} declares a policy and still holds an "
                "inventory entry — remove the entry (record it in retired)"
            )
        if declared is not None:
            if route.key in retired:
                violations.append(
                    f"retired route re-declared: {route.key} was removed from the "
                    "allowlist and may not come back without a reviewed ADR change"
                )
            violations.extend(_declared_policy_violations(route))
            if entry is None:
                continue
        assert entry is not None
        route_class = entry.get("route_class")
        if route_class not in RouteClass:
            violations.append(
                f"unknown route_class {route_class!r} for {route.key} — "
                f"expected one of {sorted(cls.value for cls in RouteClass)}"
            )
        if route.key in retired:
            violations.append(f"retired entry resurrected: {route.key}")
        if not entry.get("owner"):
            violations.append(f"entry without owner: {route.key}")
        violations.extend(_policy_violations(route, route_class))

    for key, _entry in entries.items():
        if key not in seen_keys:
            violations.append(
                f"stale entry: {key} matches no route (renamed, removed, or method "
                "changed) — remove it and record the key in retired"
            )

    seen: set[str] = set()
    for row in inventory["entries"]:
        key = row["route"]
        if key in seen:
            violations.append(f"duplicate entry: {key}")
        seen.add(key)

    return violations


def _declared_policy_violations(route: EnumeratedRoute) -> list[str]:
    """Checks that apply to a route carrying declared ``@policy`` metadata."""
    declared = route.declared
    if declared is None:
        return []
    failures: list[str] = []
    if declared.permission is not None and declared.route_class is not RouteClass.SESSION:
        failures.append(
            f"declared permission on a non-session class: {route.key} declares "
            f"{declared.permission.value} under {declared.route_class.value!r}"
        )
    if declared.permission is not None and declared.scope is None:
        failures.append(
            f"declared permission without scope: {route.key} requires "
            f"{declared.permission.value} — declare the required AccessScope"
        )
    return failures


def _policy_violations(route: EnumeratedRoute, route_class: object) -> list[str]:
    """The central-resolver checks for one route (ADR 0054, Phase 1b)."""
    if route_class not in RouteClass:
        return []  # reported by the closed-vocabulary rule above
    policy = resolve_route_policy(route.method, route.path, route_class)
    if policy is None:
        return [
            f"unresolved policy: {route.key} (class {route_class!r}) — no central "
            "rule in app/core/authz/route_policy.py; add one or fix the class"
        ]
    failures: list[str] = []
    if policy.route_class.value != route_class:
        failures.append(
            f"policy class drift: {route.key} resolved {policy.route_class.value!r} "
            f"but the inventory says {route_class!r}"
        )
    if route_class == RouteClass.SESSION:
        if policy.permission is None:
            if policy.scope is not None:
                failures.append(
                    f"auth-only policy with a scope: {route.key} — self-service "
                    "session routes carry neither permission nor scope"
                )
        elif policy.scope is None:
            failures.append(
                f"permission without scope: {route.key} requires {policy.permission.value} "
                "— declare the required AccessScope"
            )
    elif policy.permission is not None:
        failures.append(
            f"permission on a non-session class: {route.key} is {route_class!r} but "
            f"carries {policy.permission.value} — token/public classes have no permission"
        )
    return failures


def declared_policy_routes(enumerated: Iterable[EnumeratedRoute]) -> list[EnumeratedRoute]:
    """Routes that declare their own policy (migration progress signal)."""
    return [route for route in enumerated if route.declared is not None]
