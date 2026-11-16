"""Central authorization enforcement — the app-level route gate (ADR 0054 1b).

``enforce_route_policy`` is registered as an app dependency
(``FastAPI(dependencies=[Depends(enforce_route_policy)])``) and runs before
every route's own dependencies:

1. the route's :class:`~app.core.authz.policy.RoutePolicy` is resolved from
   the declared ``@policy`` metadata when present, otherwise from the central
   resolver (:mod:`app.core.authz.route_policy`) under the inventory class;
2. ``session`` routes require a verified session principal — missing token
   or failed verification answers the same 401 the route's own
   ``get_current_user`` would have raised; a held permission is checked with
   :func:`app.core.authz.roles.principal_has` (the role defaults for a
   session, the token's own intersected permission set for a PAT) and the
   required scope with
   :func:`app.core.authz.scopes.scope_covers` (403 on failure);
3. a held permission whose role scope falls short of the route's is
   re-checked against the caller's **active break-glass elevation** (ADR
   0054 D1 — one time-boxed query per request, ``expires_at > now()``
   evaluated in SQL, nothing cached into the token): when the elevation
   covers the permission, its scope (tenant elevations widen to tenant,
   system elevations — SYSTEM_ADMIN requesters only — to system) satisfies
   the route, the request passes and the use is audited (``elevation_use``,
   fail-closed); a route that was reachable anyway is audited the same way
   while a window is open;
4. ``public``/``public_auth``/``token_*``/``websocket`` routes return
   immediately — their own class mechanisms enforce (facade SMART scopes,
   presigned tokens, HMAC signatures, ``get_session_user_ws``);
5. a route that resolves to no policy fails closed with 403.

The verified principal is cached on ``request.state.session_user`` so
``get_current_user`` never verifies the same token twice in one request.
WebSocket handshakes do not solve ``Request`` parameters, so the gate
returns for them; the WS handlers run the same policy check themselves
(:func:`app.api.v1.endpoints.websockets._authenticate_handshake`).

``RoleChecker``/``require_admin`` stay in place as defense-in-depth (the
route-level role pins), not as a compat shim — the central gate is the
primary decision, they re-assert narrower facts at the handler door.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

from fastapi import Depends, HTTPException, Request, status

from app.core.authz.policy import RouteClass, RoutePolicy, route_policy_of
from app.core.authz.roles import is_scoped_principal, principal_has, principal_scope
from app.core.authz.route_gate import FRAMEWORK_ROUTE_PATHS, load_inventory
from app.core.authz.route_policy import resolve_route_policy
from app.core.authz.scopes import scope_covers
from app.core.security import (
    API_TOKEN_KIND,
    authenticate_bearer_token,
    decode_token,
    get_token,
    reject_scoped_principal_on_session_only_surface,
    require_session_token,
)
from app.models.enums import Role
from app.schemas.user import TokenData
from app.services.audit.audit_service import (
    OUTCOME_DENIED,
    AuditWriteError,
    log_audit_action,
)
from app.services.auth.elevations import ElevationDecision, active_elevation_decision

logger = logging.getLogger(__name__)

_UNRESOLVED_DETAIL = "Access denied"

#: Marker on every gate-denial 403 so clients can tell the central gate's
#: "your role may not touch this route" apart from a resource layer's
#: row-level denial (ownership answers carry the same status code but no
#: marker — the SPA toasts only the former; see ``frontend/src/api/axios.ts``).
#: Exposed to the SPA via CORS ``expose_headers`` (app.main).
GATE_DENIED_HEADER = "X-Authz-Denied"


@dataclass(frozen=True)
class _ServedRoute:
    """The route as matched for this request (composed path + endpoint)."""

    path: str
    method: str
    endpoint: object | None


@dataclass
class _InventoryIndex:
    """The route-class baseline, keyed like the inventory (``METHOD path``)."""

    classes: dict[str, str]

    @classmethod
    def load(cls) -> _InventoryIndex:
        raw = load_inventory()
        return cls(classes={entry["route"]: entry["route_class"] for entry in raw["entries"]})


_index: _InventoryIndex | None = None


def _inventory_index() -> _InventoryIndex:
    global _index
    if _index is None:
        _index = _InventoryIndex.load()
    return _index


def _token_or_none(request: Request) -> str | None:
    """Extract the session credential without raising (cookie, then Bearer)."""
    try:
        # No Authorization header must not skip the cookie path: get_token
        # checks cookies FIRST (§10) and treats a falsy header as absent.
        return get_token(request, request.headers.get("authorization") or "")
    except HTTPException:
        return None


async def get_optional_session_user(request: Request = None) -> TokenData | None:  # type: ignore[assignment]
    """The verified session principal for this request, or ``None``.

    Reuses the ``get_token`` extraction (cookie first, then Bearer) and verifies
    through the single session path
    (:func:`app.core.security.authenticate_session_token`). The result — and
    a failed verification — is cached on ``request.state`` so
    ``get_current_user`` neither re-verifies nor changes the answer.
    WebSocket scopes never receive a ``Request``; they yield ``None``.

    The ``= None`` default is load-bearing, not sloppy typing: FastAPI injects
    a ``Request`` only when the annotation is exactly ``Request`` (a
    ``Request | None`` union is treated as a Pydantic field and fails route
    registration), and it leaves the default in place for WebSocket scopes,
    which is how this dependency answers ``None`` there. The same applies to
    ``enforce_route_policy`` below and ``security.get_current_user``.
    """
    if request is None or request.scope.get("type") != "http":
        return None
    cached = getattr(request.state, "session_user", None)
    if cached is not None:
        return cached
    auth_error = getattr(request.state, "session_auth_error", None)
    if auth_error is not None:
        return None
    token = _token_or_none(request)
    if not token:
        return None
    try:
        user = await authenticate_bearer_token(token)
        reject_scoped_principal_on_session_only_surface(user, request)
    except HTTPException as exc:
        request.state.session_auth_error = exc
        return None
    request.state.session_user = user
    return user


def _served_route(request: Request) -> _ServedRoute | None:
    """The matched route's composed templated path, method, and endpoint.

    FastAPI (0.142) matches lazily-included routers through an effective
    route context carrying the composed path (``/api/v1/...``); plain app
    routes expose the same facts on the route object itself.
    """
    route = request.scope.get("route")
    if route is None:
        return None
    context = (request.scope.get("fastapi") or {}).get("effective_route_context")
    path = getattr(context, "path", None) or getattr(route, "path", None)
    endpoint = getattr(context, "endpoint", None) or getattr(route, "endpoint", None)
    methods = getattr(context, "methods", None) or getattr(route, "methods", None) or set()
    method = request.method.upper()
    if method == "HEAD" and "GET" in methods:
        method = "GET"
    if not path:
        return None
    return _ServedRoute(path=path, method=method, endpoint=endpoint)


def _deny(detail: str, *, headers: dict[str, str] | None = None) -> HTTPException:
    marker = dict(headers or {})
    marker[GATE_DENIED_HEADER] = "1"
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail, headers=marker)


async def _audit_gate_denial(
    request: Request,
    session_user: TokenData | None,
    served: _ServedRoute,
    *,
    reason: str,
    permission: str | None = None,
    role: str | None = None,
) -> None:
    """Record one ``authz_denied`` row for a gate-level 403 (S7, fail closed).

    The central gate owns the denials it raises: the handler (and its
    ``audit_read`` outcomes) never run for them, so each denial layer has
    exactly one owner and no request is double-recorded. The write is
    required — an unrecorded authz denial is refused (500) instead of
    silently dropped; the request was denied either way. Request-scoped
    dedupe keeps a repeated gate pass in one request from writing twice.

    The denial rides the caller's tenant chain; if that write fails (a
    verified-later token whose tenant was hard-deleted inside the TTL
    window — the tenant FK refuses a dead chain), the same record falls
    back to the system chain with the tenant id kept in the detail payload.
    A denied request must answer with its denial either way.
    """
    if getattr(request.state, "authz_denial_audited", False):
        return
    request.state.authz_denial_audited = True
    tenant_id = getattr(session_user, "tenant_id", None)
    detail = {
        "path": served.path,
        "method": served.method,
        "cause": reason,
        "tenant_id": str(tenant_id) if tenant_id is not None else None,
    }
    if permission is not None:
        detail["permission"] = permission
    if role is not None:
        detail["role"] = role
    principal_id = getattr(session_user, "integration_principal_id", None) or getattr(
        session_user, "pat_id", None
    )
    if principal_id is not None:
        detail["principal_id"] = str(principal_id)
    try:
        await log_audit_action(
            tenant_id=tenant_id,
            user_id=getattr(session_user, "user_id", None),
            action="authz_denied",
            resource_type="route",
            outcome=OUTCOME_DENIED,
            new_value=detail,
            required=True,
        )
    except AuditWriteError:
        await log_audit_action(
            tenant_id=None,
            user_id=getattr(session_user, "user_id", None),
            action="authz_denied",
            resource_type="route",
            outcome=OUTCOME_DENIED,
            new_value={**detail, "chain_fallback": "system"},
            required=True,
        )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _active_elevation(request: Request, session_user: TokenData) -> ElevationDecision | None:
    """The caller's active elevation decision, or ``None`` (one query/request).

    The ``authorize()`` consultation (ADR 0054 D1): one time-boxed query per
    request — never cached into the token — so an approval, an early revoke
    or the expiry are honored on the next request regardless of how old the
    presented session is. Only roles that can hold an elevation (requesters
    and approvers; USER neither requests nor approves) pay the query.
    """
    if session_user is None or is_scoped_principal(session_user):
        return None
    if session_user.role == Role.USER.value:
        return None
    cached = getattr(request.state, "elevation_decision", False)
    if cached is not False:
        return cast("ElevationDecision | None", cached)
    from app.core.database import AsyncSessionLocal

    # A session principal (scoped credentials returned above) always carries
    # its user id — the elevation ladder is keyed by it.
    assert session_user.user_id is not None
    decision = None
    async with AsyncSessionLocal() as db:
        decision = await active_elevation_decision(
            db,
            user_id=session_user.user_id,
            tenant_id=session_user.tenant_id,
            role=session_user.role,
        )
    request.state.elevation_decision = decision
    return decision


async def _audit_elevation_use(
    request: Request,
    session_user: TokenData,
    served: _ServedRoute,
    decision: ElevationDecision,
    permission: str,
) -> None:
    """Record one ``elevation_use`` row for a request exercised under a
    live elevation (fail-closed — D3: authz decisions are security events).

    One row per request, written when the route's required permission is
    inside the approved set — whether the coverage was needed to pass the
    gate (a scope widening) or the route was reachable anyway (a SYSTEM_ADMIN
    operating inside an approved break-glass window)."""
    if getattr(request.state, "elevation_use_audited", False):
        return
    request.state.elevation_use_audited = True
    await log_audit_action(
        required=True,
        tenant_id=decision.tenant_id,
        user_id=session_user.user_id,
        action="elevation_use",
        resource_type="AccessElevation",
        resource_id=decision.id,
        new_value={
            "permission": permission,
            "elevation_scope": str(decision.scope),
            "path": served.path,
            "method": served.method,
        },
    )


def _resolve_policy(request: Request, served: _ServedRoute) -> RoutePolicy | None:
    """The declared policy for this request; ``None`` = fail closed."""
    declared = route_policy_of(served.endpoint) if served.endpoint is not None else None
    if declared is not None:
        return declared
    route_class = _inventory_index().classes.get(f"{served.method} {served.path}")
    return resolve_route_policy(served.method, served.path, route_class)


def resolve_ws_policy_or_none(path: str) -> RoutePolicy | None:
    """The policy for a WebSocket handshake path, or ``None`` = fail closed.

    The WS handlers' share of the central gate: the path must resolve under
    the ``websocket`` class to an auth-only policy (a permission on a WS
    route is a declaration bug and denies).
    """
    policy = resolve_route_policy("WS", path, RouteClass.WEBSOCKET)
    if policy is None or policy.permission is not None:
        return None
    return policy


async def enforce_route_policy(
    request: Request = None,  # type: ignore[assignment]  # FastAPI DI needs the exact Request annotation; WS scopes keep the None default (see get_optional_session_user)
    session_user: TokenData | None = Depends(get_optional_session_user),
) -> None:
    """The app-level authorization gate (ADR 0054 1b) — see module docs.

    WebSocket scopes never receive a ``Request`` (FastAPI solves it only for
    HTTP), so the gate returns and the WS handlers enforce their own policy
    check; framework doc surfaces stay out of the gate like out of the
    inventory.
    """
    if request is None or request.scope.get("type") != "http":
        return
    served = _served_route(request)
    if served is None or served.path in FRAMEWORK_ROUTE_PATHS:
        return
    policy = _resolve_policy(request, served)
    if policy is None:
        logger.error(
            "authz: no route policy resolved for %s %s — denying (fail closed)",
            served.method,
            served.path,
        )
        await _audit_gate_denial(request, session_user, served, reason="no_policy")
        raise _deny(_UNRESOLVED_DETAIL)
    if policy.route_class is not RouteClass.SESSION:
        return
    if session_user is None:
        auth_error = getattr(request.state, "session_auth_error", None)
        if auth_error is not None:
            # An api-kind credential is the facade's principal, never a
            # session — the domain router's own guard owns that answer
            # (it points the client at the facade instead of the generic
            # verifier rejection).
            token = _token_or_none(request)
            payload = decode_token(token) if token else None
            if payload is not None and payload.get("token_kind") == API_TOKEN_KIND:
                await require_session_token(request.headers.get("authorization"))
            raise auth_error
        raise _unauthorized("Missing authentication token")
    if policy.permission is None:
        return
    if policy.scope is None:
        logger.error(
            "authz: policy for %s %s carries a permission without a scope",
            served.method,
            served.path,
        )
        raise _deny(_UNRESOLVED_DETAIL)
    is_scoped = is_scoped_principal(session_user)
    try:
        role = Role(session_user.role)
    except ValueError:
        logger.warning(
            "authz: unknown role %r on %s %s — denying",
            session_user.role,
            served.method,
            served.path,
        )
        await _audit_gate_denial(
            request, session_user, served, reason="unknown_role", role=str(session_user.role)
        )
        raise _deny(_UNRESOLVED_DETAIL) from None
    if not principal_has(session_user, policy.permission):
        logger.info(
            "authz: denied %s %s — %s lacks %s",
            served.method,
            served.path,
            "scoped credential" if is_scoped else f"role {role.value}",
            policy.permission.value,
        )
        await _audit_gate_denial(
            request,
            session_user,
            served,
            reason="principal_lacks_permission" if is_scoped else "role_lacks_permission",
            permission=policy.permission.value,
            role=role.value,
        )
        raise _deny(_UNRESOLVED_DETAIL)
    granted = principal_scope(session_user, policy.permission)
    decision = await _active_elevation(request, session_user)
    scope_ok = granted is not None and scope_covers(policy.scope, granted)
    elevation_covered = False
    if not scope_ok and decision is not None and decision.covers(policy.permission):
        elevated = decision.effective_scope()
        if scope_covers(policy.scope, elevated):
            scope_ok = True
            elevation_covered = True
    if not scope_ok:
        logger.info(
            "authz: denied %s %s — role %s holds %s at %s, route requires %s",
            served.method,
            served.path,
            role.value,
            policy.permission.value,
            granted.value if granted else "none",
            policy.scope.value,
        )
        await _audit_gate_denial(
            request,
            session_user,
            served,
            reason="scope_mismatch",
            permission=policy.permission.value,
            role=role.value,
        )
        raise _deny(_UNRESOLVED_DETAIL)
    if decision is not None and decision.covers(policy.permission):
        await _audit_elevation_use(request, session_user, served, decision, policy.permission.value)
    if elevation_covered and decision is not None:
        logger.info(
            "authz: %s %s allowed under elevation %s (%s scope)",
            served.method,
            served.path,
            decision.id,
            decision.scope,
        )
