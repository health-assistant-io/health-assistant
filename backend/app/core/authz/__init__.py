"""The authorization foundation — permission catalog, roles, scopes, policies.

Layer model (ADR 0054, all layers are code, none are DB rows in v1):

1. :mod:`permissions` — the closed ``resource:action`` vocabulary.
2. :mod:`roles`       — the fixed role → permission defaults.
3. :mod:`scopes`      — the access-scope taxonomy (system/tenant/own/grant/break-glass).
4. :mod:`policy`      — per-route policy metadata (declared, not yet enforced).
5. :mod:`route_gate`  — the inventory gate: every route declares or is allowlisted.

Nothing here performs an authorization decision at runtime yet; the route-gate
rollout (plan D1b/D1c) wires enforcement without changing Phase-1 behavior.
"""

from app.core.authz.permissions import Permission
from app.core.authz.policy import RouteClass, RoutePolicy, policy, route_policy_of
from app.core.authz.roles import ROLE_PERMISSIONS, permissions_for, role_has, roles_with
from app.core.authz.scopes import AccessScope, scope_covers

__all__ = [
    "ROLE_PERMISSIONS",
    "AccessScope",
    "Permission",
    "RouteClass",
    "RoutePolicy",
    "permissions_for",
    "policy",
    "role_has",
    "roles_with",
    "route_policy_of",
    "scope_covers",
]
