"""Route policy metadata — declare a route's authorization story.

A :class:`RoutePolicy` is an inert annotation: ``@policy(...)`` attaches an
attribute to the endpoint function *before* the router decorator registers
it, so routes, responses, OpenAPI output and the dependency graph are
untouched. Enforcement is Phase 1b/1c; in Phase 1 the metadata feeds the
inventory gate (``scripts/authz_inventory.py`` + the route-gate test) and the
generated matrix.

Usage (Phase 1b, after the deny-by-default flip)::

    @policy(RouteClass.SESSION, permission=Permission.PATIENT_READ, scope=AccessScope.GRANT)
    @router.get("/patients/{patient_id}")
    async def get_patient(...): ...

Token-principal routes declare their principal class instead of a permission
(FHIR facade, presign download, OAuth token, machine HMAC, WebSocket)::

    @policy(RouteClass.TOKEN_FHIR)
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from app.core.authz.permissions import Permission
from app.core.authz.scopes import AccessScope

POLICY_ATTR = "__authz_policy__"


class RouteClass(enum.StrEnum):
    """The token/principal class a route authenticates with (closed set).

    One class per mechanism; the allowlist is never a dumping ground for a
    new mechanism — a new class member is the reviewed change. The class is
    the route's *authentication* story; it implies the auth dependency the
    route must carry (see
    :data:`app.core.authz.route_gate.ROUTE_CLASS_DEPENDENCIES` for the
    pinned mapping):

    - ``session`` — browser/§9 clients presenting a session JWT
      (``get_current_user``; cookies + CSRF for browsers). The default for
      every domain route, including token-inspection endpoints such as
      ``GET /api/v1/auth/validate``.
    - ``public_auth`` — unauthenticated *auth-surface* routes that issue or
      exchange credentials (login, refresh, setup, register, mfa/verify).
      No auth dependency, but IP/account rate limits are part of the class.
      Never a plain data route.
    - ``public`` — unauthenticated, non-credential metadata (``/health``,
      ``/.well-known/smart-configuration``). No auth dependency.
    - ``token_fhir`` — SMART `api` principal via ``get_api_principal`` +
      ``require_fhir_scopes`` (the FHIR R4 facade).
    - ``token_oauth`` — OAuth2 client-credentials endpoints (client secret,
      no session principal).
    - ``token_presign`` — presigned download tokens
      (``verify_presigned_token``), no session.
    - ``tokenless_hmac`` — machine/device HMAC request signing
      (``verify_hmac_signature``), no bearer token at all.
    - ``token_scim`` — SCIM 2.0 directory clients presenting a tenant-bounded
      hashed bearer token (``get_scim_principal``), no session. The token is
      not a JWT: it resolves by its sha256 against ``scim_tokens``.
    - ``websocket`` — WebSocket handshakes via ``get_session_user_ws``
      (same verification path as ``session``, WS transport).
    """

    SESSION = "session"
    PUBLIC_AUTH = "public_auth"
    PUBLIC = "public"
    TOKEN_FHIR = "token_fhir"
    TOKEN_OAUTH = "token_oauth"
    TOKEN_PRESIGN = "token_presign"
    TOKENLESS_HMAC = "tokenless_hmac"
    TOKEN_SCIM = "token_scim"
    WEBSOCKET = "websocket"


@dataclass(frozen=True)
class RoutePolicy:
    """What a route requires, declared once where the route is defined."""

    route_class: RouteClass
    permission: Permission | None = None
    scope: AccessScope | None = None


F = TypeVar("F", bound=Callable[..., object])


def policy(
    route_class: RouteClass | str,
    permission: Permission | None = None,
    scope: AccessScope | None = None,
) -> Callable[[F], F]:
    """Decorator factory attaching the declared :class:`RoutePolicy`.

    Declaring twice is a programming error (the second declaration raises) —
    one route, one policy.
    """
    resolved = RoutePolicy(route_class=RouteClass(route_class), permission=permission, scope=scope)

    def decorate(fn: F) -> F:
        if hasattr(fn, POLICY_ATTR):
            raise ValueError(f"route already declares a policy: {getattr(fn, '__name__', fn)}")
        setattr(fn, POLICY_ATTR, resolved)
        return fn

    return decorate


def route_policy_of(endpoint: object) -> RoutePolicy | None:
    """Read the declared policy off an endpoint function (``None`` if undeclared)."""
    return getattr(endpoint, POLICY_ATTR, None)
