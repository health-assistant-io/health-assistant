# ruff: noqa: B008,SIM102 -- long immutable strings; reflow when touched
"""Authentication endpoints — login, register, invite, first-run setup.

Family contract (identity-auth §7/§8/§12) on the Class S base (§17):

1. **First-run setup (POST /auth/setup)** — the only bootstrap path.
   Creates the initial tenant + SYSTEM_ADMIN. Only callable while the
   system is uninitialized (zero users). Protected by a one-time setup
   token (from the backend logs) for non-localhost / non-dev requests,
   closing the first-claim race for internet-exposed instances. This is
   also an initialization point for the DB instance facts
   (``instance_settings`` — HA_AUTH_MODE / HA_DEMO_MODE consumed on an
   empty DB only, §4).
2. **Join existing tenant (POST /auth/register with tenant_id +
   invite_token)** — invite-only (plus the ``HA_REGISTRATION_ENABLED``
   gate): verifies the tenant exists AND that ``invite_token`` is a valid
   JWT signed with the invite key, scoped to that tenant. 403 otherwise.
   The role in the token wins; SYSTEM_ADMIN is never granted via this
   path.
3. **Invite issuance (POST /auth/invite)** — ADMIN+ only. Mints a
   single-use, jti-consumed token scoped to the caller's tenant.
4. **Sessions** — every sign-in creates an ``auth_sessions`` family row
   (device list + 30-day absolute refresh cap); rotation + reuse
   detection per §8; lockout (5 fails ⇒ 423 for 15 min) and generic
   login errors per §7.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import instance_state, setup_token, tenant_context, token_store
from app.core.authz.policy import RouteClass, policy
from app.core.config import settings
from app.core.cookies import (
    REFRESH_COOKIE,
    clear_session_cookies,
    set_session_cookies,
)
from app.core.database import get_db
from app.core.rate_limit import check_account_limit, rate_limit
from app.core.security import (
    AUTH_MODE_DEMO,
    AUTH_MODE_OIDC,
    AUTH_MODE_PASSWORD,
    MFA_CHALLENGE_TTL_SECONDS,
    _dummy_hash,
    create_invite_token,
    create_mfa_challenge_token,
    decode_refresh_token,
    get_current_user,
    get_password_hash,
    get_token,
    invite_jti,
    verify_access_token,
    verify_invite_token,
    verify_mfa_challenge_token,
    verify_password,
)
from app.models.enums import Role
from app.models.user_model import UserModel
from app.schemas.auth import (
    MFAChallengeEnrollRequest,
    MFAVerifyRequest,
    SetupRequest,
    SetupStatus,
    StepUpRequest,
    StepUpResponse,
    TokenRefresh,
    TokenResponse,
    UserRegister,
)
from app.schemas.user import PublicUser, TokenData, UserResponse
from app.services.audit.audit_service import (
    OUTCOME_DENIED,
    OUTCOME_OK,
    log_audit_action,
)
from app.services.auth import login_notifications, mfa_service, passkeys, security_policy, step_up
from app.services.auth.auth_session_service import (
    device_hint,
    issue_session,
    revoke_all_for_user,
    revoke_family,
    sha256_hex,
)
from app.services.tenancy.tenant_service import create_tenant, get_tenant
from app.services.tenancy.user_service import (
    bump_token_version,
    get_user_by_email,
    get_user_by_id,
    normalize_email,
    reset_login_failures,
    set_login_failures,
)
from app.services.tenancy.user_service import (
    create_user as service_create_user,
)

router = APIRouter(prefix="/auth", tags=["authentication"])

# §7: login failures are always generic (no user enumeration).
GENERIC_LOGIN_ERROR = "Invalid email or password"


async def _audit_auth_event(
    action: str,
    user: "UserModel | TokenData | None" = None,
    *,
    outcome: str = OUTCOME_OK,
    resource_id=None,
    new_value: dict | None = None,
    tenant_id=None,
) -> None:
    """Write one ``audit_events`` row for an auth event (§17, plan 16 H2).

    ``user`` may be ``None`` for anonymous attempts (unknown email on a
    denied login) — the NULL actor *is* the anonymous marker. Accepts a
    ``UserModel`` (``id``) or a ``TokenData`` principal (``user_id``).
    Fails closed (``required=True``, S3): an auth event that cannot be
    recorded raises :class:`~app.services.audit.audit_service.AuditWriteError`
    — an authentication decision must never succeed unrecorded.
    """
    await log_audit_action(
        required=True,
        tenant_id=tenant_id or getattr(user, "tenant_id", None),
        user_id=(
            getattr(user, "id", None) or getattr(user, "user_id", None)
            if user is not None
            else None
        ),
        action=action,
        resource_type="auth",
        resource_id=resource_id,
        outcome=outcome,
        new_value=new_value,
    )


# Stable 64-bit key for the bootstrap advisory lock. Picked from a hash of
# "HEALTH_ASSISTANT_BOOTSTRAP" so it's deterministic across code paths but
# unlikely to collide with anything else in the DB.
_BOOTSTRAP_ADVISORY_KEY = 0x48414F424F4F54  # 'HAOBOOT' as int56


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ensure_aware(value: datetime | None) -> datetime | None:
    """Normalize naive DB datetimes to aware UTC before comparing."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _token_response(issued) -> TokenResponse:
    return TokenResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        token_type="bearer",
        expires_in=issued.access_expires_in,
    )


def _set_auth_cookies(response: Response, issued) -> None:
    """Stamp the §10 cookie triple for browser sessions (plan 16 H3).

    The JSON body keeps carrying the tokens this pass — §9 user clients
    (Android app, scripts) consume them; the browser client ignores the
    body and rides the cookies. Body-token retirement for browsers is a
    frontend concern (the SPA no longer stores them).
    """
    set_session_cookies(
        response,
        access_token=issued.access_token,
        refresh_token=issued.refresh_token or None,
    )


