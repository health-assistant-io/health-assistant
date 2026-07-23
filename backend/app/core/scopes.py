"""SMART-on-FHIR scope parsing and validation.

Scope syntax: ``<context>/<resource>.<permission>`` where
* ``context``    ∈ {``system``, ``patient``, ``user``}
* ``resource``   ∈ any registered FHIR resource type, or ``*``
* ``permission`` ∈ {``read``, ``write``, ``*``}

Today the facade serves **backend service flows** via the OAuth2
client-credentials grant, so only the ``system/`` and ``patient/`` contexts
are registrable. The ``user/`` context and the ``launch``* / OpenID scopes
are tied to the interactive authorize flow (Stage 4) and are rejected at
registration.

See ``docs/FHIR_R4_FACADE.md`` (Authentication & SMART scopes).
"""
from __future__ import annotations

import re
from typing import Iterable

# ``<context>/<resource>.<permission>`` — resource is ``*`` or a PascalCase
# / mixed identifier (e.g. ``Observation``, ``MedicationRequest``).
_SCOPE_RE = re.compile(
    r"^(system|patient|user)/(\*|[A-Z][A-Za-z0-9]*)\.(read|write|\*)$"
)

# Contexts a client may register today. ``user`` requires the deferred
# authorize flow; ``launch``* / OpenID scopes are not part of the
# client-credentials model.
_REGISTRABLE_CONTEXTS = {"system", "patient"}

# Scopes that look like SMART/OIDC but are not resource scopes (and are not
# supported under client-credentials yet). Rejected with a clear message so a
# caller knows *why*, not just "invalid".
_UNSUPPORTED_SCOPES = {
    "openid",
    "fhirUser",
    "offline_access",
    "launch",
    "launch/patient",
}


class InvalidScopeError(ValueError):
    """Raised when a scope string is malformed or uses an unsupported context."""


def is_valid_scope(scope: str) -> bool:
    return bool(_SCOPE_RE.match(scope))


def parse_scopes(scope_str: str | None) -> set[str]:
    """Parse a space-separated scope string into a set."""
    if not scope_str:
        return set()
    return {s for s in scope_str.split() if s}


def validate_registrable_scopes(scopes: Iterable[str]) -> list[str]:
    """Validate + normalize scopes a client may be granted at registration.

    Returns the deduped, order-stable list. Raises :class:`InvalidScopeError`
    on the first malformed or unsupported scope (the message identifies it).
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for raw in scopes:
        scope = raw.strip()
        if not scope:
            continue
        if scope in _UNSUPPORTED_SCOPES:
            raise InvalidScopeError(
                f"Scope '{scope}' is not supported under the client-credentials "
                "grant (requires the interactive authorize flow, Stage 4)."
            )
        if not is_valid_scope(scope):
            raise InvalidScopeError(
                f"Scope '{scope}' is not a valid SMART scope. Expected "
                "'<context>/<Resource>.<permission>' (e.g. 'system/Observation.read')."
            )
        context = scope.split("/", 1)[0]
        if context not in _REGISTRABLE_CONTEXTS:
            raise InvalidScopeError(
                f"Scope '{scope}': the '{context}/' context is not supported yet."
            )
        if scope not in seen_set:
            seen.append(scope)
            seen_set.add(scope)
    return seen


def has_patient_context(scopes: Iterable[str]) -> bool:
    return any(s.startswith("patient/") for s in scopes)


def intersect_scopes(requested: Iterable[str], registered: Iterable[str]) -> list[str]:
    """Requested ∩ registered, preserving the registered order.

    A request for ``system/*.read`` is honored only if the client was actually
    granted that exact scope — we do not expand wildcards across the two sets
    (the client must have been granted what it asks for). An empty request
    means "all registered scopes" per OAuth convention.
    """
    registered_list = list(registered)
    requested_set = set(requested)
    if not requested_set:
        return list(registered_list)
    return [s for s in registered_list if s in requested_set]


def scope_allows(scopes: Iterable[str], resource_type: str, interaction: str) -> bool:
    """True if any of ``scopes`` permits ``interaction`` on ``resource_type``.

    ``interaction`` is ``"read"`` (covers read + search-type) or ``"write"``
    (covers create + update + delete). Matching rules per context
    (``system``/``patient``/``user`` are equivalent for *permission* checks;
    the patient-compartment narrowing is applied separately in the crud layer):

    * exact ``<ctx>/<Resource>.<perm>`` match
    * ``<ctx>/<Resource>.*`` (all permissions on the resource)
    * ``<ctx>/*.<perm>`` (the permission on any resource)
    * ``<ctx>/*.*`` (everything on any resource)

    Used in Phase 2 to scope-check facade interactions.
    """
    want_perm = "write" if interaction == "write" else "read"
    for scope in scopes:
        m = _SCOPE_RE.match(scope)
        if not m:
            continue
        _ctx, res, perm = m.group(1), m.group(2), m.group(3)
        res_ok = res == "*" or res == resource_type
        perm_ok = perm == "*" or perm == want_perm
        if res_ok and perm_ok:
            return True
    return False
