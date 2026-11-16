"""The access-scope taxonomy — how far a permission reaches in this request.

Scopes (ADR 0054):

- ``system``     — across all tenants (SYSTEM_ADMIN, service jobs).
- ``tenant``     — everywhere inside the caller's tenant (ADMIN; MANAGER with
  ``manager_scope=tenant``, the default).
- ``own``        — resources the caller owns (``Patient.user_id == caller``).
- ``grant``      — resources reachable through an active care-team grant.
- ``break_glass``— a time-boxed, audited elevation recorded in
  ``access_elevations`` (ADR 0054 D1); it widens the effective scope of the
  held permission for its duration, it is never a permission of its own.

The lattice is linear for containment purposes:
``grant ⊂ own ⊂ tenant ⊂ system`` and break-glass dominates while active.
"""

from __future__ import annotations

import enum


class AccessScope(enum.StrEnum):
    """The reach of an authorization decision (closed set)."""

    GRANT = "grant"
    OWN = "own"
    TENANT = "tenant"
    SYSTEM = "system"
    BREAK_GLASS = "break_glass"


_SCOPE_RANK: dict[AccessScope, int] = {
    AccessScope.GRANT: 0,
    AccessScope.OWN: 1,
    AccessScope.TENANT: 2,
    AccessScope.SYSTEM: 3,
    AccessScope.BREAK_GLASS: 4,
}


def scope_covers(required: AccessScope, granted: AccessScope) -> bool:
    """Whether a held scope satisfies a required scope.

    ``system`` covers everything below it; ``break_glass`` dominates every
    scope while the elevation is active (the elevation service — not this
    helper — owns expiry and the audit trail).
    """
    return _SCOPE_RANK[granted] >= _SCOPE_RANK[required]


def cap_scope(granted: AccessScope, cap: AccessScope) -> AccessScope:
    """``granted`` narrowed to at most ``cap`` (never widened).

    Used by scoped machine credentials: a personal access token inherits
    the owner's scope but may never outrank its cap (tenant), so a token
    minted by a SYSTEM_ADMIN still cannot reach the system actor.
    """
    return cap if _SCOPE_RANK[granted] > _SCOPE_RANK[cap] else granted


def effective_scope(base: AccessScope, elevated: bool) -> AccessScope:
    """The scope a request actually carries.

    A break-glass elevation widens the caller's base scope to ``system``
    reach for its duration; without one the base scope stands.
    """
    return AccessScope.BREAK_GLASS if elevated else base