async def _is_initialized(db: AsyncSession) -> bool:
    """True once at least one user row exists."""
    result = await db.execute(select(func.count()).select_from(UserModel))
    return (result.scalar() or 0) > 0


async def _count_system_context(db: AsyncSession) -> bool:
    """``_is_initialized`` under the auth layer's bootstrap context.

    Pre-auth instance fact (the login-vs-setup decision): the count spans
    every tenant, so it runs system-side (ADR 0054 Phase 5) and answers
    one bit, nothing more.
    """
    with tenant_context.system_scope("auth:setup-status"):
        return await _is_initialized(db)


@router.get("/setup-status", response_model=SetupStatus)
async def setup_status(request: Request, db: AsyncSession = Depends(get_db)):
    """First-run status — drives the frontend's login-vs-setup decision.

    No auth: the frontend must be able to call this before any user exists.
    ``initialized`` reflects whether a SYSTEM_ADMIN has been created (via
    the wizard, the CLI script, or the register path).
    ``setup_token_required`` tells the wizard whether to collect the
    one-time setup token (per-mode: see ``app/core/setup_token.py``).

    SECURITY: this endpoint never returns the setup token itself. In
    ``env`` mode the launcher already holds the token (it set it); it
    composes the one-click URL itself. Echoing the token here let any
    anonymous caller bootstrap the instance (audit 2026-08 C-1).
    """
    initialized = await _count_system_context(db)
    mode = setup_token.current_mode()
    token_required = False if initialized else setup_token.is_setup_token_required(request)
    return SetupStatus(
        initialized=initialized,
        setup_token_required=token_required,
        token_mode=mode,
        setup_url_hint=None,
        # DB fact (init-only HA_DEMO_MODE) — never the raw env (§4/§13).
        demo_mode=await instance_state.demo_mode_enabled(),
    )


