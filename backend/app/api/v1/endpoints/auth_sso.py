"""Per-tenant SSO endpoints (ADR 0054 D2 — program Phase 12B).

``/auth/sso/*`` — the OIDC entry points. ``authorize``/``status``/
``callback`` are unauthenticated auth-bootstrap routes (public_auth class:
rate-limited, no session dependency — same class as ``/auth/login``);
``/auth/sso/link`` is the authenticated link flow (the local user confirms
while signed in — a session route).

The callback mints the internal session through the one issuance path
(``auth_mode="oidc"``) and stamps the §10 cookie triple; JSON clients get
the token body (§9). Every refusal is a typed, machine-readable code —
``sso_*`` — and none of them ever touches the local password path.

**Parity with the password path (program 12C1):** a successful IdP
authentication is a *first factor*, not a policy exemption. The callback
enforces the tenant security policy exactly like ``POST /auth/login`` —
the IP allowlist (``403 ip_not_allowed``) and the MFA requirement
(challenge when a local TOTP secret is enrolled, when ``mfa_enforced`` is
set, or when the tenant policy lists the role). The IdP's ``amr``/``acr``
assertion satisfies the requirement only when it evidences a second
factor; otherwise the existing single-use ``mfa_challenge`` answers
``mfa_required`` and the sign-in completes locally (``/auth/mfa/verify``
issues an ``auth_mode="oidc"`` session — never a password-mode demotion).
"""

# ruff: noqa: B008 -- the rate_limit dependency pattern every auth endpoint uses

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tenant_context, token_store
from app.core.authz.policy import RouteClass, policy
from app.core.config import settings
from app.core.cookies import set_session_cookies
from app.core.database import get_db
from app.core.errors import AuthorizationError, ConflictError, ValidationError
from app.core.rate_limit import check_account_limit, rate_limit
from app.core.security import (
    MFA_CHALLENGE_TTL_SECONDS,
    create_mfa_challenge_token,
    get_current_user,
)
from app.schemas.auth import TokenResponse
from app.schemas.sso import (
    SSOAuthorizeRequest,
    SSOAuthorizeResponse,
    SSOConfigsByDomainResponse,
    SSOLinkConfirm,
)
from app.schemas.user import TokenData
from app.services.audit.audit_service import OUTCOME_DENIED, log_audit_action
from app.services.auth import login_notifications, passkeys, security_policy, sso
from app.services.auth.auth_session_service import device_hint

router = APIRouter(prefix="/auth/sso", tags=["authentication"])


def _redirect_uri(request: Request) -> str:
    """The absolute callback URL the IdP is told to redirect to."""
    base = settings.HA_SSO_REDIRECT_BASE
    if base:
        return base.rstrip("/") + "/api/v1/auth/sso/callback"
    root = str(request.base_url).rstrip("/")
    return f"{root}/api/v1/auth/sso/callback"


def _sso_http_error(exc: sso.SSOError) -> HTTPException:
    """Typed SSO refusal → HTTP (the stable ``code`` rides the body)."""
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


@policy(RouteClass.PUBLIC_AUTH)
@router.post("/status", response_model=SSOConfigsByDomainResponse)
async def sso_status(
    payload: SSOAuthorizeRequest,
    _rl=Depends(rate_limit("login", max_requests=20, window=60, bucket="auth")),
    db: AsyncSession = Depends(get_db),
) -> SSOConfigsByDomainResponse:
    """Login-page probe: does this email's domain have SSO?

    Answers one bit (no tenant enumeration — a domain either resolves or
    it does not; disabled configs read as absent). Pre-auth bootstrap
    lookup — runs system-side (ADR 0054 Phase 5).
    """
    email = (payload.email or "").strip().lower()
    if not email:
        return SSOConfigsByDomainResponse(sso_available=False)
    with tenant_context.system_scope("auth:sso-status"):
        with_sso = await sso.find_enabled_config_for_domain(db, email)
    return SSOConfigsByDomainResponse(sso_available=with_sso is not None)


@policy(RouteClass.PUBLIC_AUTH)
@router.post("/authorize", response_model=SSOAuthorizeResponse)
async def sso_authorize(
    payload: SSOAuthorizeRequest,
    request: Request,
    _rl=Depends(rate_limit("login", max_requests=20, window=60, bucket="auth")),
    db: AsyncSession = Depends(get_db),
) -> SSOAuthorizeResponse:
    """Start the OIDC flow for a tenant (resolved by email domain).

    Returns the provider's authorization URL (code + PKCE + state + nonce
    bound server-side). An unknown/disabled domain answers 404 — the SPA
    falls back to the password form.
    """
    email = (payload.email or "").strip().lower()
    if email:
        await check_account_limit("login", email)
    with tenant_context.system_scope("auth:sso-authorize"):
        if email:
            config = await sso.find_enabled_config_for_domain(db, email)
        elif payload.tenant_slug:
            config = await sso.find_enabled_config_for_tenant(db, payload.tenant_slug)
        else:
            config = None
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "sso_unavailable", "message": "No SSO provider for this address"},
        )
    try:
        begun = await sso.begin_sso(db, config, redirect_uri=_redirect_uri(request))
    except sso.SSOError as exc:
        raise _sso_http_error(exc) from exc
    return SSOAuthorizeResponse(
        authorization_url=begun.authorization_url,
        issuer=begun.config.issuer,
    )