@router.post("/setup", response_model=TokenResponse)
async def setup(
    payload: SetupRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _rl=Depends(rate_limit("register", max_requests=5, window=60, bucket="auth")),
):
    """First-run setup wizard endpoint.

    Creates the initial tenant + SYSTEM_ADMIN and returns login tokens so
    the caller is immediately authenticated. Only callable while the
    system is uninitialized. Protected by the one-time setup token (from
    the backend logs) for non-localhost / non-dev requests — closes the
    first-claim race for internet-exposed instances.

    Replaces the old ``POST /auth/register`` no-tenant_id bootstrap path
    (moved here so registration can be locked down to invite-only).
    """
    email = normalize_email(payload.email)
    await check_account_limit("register", email)

    # Bootstrap context (ADR 0054 Phase 5): the wizard checks the user
    # count across all tenants before any principal exists.
    with tenant_context.system_scope("auth:setup"):
        if await _is_initialized(db):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail=(
                    "This instance is already initialized. New accounts must be "
                    "created by an admin via an invite token (POST /auth/invite)."
                ),
            )

    # Init-only instance facts (identity-auth §4): the empty DB consumes
    # HA_AUTH_MODE / HA_DEMO_MODE exactly here (and at boot), never again.
    await instance_state.initialize()

    # Setup-token guardrail (skipped for localhost / dev).
    if setup_token.is_setup_token_required(request):
        if not setup_token.validate(payload.setup_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "A setup token is required for first-run setup. Retrieve it "
                    "from the backend container logs: "
                    "`docker compose ... logs backend | grep -i -A 1 'setup token'`."
                ),
            )

    with tenant_context.system_scope("auth:setup"):
        email_taken = await get_user_by_email(email)
    if email_taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    hashed_password = get_password_hash(payload.password)

    # Race-protected bootstrap: the advisory lock serializes the count +
    # insert so two concurrent setup attempts cannot both succeed. Same
    # pattern as the old register bootstrap path.
    with tenant_context.system_scope("auth:setup"):
        await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _BOOTSTRAP_ADVISORY_KEY})

        # Re-check inside the lock — a concurrent setup may have initialized
        # while we waited.
        if await _is_initialized(db):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="This instance was just initialized by another request.",
            )

        new_tenant = await create_tenant(name=payload.tenant_name)
        if not new_tenant:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not create the initial tenant.",
            )

        new_user_obj = UserModel(
            email=email,
            password_hash=hashed_password,
            full_name=payload.full_name,
            tenant_id=str(new_tenant.id),
            role=Role.SYSTEM_ADMIN,
            settings={"is_initial_admin": True},
        )
        db.add(new_user_obj)
        await db.commit()
        await db.refresh(new_user_obj)

    # Everything below runs as the new system actor.
    tenant_context.set_principal(
        TokenData(
            tenant_id=new_user_obj.tenant_id,
            user_id=new_user_obj.id,
            role=Role.SYSTEM_ADMIN.value,
        )
    )

    # Invalidate the one-time token — the system is now initialized.
    setup_token.clear()

    # Issue login tokens (mirrors /auth/login) so the caller is signed in.
    issued = await issue_session(
        new_user_obj,
        auth_mode=AUTH_MODE_PASSWORD,
        client_label=device_hint(request.headers.get("user-agent")) or "setup",
    )
    # §17: first-run bootstrap is the ultimate admin action — audit it.
    await _audit_auth_event(
        "auth.setup",
        new_user_obj,
        tenant_id=getattr(new_tenant, "id", None),
        new_value={
            "tenant": getattr(new_tenant, "name", None),
            "role": Role.SYSTEM_ADMIN.value,
        },
    )
    _set_auth_cookies(response, issued)
    return _token_response(issued)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    _rl=Depends(rate_limit("login", max_requests=20, window=60, bucket="auth")),
):
    """Authenticate user and return tokens (§7: generic errors, lockout)."""
    email = normalize_email(form_data.username)
    # §7 per-account sliding window — one mailbox cannot be brute-forced
    # from a botnet even when every IP stays under the per-IP budget.
    await check_account_limit("login", email)

    # Bootstrap lookup (ADR 0054 Phase 5): the email is globally unique
    # and no principal exists yet — the auth layer resolves it system-side,
    # then the rest of the login runs under the user's tenant.
    with tenant_context.system_scope("auth:login"):
        user = await get_user_by_email(email)
    if user is not None:
        tenant_context.set_tenant(user.tenant_id, actor=str(user.id))

    # Uniform timing + message: verify against a dummy hash when the user
    # does not exist (or has no password) so response time cannot
    # enumerate accounts (audit 2026-08 M1).
    stored_hash = _dummy_hash()
    if user is not None and getattr(user, "password_hash", None):
        stored_hash = user.password_hash
    authenticated = user is not None and verify_password(form_data.password, stored_hash)

    if user is not None:
        now = _utcnow()
        locked_until = _ensure_aware(getattr(user, "locked_until", None))
        if locked_until is not None and locked_until > now:
            # §7: 5 failures ⇒ locked_until = now + 15 min ⇒ 423.
            await _audit_auth_event(
                "auth.login",
                user,
                outcome=OUTCOME_DENIED,
                new_value={"detail": "account_locked"},
            )
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account locked; try again later",
            )
        if not authenticated:
            failed = int(getattr(user, "failed_login_attempts", 0) or 0) + 1
            locked: datetime | None = locked_until
            if failed >= settings.HA_AUTH_LOCKOUT_THRESHOLD:
                locked = now + timedelta(minutes=settings.HA_AUTH_LOCKOUT_MINUTES)
            await set_login_failures(user.id, failed, locked)
            if locked is not None and locked > now:
                await _audit_auth_event(
                    "auth.login",
                    user,
                    outcome=OUTCOME_DENIED,
                    new_value={"detail": "account_locked"},
                )
                raise HTTPException(
                    status_code=status.HTTP_423_LOCKED,
                    detail="Account locked; try again later",
                )
            await _audit_auth_event(
                "auth.login",
                user,
                outcome=OUTCOME_DENIED,
                new_value={"detail": "invalid_credentials"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=GENERIC_LOGIN_ERROR,
                headers={"WWW-Authenticate": "Bearer"},
            )
    if not authenticated:
        # Unknown (or password-less) email — anonymous denial (§7 dummy
        # hash keeps timing uniform; the NULL actor marks "anonymous").
        await _audit_auth_event(
            "auth.login",
            None,
            outcome=OUTCOME_DENIED,
            new_value={"detail": "invalid_credentials"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_LOGIN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not getattr(user, "is_active", True):
        # §5/§18.9 — disabled accounts get the generic login error (no
        # enumeration), and could not use a token anyway.
        await _audit_auth_event(
            "auth.login",
            user,
            outcome=OUTCOME_DENIED,
            new_value={"detail": "account_disabled"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_LOGIN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Tenant security policy (ADR 0054 Phase 3) — read per request, never
    # token-cached. The IP allowlist gates session issuance; a policy-
    # required MFA role forces the same challenge flow as admin-forced MFA
    # (the §7 counter reset below applies identically — the password just
    # succeeded). The demo principal is exempt only at the demo-login
    # endpoint; a password login always answers to the policy.
    policy = await security_policy.tenant_security_policy_for(user.tenant_id)
    if not policy.allows_ip(security_policy.request_client_ip(request)):
        await _audit_auth_event(
            "auth.login",
            user,
            outcome=OUTCOME_DENIED,
            new_value={"detail": security_policy.DENY_IP},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=security_policy.DENY_IP,
        )

    # Plan 16 H5 (TOTP MFA): when a confirmed secret exists — or an admin
    # has forced MFA — the password alone is not sufficient. Answer with
    # the machine-readable 401 challenge instead of tokens:
    # ``{"detail": "mfa_required", "mfa_token": ..., "enrollment_needed"}``.
    # The challenge JWT is a dedicated ``mfa_challenge`` kind (5-min TTL,
    # single-use via the token store — consumed by the first successful
    # verify). §7 interplay: the password just succeeded, so the failure
    # counter resets HERE; wrong MFA codes at /auth/mfa/verify then count
    # from zero toward the same 5-strike lockout (documented H5 decision).
    has_passkey = await passkeys.has_active_passkey(user.id)
    if (
        getattr(user, "mfa_secret_enc", None)
        or has_passkey
        or getattr(user, "mfa_enforced", False)
        or policy.requires_mfa(user.role)
    ):
        await reset_login_failures(user.id)
        enrollment_needed = not (getattr(user, "mfa_secret_enc", None) or has_passkey)
        methods: list[str] = []
        if has_passkey:
            methods.append("webauthn")
        if getattr(user, "mfa_secret_enc", None):
            methods.append("totp")
        challenge_token, challenge_jti = create_mfa_challenge_token(str(user.id))
        await token_store.register_mfa_challenge(
            str(user.id), challenge_jti, MFA_CHALLENGE_TTL_SECONDS
        )
        await _audit_auth_event(
            "auth.mfa_challenge",
            user,
            new_value={"enrollment_needed": enrollment_needed, "methods": methods},
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "detail": "mfa_required",
                "mfa_token": challenge_token,
                "enrollment_needed": enrollment_needed,
                "methods": methods,
            },
        )

    # Counters reset on successful login (§7).
    await reset_login_failures(user.id)

    client_ip = security_policy.request_client_ip(request)
    user_agent = request.headers.get("user-agent")
    issued = await issue_session(
        user,
        auth_mode=AUTH_MODE_PASSWORD,
        client_label=device_hint(user_agent) or "login",
        ip=client_ip,
        user_agent=user_agent,
    )
    await _audit_auth_event("auth.login", user, new_value={"detail": "password"})
    await login_notifications.notify_new_device(
        user,
        ip=client_ip,
        user_agent=user_agent,
        method="password",
        family_id=issued.family_id,
    )
    _set_auth_cookies(response, issued)
    return _token_response(issued)


@router.post("/step-up", response_model=StepUpResponse)
@policy(RouteClass.SESSION)
async def step_up_verify(
    payload: StepUpRequest,
    current_user: TokenData = Depends(get_current_user),
    _rl=Depends(rate_limit("step_up", max_requests=10, window=60, bucket="auth")),
):
    """Prove presence for one sensitive action (program 12C1).

    A live session is not enough for a role change, an export, a break-glass
    elevation or an admin-level grant: those endpoints answer
    ``403 {"code": "step_up_required", "action": ...}`` until this call
    records a proof for that action. The factors are the account's own —
    the local password when one is set, TOTP/recovery when MFA is active —
    and an SSO-only account may use its federated sign-in while the IdP's
    ``auth_time`` is inside the window and a second factor was asserted.

    The proof is bound to this sign-in family, to this one action and to a
    10-minute window; every success/refusal is audited (``auth.step_up``).
    Both an IP window and a per-account window apply, so a stolen session
    cannot turn this into a password oracle.
    """
    await check_account_limit("step_up", str(current_user.user_id))
    try:
        action = step_up.StepUpAction(payload.action)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown step-up action {payload.action!r}",
        ) from None
    proof = await step_up.issue_proof(
        current_user,
        action=action,
        password=payload.password,
        code=payload.code,
    )
    return StepUpResponse(**proof.to_dict())


@router.post("/demo-login", response_model=TokenResponse)
async def demo_login(
    request: Request,
    response: Response,
    _rl=Depends(rate_limit("demo_login", max_requests=20, window=60, bucket="auth")),
):
    """Credential-free login for the demo principal (§13).

    Only available while the DB instance fact ``demo_mode`` is true
    (init-only ``HA_DEMO_MODE`` — returns 404 otherwise). Looks up the
    pre-seeded demo user (``HA_DEMO_EMAIL``) and issues tokens stamped
    with ``auth_mode="demo"`` so verifiers can reject them the moment the
    instance stops being a demo (S-7). The demo user + data are
    auto-seeded on boot (scripts/seed_demo.py).
    """
    if not await instance_state.demo_mode_enabled():
        # §13/S-7 (plan 16 H7): probing demo-login on a non-demo instance
        # is a (rate-limited) auth signal — one denied audit row, then the
        # generic 404 (the route must look inert, not locked).
        await _audit_auth_event(
            "auth.demo_login",
            None,
            outcome=OUTCOME_DENIED,
            new_value={"detail": "demo_mode_off"},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demo login is not enabled on this instance.",
        )

    with tenant_context.system_scope("auth:demo_login"):
        user = await get_user_by_email(settings.HA_DEMO_EMAIL)
    if user is not None:
        tenant_context.set_tenant(user.tenant_id, actor=str(user.id))
    if not user:
        await _audit_auth_event(
            "auth.demo_login",
            None,
            outcome=OUTCOME_DENIED,
            new_value={"detail": "demo_user_missing"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Demo user not found. Ensure demo seeding has completed "
                "(the demo user is created on backend startup)."
            ),
        )

    if not getattr(user, "is_active", True):
        await _audit_auth_event(
            "auth.demo_login",
            user,
            outcome=OUTCOME_DENIED,
            new_value={"detail": "account_disabled"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo account is disabled.",
        )

    issued = await issue_session(
        user,
        auth_mode=AUTH_MODE_DEMO,
        client_label=device_hint(request.headers.get("user-agent")) or "demo",
    )
    await _audit_auth_event("auth.demo_login", user, new_value={"detail": "demo"})
    _set_auth_cookies(response, issued)
    return _token_response(issued)


@router.post("/mfa/verify", response_model=TokenResponse)
async def mfa_verify(
    request: Request,
    response: Response,
    payload: MFAVerifyRequest,
    _rl=Depends(rate_limit("mfa_verify", max_requests=20, window=60, bucket="auth")),
):
    """Answer a login MFA challenge (plan 16 H5).

    ``{mfa_token, code}`` where ``code`` is the current TOTP code or one
    of the single-use recovery codes. On success the challenge jti is
    consumed (single-use — a replayed challenge cannot mint a second
    session), the §7 counters reset, and a **normal** session is issued
    (§10 cookie triple + body tokens, exactly like a password login).

    Forced enrollment: when the login challenge carried
    ``enrollment_needed: true`` (admin-forced MFA), the code is checked
    against the *pending* secret from ``POST /auth/mfa/enroll`` and a
    match activates MFA as part of signing in.

    §7 lockout (documented decision): wrong codes increment the same
    ``failed_login_attempts`` counter as wrong passwords — 5 strikes
    lock the account for 15 minutes (423). A recovery code is consumed
    only on a successful match.
    """
    challenge = verify_mfa_challenge_token(payload.mfa_token)
    if not challenge:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge",
        )
    challenge_user_id = str(challenge.get("sub") or challenge.get("user_id") or "")
    challenge_jti = str(challenge.get("jti") or "")
    if not challenge_user_id or not challenge_jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge",
        )

    # §7 per-account window, keyed by the challenge's user.
    await check_account_limit("mfa_verify", challenge_user_id)

    # Bootstrap lookup (ADR 0054 Phase 5): the signed challenge carries
    # only the user id — the auth layer resolves it system-side, then the
    # verify runs under the user's tenant.
    with tenant_context.system_scope("auth:mfa_verify"):
        user = await get_user_by_id(challenge_user_id)
    if user is None or not getattr(user, "is_active", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge",
        )
    tenant_context.set_tenant(user.tenant_id, actor=str(user.id))

    now = _utcnow()
    locked_until = _ensure_aware(getattr(user, "locked_until", None))
    if locked_until is not None and locked_until > now:
        await _audit_auth_event(
            "auth.mfa_verify",
            user,
            outcome=OUTCOME_DENIED,
            new_value={"detail": "account_locked"},
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account locked; try again later",
        )

    # Single-use: the challenge must still be registered (a consumed or
    # expired challenge cannot mint a session — fail closed on Redis).
    if not await token_store.is_mfa_challenge_active(str(user.id), challenge_jti):
        await _audit_auth_event(
            "auth.mfa_verify",
            user,
            outcome=OUTCOME_DENIED,
            new_value={"detail": "challenge_replayed"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge",
        )

    # Tenant security policy (ADR 0054 Phase 3): the IP allowlist gates
    # session *issuance*, and this verify mints the session — so the same
    # check applies here (an address that passed the password step can
    # change before the MFA step; program 12C1 closed that gap).
    policy = await security_policy.tenant_security_policy_for(user.tenant_id)
    client_ip = security_policy.request_client_ip(request)
    if not policy.allows_ip(client_ip):
        await _audit_auth_event(
            "auth.mfa_verify",
            user,
            outcome=OUTCOME_DENIED,
            new_value={"detail": security_policy.DENY_IP},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=security_policy.DENY_IP,
        )

    method: str | None = None
    if getattr(user, "mfa_secret_enc", None):
        method = await mfa_service.verify_code(user, payload.code)
    else:
        if getattr(user, "mfa_enforced", False) or policy.requires_mfa(user.role):
            # Admin-forced (plan 16 H5) or tenant-policy-required (ADR 0054
            # Phase 3): the code must confirm the *pending* enrollment
            # secret (provisioned via POST /auth/mfa/enroll with this
            # challenge token) — the match activates MFA.
            if await mfa_service.confirm_enrollment(user.id, payload.code):
                method = "totp_enroll"
        else:
            # MFA was cleared between the challenge and this verify — the
            # user simply re-logs-in with the password.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired MFA challenge",
            )

    if not method:
        failed = int(getattr(user, "failed_login_attempts", 0) or 0) + 1
        locked: datetime | None = locked_until
        if failed >= settings.HA_AUTH_LOCKOUT_THRESHOLD:
            locked = now + timedelta(minutes=settings.HA_AUTH_LOCKOUT_MINUTES)
        await set_login_failures(user.id, failed, locked)
        await _audit_auth_event(
            "auth.mfa_verify",
            user,
            outcome=OUTCOME_DENIED,
            new_value={"detail": "invalid_code"},
        )
        if locked is not None and locked > now:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account locked; try again later",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid verification code",
        )

    # Success: consume the challenge (single-use), reset §7, sign in.
    # The GETDEL result is the gate: a concurrent request carrying the same
    # challenge + code must not mint a second session.
    consumed = await token_store.consume_mfa_challenge(str(user.id), challenge_jti)
    if not consumed:
        await _audit_auth_event(
            "auth.mfa_verify",
            user,
            outcome=OUTCOME_DENIED,
            new_value={"detail": "challenge_replayed"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge",
        )
    await reset_login_failures(user.id)
    # The challenge's origin decides the session mode: an SSO challenge
    # completes as an OIDC session (never a password-mode demotion), and
    # the local one as a password session.
    challenge_origin = str(challenge.get("origin") or "")
    auth_mode = AUTH_MODE_OIDC if challenge_origin == "sso" else AUTH_MODE_PASSWORD
    client_ip = security_policy.request_client_ip(request)
    user_agent = request.headers.get("user-agent")
    label = "sso+mfa" if challenge_origin == "sso" else "mfa-login"
    issued = await issue_session(
        user,
        auth_mode=auth_mode,
        client_label=device_hint(user_agent) or label,
        ip=client_ip,
        user_agent=user_agent,
    )
    await _audit_auth_event("auth.mfa_verify", user, new_value={"method": method})
    await login_notifications.notify_new_device(
        user,
        ip=client_ip,
        user_agent=user_agent,
        method="sso+totp" if challenge_origin == "sso" else "password+totp",
        family_id=issued.family_id,
    )
    _set_auth_cookies(response, issued)
    return _token_response(issued)


@router.post("/mfa/enroll")
async def mfa_challenge_enroll(
    payload: MFAChallengeEnrollRequest,
    _rl=Depends(rate_limit("mfa_enroll", max_requests=10, window=60, bucket="auth")),
):
    """Provisioning for the **forced** enrollment path (plan 16 H5).

    Only meaningful when a login challenge carried
    ``enrollment_needed: true`` (``mfa_enforced`` set, no active
    secret): returns the one-time provisioning payload (secret, otpauth
    URI, recovery codes) bound to the challenge's user. The user then
    confirms with the current code at ``POST /auth/mfa/verify``.
    Self-service enrollment (MFA already active-able account) lives at
    ``POST /me/mfa/enroll`` instead.
    """
    challenge = verify_mfa_challenge_token(payload.mfa_token)
    if not challenge:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge",
        )
    challenge_user_id = str(challenge.get("sub") or challenge.get("user_id") or "")
    challenge_jti = str(challenge.get("jti") or "")
    if not challenge_user_id or not challenge_jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge",
        )
    if not await token_store.is_mfa_challenge_active(challenge_user_id, challenge_jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge",
        )

    with tenant_context.system_scope("auth:mfa_enroll"):
        user = await get_user_by_id(challenge_user_id)
    if user is None or not getattr(user, "is_active", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge",
        )
    tenant_context.set_tenant(user.tenant_id, actor=str(user.id))
    if not getattr(user, "mfa_enforced", False):
        policy = await security_policy.tenant_security_policy_for(user.tenant_id)
        if not policy.requires_mfa(user.role):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA enrollment is not required for this account.",
            )
    if getattr(user, "mfa_secret_enc", None):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is already active on this account.",
        )

    data = await mfa_service.begin_enrollment(user)
    await _audit_auth_event("auth.mfa_enroll", user, new_value={"forced": True})
    return data


@router.post("/register", response_model=UserResponse)
async def register(
    user_data: UserRegister,
    db: AsyncSession = Depends(get_db),
    _rl=Depends(rate_limit("register", max_requests=5, window=60, bucket="auth")),
):
    """Register a new user into an existing tenant (invite-only).

    Note (plan 16 H3): unlike login/setup/demo-login, this endpoint mints
    **no tokens** — the new member signs in via ``POST /auth/login``
    right after, which sets the §10 cookie triple. There is deliberately
    no auto-session here.

    Gated by ``HA_REGISTRATION_ENABLED`` (§12/§16) *and* an invite token:
    the open bootstrap path (no ``tenant_id``) was removed — first-run
    provisioning goes through ``POST /auth/setup`` (the browser wizard) or
    the ``create_system_admin.py`` CLI. Every registration here requires a
    ``tenant_id`` plus a valid invite token minted by that tenant's admin
    via ``POST /auth/invite``.

    The invite is validated (and consumed — single-use) BEFORE the
    email-exists check, so an unauthenticated caller cannot use the
    "Email already registered" error to enumerate accounts without a
    valid invite (audit 2026-08 M2).
    """
    email = normalize_email(user_data.email)
    await check_account_limit("register", email)

    if not settings.HA_REGISTRATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is disabled on this instance.",
        )

    if not user_data.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "A tenant_id and a valid invite token are required to register. "
                "If this is a fresh install, use the first-run setup wizard "
                "(POST /auth/setup) instead."
            ),
        )

    if not user_data.invite_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "An invite token is required to join an existing tenant. "
                "Ask the tenant administrator to issue one via POST /auth/invite."
            ),
        )

    # Joining an existing tenant — require a valid invite token.
    tenant = await get_tenant(user_data.tenant_id)
    if not tenant:
        # 404 (not 403) so we don't leak that the tenant exists but is
        # locked down — matches the rest of the API's posture.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    # The invite token is signature-bound to this tenant (ADR 0054
    # Phase 5): from here the registration runs under the target tenant's
    # context; the global email-uniqueness check below stays system-side.
    tenant_context.set_tenant(tenant.id, actor=email)

    ok, granted_role = verify_invite_token(
        user_data.invite_token,
        expected_tenant_id=str(tenant.id),
        expected_email=email,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid, expired, or tenant-mismatched invite token.",
        )

    # Single-use: atomically consume the invite's jti (Redis GETDEL
    # semantics). Fails closed when Redis is unavailable so an outage
    # cannot convert single-use invites into unlimited ones.
    invite_jti_value = invite_jti(user_data.invite_token)
    if invite_jti_value and not await token_store.consume_invite(invite_jti_value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite token has already been used.",
        )

    with tenant_context.system_scope("auth:register"):
        if await get_user_by_email(email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
            )

    hashed_password = get_password_hash(user_data.password)

    role = granted_role or Role.USER.value
    new_user = await service_create_user(
        email=email,
        password_hash=hashed_password,
        tenant_id=str(tenant.id),
        role=role,
        full_name=user_data.full_name,
    )
    # §17: invite use (a join) is an auditable identity event.
    await _audit_auth_event(
        "auth.register",
        new_user,
        new_value={"role": role, "tenant_id": str(tenant.id)},
    )
    return new_user