async def _enforce_ip_policy(request: Request, state: str | None) -> None:
    """Refuse the callback when the tenant's IP allowlist excludes the client.

    Reads the flow state (single-use, server-side, TTL-bounded) only to
    learn the tenant — the state is **not** consumed here; the callback
    still enforces single use. A missing/unreadable state falls through to
    the callback's own typed refusal. The denied audit row goes on the
    dedicated lane (the request's transaction is rolled back by the raise)
    so the attempt survives.
    """
    if not state:
        return
    raw = await token_store.peek_sso_state(state)
    if not raw:
        return
    try:
        flow = json.loads(raw)
    except ValueError:
        return
    tenant_id = flow.get("tenant_id")
    policy = await security_policy.tenant_security_policy_for(tenant_id)
    client_ip = security_policy.request_client_ip(request)
    if policy.allows_ip(client_ip):
        return
    await log_audit_action(
        required=True,
        tenant_id=tenant_id,
        user_id=None,
        action="auth.sso_login",
        resource_type="auth",
        outcome=OUTCOME_DENIED,
        new_value={"detail": security_policy.DENY_IP},
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=security_policy.DENY_IP,
    )


@policy(RouteClass.PUBLIC_AUTH)
@router.get("/callback")
async def sso_callback(
    request: Request,
    response: Response,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    browser: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    _rl=Depends(rate_limit("login", max_requests=20, window=60, bucket="auth")),
):
    """Finish the OIDC flow: verify, resolve the link, sign in.

    JSON mode (default) answers the token body + §10 cookies; ``?browser=1``
    redirects the user agent to the SPA root with the same cookies (the SPA
    discovers the session through ``/auth/validate``). Every refusal keeps a
    stable ``code`` so the SPA can render the right message.
    """
    # The tenant IP allowlist gates the *sign-in attempt*, so it runs before
    # any provisioning: the flow state names the tenant, and a denied
    # address must not leave a freshly JIT-provisioned account behind.
    await _enforce_ip_policy(request, state)

    try:
        result = await sso.callback_sso(
            db, code=code, state=state, redirect_uri=_redirect_uri(request)
        )

        # Tenant security policy (ADR 0054 Phase 3) — the MFA requirement is
        # the second half of the password-parity gate (program 12C1): a
        # successful IdP authentication is a first factor, never a policy
        # exemption.
        user = result.user
        policy = await security_policy.tenant_security_policy_for(user.tenant_id)
        client_ip = security_policy.request_client_ip(request)

        # The provisioning (JIT user / identity / role change) must be
        # durable BEFORE the family row references it — issue_session
        # writes auth_sessions on its own lane — and before the MFA
        # challenge, which is answered on later requests.
        await db.commit()
        if result.role_changed_to:
            # The group→role change bumped ``users.token_version`` in the
            # committed transaction; drop the Redis hot layer too so a
            # demoted member's live access/refresh jtis die now (the admin
            # and SCIM paths do the same). The new family is minted below.
            await token_store.revoke_everything(str(user.id))
        tenant_context.set_tenant(user.tenant_id, actor=str(user.id))

        claims = result.claims
        has_passkey = await passkeys.has_active_passkey(user.id)
        mfa_required = bool(
            getattr(user, "mfa_secret_enc", None)
            or has_passkey
            or getattr(user, "mfa_enforced", False)
            or policy.requires_mfa(user.role)
        )
        if mfa_required and not (claims and sso.second_factor_asserted(claims)):
            enrollment_needed = not (getattr(user, "mfa_secret_enc", None) or has_passkey)
            methods: list[str] = []
            if has_passkey:
                methods.append("webauthn")
            if getattr(user, "mfa_secret_enc", None):
                methods.append("totp")
            challenge_token, challenge_jti = create_mfa_challenge_token(str(user.id), origin="sso")
            await token_store.register_mfa_challenge(
                str(user.id), challenge_jti, MFA_CHALLENGE_TTL_SECONDS
            )
            await log_audit_action(
                required=True,
                tenant_id=user.tenant_id,
                user_id=user.id,
                action="auth.sso_mfa_challenge",
                resource_type="auth",
                new_value={
                    "enrollment_needed": enrollment_needed,
                    "methods": methods,
                    "amr": list(claims.amr) if claims else [],
                    "acr": claims.acr if claims else None,
                },
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "detail": "mfa_required",
                    "mfa_token": challenge_token,
                    "enrollment_needed": enrollment_needed,
                    "methods": methods,
                    "origin": "sso",
                },
            )

        user_agent = request.headers.get("user-agent")
        issued = await sso.issue_sso_session(
            user,
            client_label=device_hint(user_agent) or "sso",
            ip=client_ip,
            user_agent=user_agent,
            amr=claims.amr if claims else (),
            auth_time=(claims.auth_time if claims and claims.auth_time is not None else None),
        )
    except sso.LinkRequiredConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc), "link_token": exc.link_token},
        ) from exc
    except sso.SSOError as exc:
        raise _sso_http_error(exc) from exc
    except AuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await login_notifications.notify_new_device(
        user,
        ip=client_ip,
        user_agent=user_agent,
        method="sso",
        family_id=issued.family_id,
    )

    if browser:
        redirect = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        set_session_cookies(
            redirect,
            access_token=issued.access_token,
            refresh_token=issued.refresh_token or None,
        )
        return redirect
    set_session_cookies(
        response,
        access_token=issued.access_token,
        refresh_token=issued.refresh_token or None,
    )
    return TokenResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        token_type="bearer",
        expires_in=issued.access_expires_in,
    )


@policy(RouteClass.SESSION)
@router.post("/link")
async def sso_link_confirm(
    payload: SSOLinkConfirm,
    current_user: TokenData = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The authenticated link flow (D2): the signed-in local user confirms.

    The actor must own the intent's email and tenant; the intent is
    single-use. No session is minted here — the next SSO sign-in resolves
    through the new link.
    """
    try:
        identity = await sso.confirm_link(db, actor=current_user, link_token=payload.link_token)
        await db.commit()
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {
        "linked": True,
        "identity": identity.to_dict(),
    }