@router.post("/invite")
async def create_invite(
    tenant_id: str | None = None,
    email: str | None = None,
    role: str = Role.USER.value,
    expires_days: int = 7,
    current_user: TokenData = Depends(get_current_user),
    _rl=Depends(rate_limit("invite", max_requests=10, window=60, bucket="auth")),
):
    """Mint a tenant invite token (single-use, TTL capped at 30 days).

    Admin/Manager/System-admin only. The token is scoped to the caller's
    tenant (the ``tenant_id`` query param, if supplied, must match it).
    Cannot grant SYSTEM_ADMIN — that role is bootstrap-only.
    """
    if current_user.role not in (
        Role.ADMIN.value,
        Role.MANAGER.value,
        Role.SYSTEM_ADMIN.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators may issue invite tokens.",
        )

    target_tenant = tenant_id or str(current_user.tenant_id)
    if (
        str(current_user.tenant_id) != target_tenant
        and current_user.role != Role.SYSTEM_ADMIN.value
    ):
        # Non-SYSTEM_ADMIN can only invite into their own tenant.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot issue invites for a different tenant.",
        )

    if role == Role.SYSTEM_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SYSTEM_ADMIN cannot be granted via invite. Use the bootstrap path.",
        )

    try:
        granted_role = Role(role)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role: {role}.",
        ) from None

    # A MANAGER manages content, not accounts: they may only invite plain
    # USERs. An ADMIN may invite up to their own level.
    if current_user.role == Role.MANAGER.value and granted_role != Role.USER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MANAGER may only invite USER accounts.",
        )

    # Cap the TTL: an invite is a short-lived onboarding artifact, not a
    # standing credential (audit 2026-08 M3).
    expires_days = max(1, min(int(expires_days), 30))

    token, invite_jti_value = create_invite_token(
        tenant_id=target_tenant,
        email=email,
        role=role,
        expires_days=expires_days,
    )
    await token_store.register_invite(
        invite_jti_value, int(timedelta(days=expires_days).total_seconds())
    )
    return {
        "invite_token": token,
        "tenant_id": target_tenant,
        "role": role,
        "expires_in_days": expires_days,
    }


@router.get("/validate")
async def validate_token(current_user: TokenData = Depends(get_current_user)):
    """Validate current token.

    The response is additively extended (plan 16 H3) with the session's
    product claims: the browser client can no longer decode the JWT
    itself (§10 — it is HttpOnly), so this is where it reads tenant /
    role / auth_mode / switched state (e.g. to sync the tenant-switch
    banner). §9 Bearer clients ignore the extra fields.
    """
    return {
        "valid": True,
        "user_id": str(current_user.user_id),
        "email": current_user.email,
        "role": current_user.role,
        "tenant_id": str(current_user.tenant_id) if current_user.tenant_id else None,
        "auth_mode": current_user.auth_mode,
        "switched": bool(current_user.switched),
        "original_tenant_id": (
            str(current_user.original_tenant_id) if current_user.original_tenant_id else None
        ),
    }


@router.get("/me", response_model=PublicUser)
async def auth_me(current_user: TokenData = Depends(get_current_user)):
    """Identity-auth §12: the caller's ``PublicUser``.

    The user row was already verified live by ``get_current_user``
    (is_active + ``ver``); re-read it for the full contract shape.
    """
    user = await get_user_by_id(current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found in database")
    return PublicUser(
        id=user.id,
        email=user.email,
        full_name=getattr(user, "full_name", "") or "",
        is_admin=bool(user.is_admin),
        is_active=bool(user.is_active),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    response: Response,
    token_data: TokenRefresh | None = None,
    _rl=Depends(rate_limit("refresh", max_requests=30, window=60, bucket="auth")),
):
    """Refresh access token (with rotation + reuse detection — §8).

    The presented refresh token must belong to a live ``auth_sessions``
    family (``fid`` claim): its jti hash must match the family's current
    hash — replay of a rotated token revokes the whole family and bumps
    ``token_version`` (family-wide sign-out) with 423. The 30-day
    ``absolute_expires_at`` caps the rolling window regardless of
    activity.

    Presentation (§10): browsers send no body — the rotating refresh JWT
    is read from the HttpOnly ``ha_refresh`` cookie (Path=/api/v1/auth,
    so it only rides this endpoint); §9 user clients keep the JSON body.
    On success the cookie triple is re-stamped, so rotation rotates the
    cookies too.

    The user row is re-loaded from the DB on every refresh (audit 2026-08
    H2): a deleted or deactivated user is refused, and the new claims
    (email, tenant, role, ver) are rebuilt from the database — never from
    the old token — so role changes and tenant moves take effect at the
    next refresh even if the old token predates them.

    Switched SYSTEM_ADMIN sessions preserve their switch metadata: the
    target tenant is re-validated to still exist and be active before the
    switched context is extended.
    """
    presented_refresh = (token_data.refresh_token if token_data else None) or (
        request.cookies.get(REFRESH_COOKIE)
    )
    if not presented_refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_refresh_token(presented_refresh)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("user_id") or payload.get("sub")
    jti = payload.get("jti")
    family_id = payload.get("fid")
    if not user_id or not jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # §7 per-account window on refresh, keyed by the token's user.
    await check_account_limit("refresh", str(user_id))

    # Bootstrap verification (ADR 0054 Phase 5): refresh tokens carry no
    # tenant claim (identity-auth §8) — the auth layer resolves the user
    # and the session family system-side, then re-plants the user's
    # tenant for issuance below.
    with tenant_context.system_scope("auth:refresh"):
        user = await get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    tenant_context.set_tenant(user.tenant_id, actor=str(user.id))
    if not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("ver") is not None and int(user.token_version or 1) != int(payload["ver"]):
        # token_version bump = global sign-out (§8).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("auth_mode") == AUTH_MODE_DEMO:
        # §13 / S-7 (plan 16 H7): the demo stamp travels with the family,
        # but it is valid only while the live DB fact ``demo_mode`` is
        # true. Re-checked on **every rotation** so a family minted on a
        # demo instance cannot keep refreshing after the instance stops
        # being one (the init-only env can never extend it — §4.1).
        if not await instance_state.demo_mode_enabled():
            await _audit_auth_event(
                "auth.refresh",
                user,
                outcome=OUTCOME_DENIED,
                new_value={"detail": "demo_refresh_on_non_demo_instance"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

    from app.services.auth.auth_session_service import get_family

    family = await get_family(family_id) if family_id else None
    if family is None or str(family.user_id) != str(user.id) or family.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = _utcnow()
    if now >= _ensure_aware(family.absolute_expires_at) or now >= _ensure_aware(family.expires_at):
        await revoke_family(family_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if sha256_hex(str(jti)) != family.refresh_jti_hash:
        # Reuse of a rotated refresh token: revoke the whole family and
        # bump token_version — family-wide sign-out (identity-auth §8).
        await revoke_all_for_user(user.id)
        await bump_token_version(user.id)
        # §17 (plan 16 H2): refresh-token reuse is a security signal —
        # it lands in the audit stream with outcome=denied.
        await _audit_auth_event(
            "auth.refresh_reuse",
            user,
            outcome=OUTCOME_DENIED,
            new_value={"detail": "rotated_refresh_token_replayed"},
        )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Session revoked",
        )

    # Hot layer: the presented jti must still be registered (logout kills
    # it immediately; Redis outage degrades open — the DB family is the
    # authoritative record).
    if not await token_store.is_active(str(user.id), str(jti)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Tenant security policy (ADR 0054 Phase 3) — per request: the IP
    # allowlist gates refresh, the family row caps the session lifetime at
    # ``session_max_hours`` (authoritative even after a Redis anchor loss)
    # and the idle window rides the family anchor.
    policy = await security_policy.tenant_security_policy_for(user.tenant_id)
    if not policy.allows_ip(security_policy.request_client_ip(request)):
        await _audit_auth_event(
            "auth.refresh",
            user,
            outcome=OUTCOME_DENIED,
            new_value={"detail": security_policy.DENY_IP},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=security_policy.DENY_IP,
        )

    if policy.session_max_hours is not None:
        created = _ensure_aware(getattr(family, "created_at", None))
        if created is not None and now - created > timedelta(hours=policy.session_max_hours):
            await revoke_family(family_id)
            await _audit_auth_event(
                "auth.refresh",
                user,
                outcome=OUTCOME_DENIED,
                new_value={"detail": security_policy.DENY_MAX_AGE},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=security_policy.DENY_MAX_AGE,
                headers={"WWW-Authenticate": "Bearer"},
            )

    window_denial = await security_policy.check_session_window(
        policy, str(family_id) if family_id else None
    )
    if window_denial is not None:
        await _audit_auth_event(
            "auth.refresh",
            user,
            outcome=OUTCOME_DENIED,
            new_value={"detail": window_denial},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=window_denial,
            headers={"WWW-Authenticate": "Bearer"},
        )

    getattr(user.role, "value", user.role)
    str(user.tenant_id)

    extra_claims: dict = {}
    # The auth_mode (password / demo) travels with the family — but a demo
    # stamp is re-checked against the live instance fact on every rotation
    # (S-7 gate above), so it dies with demo_mode (§13).
    if payload.get("auth_mode"):
        extra_claims["auth_mode"] = payload["auth_mode"]

    if payload.get("switched"):
        # Preserve the tenant-switch context, but re-validate the target
        # tenant is still there. Privileges (role) still come from the DB.
        scoped_tenant_id = payload.get("scoped_tenant_id") or payload.get("tenant_id")
        target = await get_tenant(scoped_tenant_id)
        if target is None or not getattr(target, "is_active", True):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Switched session target tenant is gone; switch back and re-authenticate.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        extra_claims.update(
            {
                "tenant_id": str(target.id),
                "original_tenant_id": payload.get("original_tenant_id"),
                "original_user_id": payload.get("original_user_id"),
                "switched": True,
                "scoped_tenant_id": str(target.id),
            }
        )

    # Rotate: the old jti dies (Redis + family hash) and a fresh pair is
    # issued inside the same family (rolling ≤ 30-day absolute cap).
    await token_store.revoke_refresh(str(user.id), str(jti))
    issued = await issue_session(
        user,
        auth_mode=extra_claims.pop("auth_mode", AUTH_MODE_PASSWORD),
        client_label=family.client_label or "refresh",
        extra_claims=extra_claims or None,
        family_id=str(family.id),
        absolute_expires_at=_ensure_aware(family.absolute_expires_at),
    )
    _set_auth_cookies(response, issued)
    return _token_response(issued)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    token_data: TokenRefresh | None = None,
    current_user: TokenData = Depends(get_current_user),
    token: str = Depends(get_token),
):
    """Revoke the presented refresh token, its family row AND the caller's
    access token.

    The access token's ``jti`` (from the cookie or Authorization header)
    is deleted from the session store, so the credential itself stops
    working immediately — not just at the next refresh (audit 2026-08 H3).

    §10: browsers present the refresh token via the ``ha_refresh`` cookie
    (body optional) and get the whole cookie triple cleared on the
    response; the CSRF middleware gates this route (double-submit) since
    it is a cookie-authenticated session action.
    """
    presented_refresh = (token_data.refresh_token if token_data else None) or (
        request.cookies.get(REFRESH_COOKIE)
    )
    payload = decode_refresh_token(presented_refresh) if presented_refresh else None
    if payload and payload.get("user_id") and payload.get("jti"):
        # A caller may only revoke their own family; a foreign token in the
        # body (e.g. leaked from another tenant's logs) is ignored.
        if str(payload["user_id"]) == str(current_user.user_id):
            await token_store.revoke_refresh(str(payload["user_id"]), str(payload["jti"]))
            if payload.get("fid"):
                await revoke_family(payload["fid"])
    access_payload = verify_access_token(token)
    if access_payload and access_payload.get("jti"):
        await token_store.revoke_session(str(access_payload["user_id"]), str(access_payload["jti"]))
        # No refresh body? Still drop the access token's own family row.
        if not (payload and payload.get("fid")) and access_payload.get("fid"):
            await revoke_family(access_payload["fid"])
    await _audit_auth_event("auth.logout", current_user)
    clear_session_cookies(response)
    return {"revoked": True}


@router.post("/logout-all")
async def logout_all(
    response: Response,
    current_user: TokenData = Depends(get_current_user),
):
    """Revoke every session for the current user — and kill their tokens.

    §8 revocation semantics: all ``auth_sessions`` rows are revoked, the
    Redis jti hot layer is wiped, and ``users.token_version`` is bumped so
    every outstanding token fails its ``ver`` check immediately. The §10
    cookie triple is cleared on the response (browsers).
    """
    count = await revoke_all_for_user(current_user.user_id)
    await bump_token_version(current_user.user_id)
    await token_store.revoke_everything(current_user.user_id)
    await _audit_auth_event("auth.logout_all", current_user, new_value={"revoked": count})
    clear_session_cookies(response)
    return {"revoked": count}
