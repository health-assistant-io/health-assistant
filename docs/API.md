# Health Assistant — REST API Reference

The Health Assistant REST API exposes the platform's clinical and operational
surface over JSON. It's the primary API for the **web frontend and the mobile
companion app**. Every endpoint is **tenant-scoped** (callers only see rows
whose `tenant_id` matches their own, except `SYSTEM_ADMIN`) and most
patient-scoped routes additionally verify ownership for the `USER` role.

> **External integrators:** this router is **not** a public API. It returns
> internal ORM-shape JSON and is intended for first-party clients only. To
> integrate an external system, use the [FHIR R4 facade](FHIR_R4_FACADE.md)
> (`/api/v1/fhir/R4/*`) with OAuth2 client-credentials + SMART-on-FHIR scopes
> — see [API Access Layers](API_LAYERS.md) for the decision guide.

**Base URL:** `http://localhost:8000/api/v1`
**Interactive docs (when running):** `http://localhost:8000/docs` (Swagger) and `http://localhost:8000/redoc`
**FHIR R4 interop surface:** `/api/v1/fhir/R4/*` — see [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md)

> This reference documents every router module mounted under
> `backend/app/api/v1/endpoints/`. Each section mirrors the FastAPI tag/grouping.
> 459 HTTP/WS handlers across 59 router modules (incl. `oauth` client management). For
> the always-current OpenAPI rendering, visit `http://localhost:8000/docs` while
> the server is running.

## Contents

- [Authentication & authorization](#authentication--authorization) — JWT, tenant/patient scoping, rate limits
- [OAuth2 — FHIR facade client auth](#oauth--fhir-facade-client-auth-oauth2--smart) — client-credentials + SMART scopes for external systems
- [Quick start — common flows](#quick-start--common-flows) — login → list → upload → extract → subscribe
- [Identity & access](#identity--access) — `users`, `tenants`, `settings`, `admin`, `admin/tenants`, `admin/integrations`
- [Patient clinical record](#patient-clinical-record) — `patients`, `patients/{id}/layouts`, `observations`, `examinations`, `documents`, `clinical-events`
- [Treatments & alerts](#treatments--alerts) — `medications`, `allergies`, `vaccines`, `notifications`, `notification-rules`
- [Catalogs & reference data](#catalogs--reference-data) — `catalogs`, `biomarkers`, `concepts`+`concept-edges`, `anatomy`, `search`+`instances`
- [Analytics](#analytics) — `analytics` (read surface), `telemetry` (ingest seam)
- [AI](#ai) — `ai-config`, `ai-assistance`
- [Integrations](#integrations) — discovery, OAuth, instances, inbound webhooks / API proxy
- [Operations](#operations) — `task-monitor`, `ws`, `export`, `import`, `doctors`, `organizations`
- [FHIR R4 facade](#fhir-r4-facade) — canonical interop surface (20 resource types)
- [Error handling](#error-handling) — domain exceptions, status code table
- [See also](#see-also)

---

## Authentication & authorization

> Per-surface security posture (which endpoints are public, cookie/CSRF
> semantics, the user-client classes incl. the integrations HMAC
> machine/device credential, and known gaps) is codified in
> [SECURITY.md](../SECURITY.md).

### JWT

**Browser clients (SPA) — cookie mode:** the web client
authenticates with HttpOnly cookies set by the auth endpoints; **tokens are
never stored in `localStorage`/`sessionStorage`** for browser clients.

| Cookie | Value | Flags |
|---|---|---|
| `nx_access` (`__Host-nx_access` when `HA_COOKIE_SECURE=true`) | session access JWT | `HttpOnly; SameSite=Lax; Secure` (with the flag); `Path=/` |
| `nx_refresh` | refresh JWT | `HttpOnly; SameSite=Lax; Secure`; `Path=/api/v1/auth` |
| `nx_csrf` | random CSRF secret | **JS-readable** (not HttpOnly); `SameSite=Lax`; `Path=/` |

`POST /auth/login`, `/auth/setup`, `/auth/demo-login`, `/auth/refresh`
(plus the tenant-switch endpoints) set the triple; `POST /auth/logout` and
`/auth/logout-all` clear it. This pass **also keeps returning the tokens in
the JSON body** — personal API clients (the Android app, CLI scripts) consume
them; the browser client ignores the body and rides the cookies.

**CSRF (double submit):** any non-GET `/api/*` request that carries the
session cookies must echo the `nx_csrf` cookie value in an `X-CSRF-Token`
header — mismatch or absence is a `403`. Requests with an `Authorization`
header (Bearer user clients) are exempt, as are the auth bootstrap
endpoints (`login` / `refresh` / `register` / `setup` / `demo-login`) and
health/docs.

**User clients — Bearer:** keep sending the header:

```
Authorization: Bearer <your-jwt-token>
```

Cookie and Bearer are both accepted everywhere (`get_token` checks the
cookie first); the Android/integrations HMAC bridge is unchanged.

Session JWTs are HS256, carry `user_id`, `tenant_id`, `role`, `sub`, a
`token_kind="session"` claim and a `jti` registered in the server-side session
store — so `logout`, user deletion and role changes take effect immediately (not
at token expiry). Default lifetime is **1 hour** (`HA_AUTH_ACCESS_TTL_MINUTES`);
refresh tokens are typed (`token_kind=refresh`), jti-tracked, rotated on every
use (rotation also re-stamps the cookies), and are **never** accepted as bearer
credentials. Refresh tokens re-validate the user row
from the DB (existence + `is_active` + current role/tenant) on every use.
Browsers present the refresh token via the `nx_refresh` cookie (body optional);
`get_session_user_ws` is the WebSocket variant and reads the token from the
`nx_access` cookie **or** the `["bearer", <jwt>]` `Sec-WebSocket-Protocol`
subprotocol (the `?token=` query-string fallback was removed — query strings
land in proxy logs). WS handshakes additionally verify the browser `Origin`
against the configured allow-list (`HA_WS_ALLOWED_ORIGINS`, default
same-origin + the CORS list) and reject anything else with `1008`.

**Signing keys (per-purpose key separation):** JWTs are signed with
per-purpose keys — `HA_SESSION_KEY` signs session-family tokens
(session/api/invite/download) and `HA_REFRESH_KEY` signs refresh tokens
only; verification is family-locked, so a token signed by the "wrong"
key always fails. The Fernet `HA_DATA_KEY` encrypts secrets at rest and
never signs anything. Servers must pin all three via env (weak/missing/
shared values refuse to boot) — see the key-separation note in
[INSTALL.md](INSTALL.md#security-checklist).

### Tenant & patient scoping

Every authenticated request carries a `tenant_id` (from the JWT). All
list/read/write endpoints are **tenant-scoped** — a caller can only see rows whose
`tenant_id` matches their own. Cross-tenant calls return `404` (not `403`) so the
existence of a row in another tenant is not leaked. The deliberate exception is
the **`SYSTEM_ADMIN`** role, which bypasses the tenant filter (operator visibility
— see [TENANCY_AND_USER_MANAGEMENT.md](TENANCY_AND_USER_MANAGEMENT.md)).

For patient-scoped endpoints (anything that takes a `patient_id`), a `USER`-role
caller must additionally be the patient's linked user
(`Patient.user_id == current_user.user_id`) or hold an **active care-team
grant** on that patient; `ADMIN` and `MANAGER` see all patients in their
tenant (a `manager_scope=assigned` tenant narrows its MANAGERs to own +
grants). The canonical check is `check_patient_access` in
`app/services/auth/access.py` — grant permission ranks as `read` < `write`
< `admin`, and a `read` grant never satisfies a write operation. See
[TENANCY_AND_USER_MANAGEMENT.md §2b](TENANCY_AND_USER_MANAGEMENT.md).

### Rate limiting

The auth endpoints are rate-limited per client IP via Redis fixed-window counters
(`app/core/rate_limit.py`). Degrades open if Redis is unreachable. The client IP
honors `X-Forwarded-For` **only** up to `HA_TRUSTED_PROXY_COUNT` (default `0` = direct
exposure, header ignored) — set it to your proxy-chain depth so spoofed headers
can't mint fresh buckets.

| Endpoint | Cap |
|---|---|
| `POST /auth/login` | 20 / minute |
| `POST /auth/register` | 5 / minute |
| `POST /auth/refresh` | 30 / minute |
| `POST /auth/invite` | 10 / minute |
| `POST /auth/mfa/verify` | 20 / minute |
| `POST /auth/mfa/enroll` | 10 / minute |
| `POST /auth/passkeys/options` | 20 / minute |
| `POST /auth/passkeys/verify` | 20 / minute |
| `POST /auth/step-up/passkeys/options` | 10 / minute |
| `POST /auth/step-up/passkeys/verify` | 10 / minute |

### `auth` — login, register, invite, tokens

| Method | Path | Body | Response | Notes |
|---|---|---|---|---|
| `POST` | `/auth/login` | `OAuth2PasswordRequestForm` (`username`, `password`) | `TokenResponse` | Issues access + refresh JWTs. |
| `POST` | `/auth/setup` | `SetupRequest` (`email`, `password`, `tenant_name`, `setup_token?`) | `TokenResponse` | First-run bootstrap — creates the initial tenant + `SYSTEM_ADMIN`. Only callable while uninitialized (410 after). Protected by the one-time setup token on every non-dev instance, including localhost (see `app/core/setup_token.py`). |
| `POST` | `/auth/register` | `UserRegister` | `UserResponse` | **Join** an existing tenant — requires `tenant_id` + a valid **single-use** `invite_token` JWT (consumed atomically on first use; TTL capped at 30 days). The invite is validated *before* the email-exists check so unauthenticated callers can't enumerate emails. Bootstrap lives at `/auth/setup`. |
| `POST` | `/auth/invite` | (none; query: `tenant_id?`, `email?`, `role=user\|manager\|admin`, `expires_days=7`, capped at 30) | `{invite_token, tenant_id, role, expires_in_days}` | `ADMIN` / `MANAGER` / `SYSTEM_ADMIN` only. Non-`SYSTEM_ADMIN` can only mint for own tenant. Tokens are single-use; `SYSTEM_ADMIN` cannot be granted via invite. |
| `GET` | `/auth/validate` | (none) | `{valid: true, user_id}` | Lightweight check that the JWT is still valid. |
| `GET` | `/auth/setup-status` | (none) | `{initialized: bool, …}` | Whether first-run setup has been claimed (drives the login-page redirect to the setup wizard). |
| `POST` | `/auth/refresh` | `{refresh_token}` (optional for browsers — the `nx_refresh` cookie is used when the body omits it) | `TokenResponse` | **Rotates** the refresh token (rotation also re-stamps the cookie triple) and re-validates the user row from the DB: deleted/deactivated users are refused, and the new claims (email/tenant/role) are rebuilt from the database — a role change takes effect at the next refresh. Switched SYSTEM_ADMIN sessions preserve + re-validate their target tenant. |
| `POST` | `/auth/logout` | `{refresh_token}` (optional for browsers — cookie fallback) | `{revoked: true}` | Revokes the presented refresh token's `jti` **and** the caller's live access-token `jti` — the credential itself stops working immediately. Clears the session cookies. CSRF-gated for cookie sessions (double submit). |
| `POST` | `/auth/logout-all` | (none) | `{revoked: <count>}` | Revokes every refresh **and** session access token for the calling user, and bumps `token_version` — every credential of the account dies, including the caller's (sign in again). For "sign out the other devices but keep me in", use `POST /me/sessions/revoke-others`. |
| `POST` | `/auth/step-up` | `StepUpRequest` (`action`, `password?`, `code?`) | `{action, methods, issued_at, expires_at, expires_in}` | **Step-up re-authentication** (program 12C1) for the five sensitive actions: `role_change`, `export`, `break_glass`, `grant_admin`, `mfa_change`. The factors are the account's own — the local password when one is set, TOTP/recovery when MFA is enrolled (both when both exist); an SSO-only account may use a fresh IdP-asserted second factor (`methods: ["federated"]`). Records a family-bound proof (`stepup:{fid}:{action}`, 10-minute window, one action, one sign-in family) — see the typed refusals below. Rate-limited (10/min per IP); audited (`auth.step_up`). |

**Typed step-up refusals.** A gated action without a live proof answers
`403 {"code": "step_up_required", "action": "<action>", "expires_in": 600}`
(`methods` is included when a factor of the request body is missing); an
account with no usable factor at all answers
`403 {"code": "step_up_unavailable", "action": …, "reason": "no_factor" |
"no_session_family" | "account_unavailable"}`. Wrong passwords/codes answer
`401`. Gated surfaces: `PUT /users/{id}` only when the role actually
changes, `POST /export` (every scope), `POST /admin/access-elevations` and
its `/approve` (deny/revoke only narrow access and stay ungated),
`POST /patients/{id}/access` (+ `PATCH` raising a grant to `admin`), and
every factor change (`POST /me/mfa/enroll`, `POST /me/mfa/confirm`, the
`/me/mfa/passkeys` add/delete routes — `mfa_change`). A registered passkey can
prove the same actions at `POST /auth/step-up/passkeys/{options,verify}`
(`methods: ["webauthn"]`). Ordinary reads never require a proof.

#### TOTP MFA

Optional per-account two-factor authentication (RFC 6238 TOTP — SHA1, 6 digits,
30 s step, ±1 step drift; any authenticator app works via the standard
`otpauth://` provisioning URI). The shared secret is stored **encrypted at rest
under the `HA_DATA_KEY` family** (Fernet, `enc::` prefix — never plaintext);
8 single-use recovery codes are bcrypt-hashed like passwords and shown exactly
once at enrollment. MFA gates **login only** — live sessions and the Bearer
clients (Android app, OAuth facade clients) are unaffected.

Login flow when MFA is active: `POST /auth/login` with correct credentials
answers **401** with a machine-readable challenge instead of tokens —
`{"detail": "mfa_required", "mfa_token": "<JWT>", "enrollment_needed": false}`.
The `mfa_token` is a dedicated `mfa_challenge` JWT kind (session key family,
5-minute TTL, single-use — consumed by the first successful verify; it is
mutually exclusive with session tokens and never authenticates the API).
`POST /auth/mfa/verify` with `{mfa_token, code}` (TOTP or a recovery code)
issues the normal session (cookie triple + body tokens). **Wrong codes
count toward the account lockout** (same counter as wrong passwords — 5 strikes ⇒
423 for 15 min); the counter resets when the password succeeds and again when
the challenge passes.

Admin-forced MFA (`mfa_enforced`, "promoted for institute use"): the member's
next login challenge carries `enrollment_needed: true` only when the account
has no factor (TOTP or passkey); `POST /auth/mfa/enroll`
(with the challenge token) returns the one-time provisioning payload, and the
verify call confirms the enrollment and signs in in one step.

Tenant security policy: the same challenge flow fires when
the **tenant policy** lists the caller's role in `mfa_required_roles` — and for
`SYSTEM_ADMIN` logins always (that role can never waive MFA; the bootstrap
session from `POST /auth/setup` is the only pre-enrollment session). The
policy can also require MFA without any account flag, so the disable card on
`/me/mfa` refuses while the policy lists the caller's role. See
[tenants](#tenants--tenant-self-service) for the policy surface; session
policy (idle/max-age windows, concurrent-session limit, IP allowlist) is
enforced on verification, refresh and issuance with the typed denials
`session_idle_expired` / `session_max_age_expired` / `ip_not_allowed`.

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/auth/mfa/verify` | none (challenge token) | `MFAVerifyRequest` (`mfa_token`, `code`) | `TokenResponse` | Answers a login challenge. Consumes the challenge (single-use); 401 on wrong code, 423 when the account lockout trips. Rate-limited (20/min per IP, per-account window). |
| `POST` | `/auth/mfa/enroll` | none (challenge token) | `{mfa_token}` | `MFAEnrollResponse` | Forced-enrollment provisioning — only when the login challenge carried `enrollment_needed: true` (400 otherwise). |

Self-service surface (settings → Security), mounted under `/me` to match the
`/me/sessions` identity neighbor:

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/me/mfa` | any | — | `MFAStatusResponse` | `{enabled, enforced, pending}` — drives the settings card. |
| `PATCH` | `/me/password` | any | `{current_password, new_password}` | `204` | Password rotation. Wrong current passwords share the login lockout; success bumps `token_version` and revokes every token family, so the caller signs in again. Rate-limited (5/min per IP). |
| `GET` | `/me/sessions` | any | — | list of session rows | Live sign-in families: `id`, `client_label`, `ip`, `user_agent`, `last_seen_at` (≥60 s throttled activity stamp), `created_at`, `expires_at`, `absolute_expires_at`, and `current` for the family serving this request. |
| `DELETE` | `/me/sessions/{session_id}` | any | — | `{revoked: true}` | Revoke one device's family (its refresh chain dies at once; revoking the caller's own family also kills its bearer). Hidden 404 for a family the caller does not own. |
| `POST` | `/me/sessions/revoke-others` | any | — | `{revoked: <count>}` | Sign out every device **except** this one — no `token_version` bump, so the caller keeps working (unlike `/auth/logout-all`). Audited (`session.revoke_others`). |
| `POST` | `/me/mfa/enroll` | any + `mfa_change` step-up | — | `MFAEnrollResponse` | One-time provisioning payload (secret + otpauth URI + recovery codes); pending until confirmed. 409 when already active; 403 `step_up_required` (action `mfa_change`, `methods` naming the acceptable factors) without a proof — an enrolled TOTP secret is itself a step-up factor, so enrollment may not be self-authorized by a bare session. Audit-logged as `mfa.enroll`. |
| `POST` | `/me/mfa/confirm` | any + `mfa_change` step-up | `{code}` | `MFAStatusResponse` | Code check against the pending secret ⇒ active. 403 `step_up_required` without the proof; 400 on a wrong code. Audit-logged as `mfa.confirm`. |
| `DELETE` | `/me/mfa` | any | `{password}` | `MFAStatusResponse` | Password-confirmed TOTP removal. **403 while `mfa_enforced` or a tenant-policy-listed role applies and no other factor remains** (a registered passkey keeps MFA satisfied); the admin/policy requirement itself is never self-cancellable. Audit-logged as `mfa.disable`. |

Admin force — `PATCH /admin/tenants/{tenant_id}/users/{user_id}/mfa` with
`{"enforced": true|false}` (`ADMIN` inside their own tenant, `SYSTEM_ADMIN`
anywhere; `MANAGER`/`USER` get 403). Audit-logged as `user.mfa_enforce` with
old/new values. Clearing the requirement never removes an enrolled secret.
`GET /users` and the tenant user list expose `mfa_enabled` / `mfa_enforced`
booleans for the admin UI.

#### Passkeys / WebAuthn (program 12C2)

Passkeys are WebAuthn Level 2 credentials (platform authenticators such as
Touch ID/Windows Hello, or roaming security keys) stored in
`webauthn_credentials` (public key + signature counter only — the private key
never leaves the authenticator). They are a **second factor and a login
method**: a user-verified assertion satisfies the tenant MFA policy exactly
like TOTP, and password login then offers both methods. Every ceremony
requires **user verification** and uses a **single-use, five-minute**
challenge stored in the hot layer (consumed before signature verification, so
replay fails even after a malformed attempt).

**Relying-party configuration.** Set `HA_WEBAUTHN_RP_ID` (bare registrable
domain) and `HA_WEBAUTHN_ORIGIN` (comma-separated exact page origins; https
outside development/test). In development/test the defaults are the
`FRONTEND_URL`/`APP_URL` origins with `localhost` as the RP id; on any other
environment an unconfigured instance answers `503
{"detail":{"code":"webauthn_not_configured"}}`. RP/origin mismatches,
missing user verification, unknown credentials, replayed/expired challenges
and signature-counter regressions are typed refusals below.

Registration (authenticated; requires a recent `mfa_change` step-up proof —
password/TOTP or an existing passkey):

| Method | Path | Body | Response | Notes |
|---|---|---|---|---|
| `POST` | `/me/mfa/passkeys/options` | — | `{options, expires_in}` | `options` is the `PublicKeyCredentialCreationOptions` object: pass it as `navigator.credentials.create({publicKey: options})`. `excludeCredentials` lists already-registered credentials; UV required. 403 `step_up_required` (action `mfa_change`) without a proof. |
| `POST` | `/me/mfa/passkeys/verify` | `{credential, name}` | `PasskeyResponse` | `credential` is the `PublicKeyCredential` JSON (`{id, rawId, type, response:{clientDataJSON, attestationObject, transports?}}`). Verifies the attestation against the configured RP/origins, requires UV, stores the credential. `409 webauthn_credential_exists` when already registered; `400 webauthn_invalid_response` / `webauthn_challenge_expired`. Audited `mfa.passkey_register`. |
| `GET` | `/me/mfa/passkeys` | — | `{items: PasskeyResponse[]}` | `id`, `name`, `transports`, `aaguid`, `backup_eligible`, `backup_state`, `created_at`, `last_used_at`, `disabled`. No key material. |
| `DELETE` | `/me/mfa/passkeys/{credential_id}` | — | `204` | Requires the `mfa_change` proof. Refused `403` when `mfa_enforced`/a policy-listed role applies and this is the **last** factor (enroll another factor first). Audited `mfa.passkey_delete`. |

Passkey sign-in (public; the browser ceremony is `navigator.credentials.get`):

| Method | Path | Body | Response | Notes |
|---|---|---|---|---|
| `POST` | `/auth/passkeys/options` | `{email?}` | `{options, expires_in}` | `email` names the account (`allowCredentials` is filled); omit it for a usernameless/discoverable ceremony. An unknown/inactive email gets the same shape with an empty `allowCredentials` (no enumeration). UV required. Rate-limited 20/min. |
| `POST` | `/auth/passkeys/verify` | `{credential}` | `TokenResponse` (+ §10 cookies) | Verifies the assertion, advances the counter, enforces the tenant IP allowlist, and issues a normal session. The passkey satisfies MFA (no `mfa_required` challenge). The session carries `auth_mode="password"` (the contract's closed local-auth value) plus `amr:["hwk","user"]` (or `swk` for a synced credential) and `auth_time`. A non-zero counter that does not advance answers `403 webauthn_clone_detected`, disables the credential and audits `auth.passkey_clone_detected`. Legacy first-factor failures: `401 webauthn_invalid_response` / `webauthn_unknown_credential`, `400 webauthn_challenge_expired`, `423` while the account lockout is active, `403 ip_not_allowed`. Audited `auth.passkey_login` and `auth.login`. |

Step-up with a passkey (authenticated; any closed action —
`role_change | export | break_glass | grant_admin | mfa_change`):

| Method | Path | Body | Response | Notes |
|---|---|---|---|---|
| `POST` | `/auth/step-up/passkeys/options` | `{action}` | `{action, options, expires_in}` | Bound to the caller's sign-in family **and** the named action. 403 `step_up_unavailable` (`no_session_family`) for a familyless token; 422 for an unknown action. |
| `POST` | `/auth/step-up/passkeys/verify` | `{action, credential}` | `{action, methods:["webauthn"], issued_at, expires_at, expires_in}` | Verifies the assertion and writes the ordinary `stepup:{fid}:{action}` proof (10-minute window, one action, one family). The same gated endpoints (`PUT /users/{id}`, `POST /export`, elevations, admin grants) now accept it. Audited `auth.step_up`. |

**MFA policy and recovery.** `GET /me/mfa` is factor-aware:
`{enabled, enforced, pending, totp_enabled, passkey_enabled, passkey_count}` —
`enabled` is TOTP **or** passkey. `POST /auth/login` answers
`401 {"detail":"mfa_required","mfa_token","enrollment_needed","methods":["webauthn","totp"]}`
(`methods` lists the factors available; either completes the sign-in). A
passkey-only account under an MFA-requiring policy gets
`enrollment_needed:false` and `methods:["webauthn"]`. Disabling TOTP or
deleting a passkey is allowed while another factor remains; the **last**
factor cannot be removed while `mfa_enforced` or a policy-listed role
requires MFA. TOTP recovery codes remain the TOTP recovery path; if a
passkey device is lost, sign in with another factor (TOTP/recovery, or a
fresh SSO sign-in for SSO-only accounts) and register a replacement, or use
the host recovery runbook in `SECURITY.md`. Every ceremony, refusal, clone
alarm and delete is audited (`mfa.passkey_register`, `mfa.passkey_delete`,
`auth.passkey_login`, `auth.passkey_clone_detected`, `auth.step_up`).

#### Register examples

```json
// Join an existing tenant (single-use invite required; bootstrap uses POST /auth/setup)
{
  "email": "newmember@family.com",
  "password": "securepassword123",
  "tenant_id": "123e4567-e89b-12d3-a456-426614174001",
  "invite_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

Login response shape:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

See [TENANCY_AND_USER_MANAGEMENT.md](TENANCY_AND_USER_MANAGEMENT.md) for the full
invite + bootstrap flow and curl examples.

### `oauth` — FHIR facade client auth (OAuth2 + SMART)

External systems authenticate against the public FHIR R4 facade
(`/api/v1/fhir/R4/*`) via the OAuth2 **client-credentials** grant (RFC 6749
§4.4) with SMART-on-FHIR scopes. An administrator registers a client (the
Admin "API Clients" UI or `POST /oauth/clients`); the external system then
exchanges its credentials for a short-lived access token and sends it as a
Bearer against the facade. The domain REST API above does **not** accept
these tokens — see [API Access Layers](API_LAYERS.md).

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/oauth/token` | client creds | form or JSON: `grant_type=client_credentials`, `client_id`, `client_secret` (or HTTP Basic); `scope?` | `{access_token, token_type, expires_in, scope, tenant_id}` | RFC 6749 §4.4. `scope` is intersected with the client's registered scopes; an ungranted scope → `400 invalid_scope`. |
| `POST` | `/oauth/revoke` | any session token | `{token}` | `{revoked: true}` | RFC 7009 (best-effort jti blocklist). Always 200. |
| `GET` | `/oauth/clients` | `ADMIN`/`MANAGER`/`SYSTEM_ADMIN` | `search?`, `is_active?` | `List[OAuthClientResponse]` | Tenant-scoped; SYSTEM_ADMIN sees all. Never returns the secret. |
| `POST` | `/oauth/clients` | `ADMIN`/`MANAGER`/`SYSTEM_ADMIN` | `OAuthClientCreate` (`display_name`, `scopes[]`, `tenant_id?`, `bound_patient_id?`) | `OAuthClientCreateResponse` (`201`) | Returns the plaintext `client_secret` **exactly once**. `patient/` scopes require `bound_patient_id`. |
| `POST` | `/oauth/clients/{id}/rotate-secret` | admin | — | `{client_id, client_secret}` | New plaintext secret; old hash overwritten. |
| `PATCH` | `/oauth/clients/{id}` | admin | `OAuthClientUpdate` (`display_name?`, `scopes?`, `is_active?`, `bound_patient_id?`) | `OAuthClientResponse` | Scope changes apply on the next token mint. |
| `DELETE` | `/oauth/clients/{id}` | admin | — | `{message}` | Hard delete. Outstanding tokens stay valid until expiry. |
| `GET` | `/.well-known/smart-configuration` | **none** | — | smart-configuration JSON | SMART discovery: token endpoint, grant types, supported scopes. |

**SMART scope syntax:** `<context>/<resource>.<permission>` — `context` ∈
`{system, patient}` (`user/` + `launch` arrive with the Stage-4 authorize
flow); `resource` is any registered FHIR type or `*`; `permission` is
`read`/`write`/`*`. Examples: `system/Observation.read`, `system/*.*`,
`patient/Condition.read`. Read/search needs a `.read` scope; create/update/
delete needs `.write`. See [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md) for the
full scope model and the patient-compartment rules.

Token mint:

```bash
curl -s -X POST http://localhost:8000/api/v1/oauth/token \
  -d "grant_type=client_credentials" \
  -d "client_id=ci_..." \
  -d "client_secret=..." \
  -d "scope=system/Observation.read" | jq
# → { "access_token": "eyJ...", "token_type": "Bearer",
#     "expires_in": 3600, "scope": "system/Observation.read", "tenant_id": "..." }
```

Then use the token against the facade:

```bash
curl -s http://localhost:8000/api/v1/fhir/R4/Observation?patient=Patient/{id} \
  -H "Authorization: Bearer <access_token>" | jq
```

---

## Quick start — common flows

End-to-end examples covering the most common integrator journeys. Every example
uses `curl` with `$TOKEN` standing in for an `Authorization: Bearer <jwt>` value
obtained from `/auth/login`.

### Flow 1 — Login, list patients, read observations

```bash
# 1. Login (obtain access + refresh tokens)
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=alice@example.com" \
  -d "password=secret123" | jq
# → { "access_token": "eyJ...", "refresh_token": "eyJ...",
#     "token_type": "Bearer", "expires_in": 3600 }

export TOKEN="<access_token from above>"

# 2. List the caller's patients (USER → own patients; ADMIN → tenant-wide)
curl -s http://localhost:8000/api/v1/patients?limit=10 \
  -H "Authorization: Bearer $TOKEN" | jq
# → [ { "id": "5e0c...", "name": [...], "birth_date": "1985-04-12", ... } ]

# 3. Fetch the latest 50 biomarker readings for one patient
curl -s "http://localhost:8000/api/v1/observations?patient_id=5e0c...&limit=50" \
  -H "Authorization: Bearer $TOKEN" | jq '.[] | {code: .code.coding[0].code,
                                                 value: .value_quantity.value,
                                                 unit: .value_quantity.unit,
                                                 effective_date}'
```

### Flow 2 — Upload a document, trigger extraction, poll until done

```bash
PATIENT_ID="5e0c..."
EXAM_ID="..."

# 1. Upload a PDF (or image / DICOM / DOCX / TXT). 202 Accepted on success.
curl -s -X POST http://localhost:8000/api/v1/documents \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@lab_report.pdf" \
  -F "patient_id=$PATIENT_ID" \
  -F "examination_id=$EXAM_ID" \
  -F "include_in_extraction=true" | jq
# → { "id": "doc-uuid", "status": "uploaded", "progress": 0, ... }

DOC_ID="<id from above>"

# 2. Trigger OCR + AI extraction explicitly (also auto-fires on upload when
#    include_in_extraction=true; this is the manual re-trigger path)
curl -s -X POST http://localhost:8000/api/v1/documents/$DOC_ID/extract \
  -H "Authorization: Bearer $TOKEN"
# → { "job_id": "...", "message": "Extraction triggered" }

# 3. Poll until status == completed (or use the /ws/tasks WebSocket for push)
until [ "$(curl -s http://localhost:8000/api/v1/documents/$DOC_ID/extract/status \
            -H "Authorization: Bearer $TOKEN" | jq -r .status)" = "completed" ]; do
  sleep 5
done

# 4. Read the biomarker observations the pipeline created
curl -s "http://localhost:8000/api/v1/observations?patient_id=$PATIENT_ID&limit=20" \
  -H "Authorization: Bearer $TOKEN" | jq
```

For multi-document exams, the pipeline auto-fires cumulative extraction once all
`include_in_extraction=true` documents are OCR'd (race-protected via
`pg_try_advisory_xact_lock`).

### Flow 3 — Subscribe to push notifications + open the live WebSocket

```bash
# 1. Fetch the VAPID public key (no auth)
curl -s http://localhost:8000/api/v1/notifications/vapid-public-key | jq
# → { "public_key": "BPn..." }

# 2. Register a Web Push subscription (body = SubscribeRequest envelope)
curl -s -X POST http://localhost:8000/api/v1/notifications/subscribe \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "subscription": {
          "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/...",
          "keys": {"p256dh": "...", "auth": "..."}
        },
        "device_id": "laptop-chrome",
        "user_agent": "Mozilla/5.0 ..."
      }' | jq
# → { "status": "subscribed", "id": "..." }
```

Then in JavaScript, open the live notification stream over the WebSocket
subprotocol auth (the JWT never lands in URL logs):

```javascript
const ws = new WebSocket("ws://localhost:8000/api/v1/ws/notifications", [
  "bearer",
  accessToken,
]);
ws.onmessage = (e) => console.log(JSON.parse(e.data));
// server ping every 30 s; reconnect with 5 s backoff on drop
```

### Flow 4 — Create a biomarker threshold alert

```bash
# Fire a notification whenever the patient's fasting glucose exceeds 7.0 mmol/L
curl -s -X POST http://localhost:8000/api/v1/notification-rules \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "patient_id": "5e0c...",
        "biomarker_id": "9b1c...",
        "name": "High fasting glucose",
        "condition": { "operator": ">", "value": 7.0 },
        "channels": ["IN_APP", "PUSH"],
        "enabled": true
      }' | jq
```

The rule is evaluated on every observation ingestion
(`fhir_service.create_observation` → `notification_rule_service.evaluate_and_fire`).
Force-fire it once to verify wiring:

```bash
RULE_ID="<id from above>"
curl -s -X POST http://localhost:8000/api/v1/notification-rules/$RULE_ID/test \
  -H "Authorization: Bearer $TOKEN" | jq
```

### Flow 5 — Enable an integration and trigger a manual sync

```bash
PATIENT_ID="5e0c..."

# 1. Discover what's available (admin must have enabled it globally first)
curl -s http://localhost:8000/api/v1/integrations/available \
  -H "Authorization: Bearer $TOKEN" | jq '.[] | {domain, name, version}'

# 2. Read the config flow schema for the integration you want
curl -s http://localhost:8000/api/v1/integrations/withings/config-flow \
  -H "Authorization: Bearer $TOKEN" | jq

# 3. Submit config (provider-specific payload) — returns the new instance id
curl -s -X POST "http://localhost:8000/api/v1/integrations/withings/config-flow?patient_id=$PATIENT_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"access_token": "...", "refresh_token": "..."}' | jq
# → { "id": "int-uuid", "status": "ACTIVE", ... }

INTEGRATION_ID="<id from above>"

# 4. Manual sync (also auto-runs every sync_interval via the beat)
curl -s -X POST "http://localhost:8000/api/v1/integrations/instance/$INTEGRATION_ID/sync?patient_id=$PATIENT_ID" \
  -H "Authorization: Bearer $TOKEN" | jq
# → { "message": "Sync completed", "metrics_synced": 12, "pulled": 47,
#     "dropped_invalid": 0, "status": "ACTIVE", "last_synced_at": "..." }
```

For OAuth-based providers (Garmin, Withings, Apple Health), substitute steps 3–4
with the OAuth start/callback flow — see
[INTEGRATIONS_FRAMEWORK.md](INTEGRATIONS_FRAMEWORK.md).

### Flow 6 — Export the full patient record and re-import it

```bash
PATIENT_ID="5e0c..."

# 1. Kick off a full-backup export (async — enqueues Celery)
curl -s -X POST http://localhost:8000/api/v1/export \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "scope": "patient",
        "export_type": "full_backup",
        "patient_ids": ["'"$PATIENT_ID"'"]
      }' | jq
# → { "job_id": "...", "status": "pending", ... }

JOB_ID="<id from above>"

# 2. Poll status, then download the resulting .zip
until [ "$(curl -s http://localhost:8000/api/v1/export/jobs/$JOB_ID \
            -H "Authorization: Bearer $TOKEN" | jq -r .status)" = "completed" ]; do
  sleep 5
done
curl -s -o backup.zip http://localhost:8000/api/v1/export/jobs/$JOB_ID/download \
  -H "Authorization: Bearer $TOKEN"

# 3. Restore into another instance (also async — check via GET /import/jobs/{id})
curl -s -X POST http://localhost:8000/api/v1/import/backup \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@backup.zip" | jq
```

See [EXPORT_IMPORT.md](EXPORT_IMPORT.md) for format details, scopes, and
cross-tenant id remapping.

---

## Identity & access

### `me/access` — the caller's authorization identity

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/me/access` | session (auth-only) | — | `{user, roles, permissions, tenant, patient_grants, pending_grants}` | The health-owned access surface: the signed-in caller's role list, effective permission set (from the code catalog), tenant and incoming care-team grants (`patient_grants` = accepted, `pending_grants` = awaiting acceptance; minimal fields with the patient display name). Read per request — a grant created or revoked mid-session is honored on the next fetch. The client derives `can(permission)` from it; the server keeps enforcing — the payload is a mirror, never the gate. |
| `GET` | `/me/access/grants` | session (auth-only) | — | `{active: [grant], pending: [grant]}` | The caller's incoming grants for the self-service page (grant id, patient id + name, permission, source, status, timestamps). |
| `POST` | `/me/access/grants/{grant_id}/accept` | session (auth-only) | — | `{id, status}` | The invited staff member accepts a pending share (pending → `active`). Re-validated at accept time: `403` for anyone but the grantee or after the grantee lost its staff role, `409` if no longer pending or the tenant switched `allow_patient_share` off. |
| `POST` | `/me/access/grants/{grant_id}/decline` | session (auth-only) | `{reason?}` | `{id, status}` | Decline a pending share (pending → `declined`). Same 403/409 rules. |
| `GET` | `/me/elevations` | session (auth-only) | — | `[elevation]` | The caller's own break-glass elevations, newest first (pending, live and settled) — the self-service view of the windows they requested. |

```bash
curl -H "Authorization: Bearer $ACCESS" /api/v1/me/access
# {"user":{"id":"…","email":"…","full_name":"…","role":"ADMIN"},
#  "roles":["ADMIN"],
#  "permissions":["audit:read","catalog:create", …],
#  "tenant":{"id":"…","name":"…"},
#  "patient_grants":[{"id":"…","patient_id":"…","patient_name":"…",
#                     "permission":"read","source":"care_team","status":"active", …}],
#  "pending_grants":[]}
```

### `me/api-tokens` — scoped personal access tokens (Program Phase 12C2B)

Opaque, owner-bound credentials for CLI/scripts/home-automation clients
(`pat` token kind). The plaintext is returned **once**;
the route gate authorizes with the token's permission subset (not the
owner's role), re-intersected with the owner's live role on every request,
and the patient scope is intersected with the owner's live reach. A token
can never mint another token, a session, a refresh or a step-up proof.

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/me/api-tokens` | session (never a PAT) | — | `[ApiToken]` | The caller's tokens, newest first (revoked/expired included). Never the plaintext or its hash. |
| `GET` | `/me/api-tokens/permissions` | session | — | `{permissions: [{permission, scope}], patient_scopes}` | The caller's assignable catalog (the picker's source of truth); server re-validates every write. |
| `POST` | `/me/api-tokens` | session | `{name, permissions[], patient_scope?, patient_ids?, expires_at?}` | `{token, api_token}` (`201`) | Creates the token. `token` is the one-time plaintext (`ha_pat_<prefix>_<secret>`); `api_token` is the hash-free projection. `permissions` must be a non-empty subset of the caller's catalog. |
| `GET` | `/me/api-tokens/{id}` | session | — | `ApiToken` | One owned token; another owner's id is a hidden `404`. |
| `PATCH` | `/me/api-tokens/{id}` | session | `{name?, permissions?, patient_scope?, patient_ids?, expires_at?}` | `ApiToken` | Re-validates the subset and allowlist. A revoked token cannot be updated. |
| `POST` | `/me/api-tokens/{id}/rotate` | session | — | `{token, api_token}` | Issues a replacement and revokes the predecessor in one transaction (`rotated_from` on the new row). |
| `DELETE` | `/me/api-tokens/{id}` | session | — | `204` | Revokes; the bearer stops resolving at once (the row stays as the ledger). |

`ApiToken` = `{id, tenant_id, owner_user_id, name, token_prefix,
permissions, patient_scope, patient_ids, expires_at, last_used_at,
revoked_at, rotated_from, created_by, created_at, updated_at}`.

Usage is the ordinary bearer header — a PAT carries the owner's tenant and
nothing else:

```bash
curl -H "Authorization: Bearer $PAT" /api/v1/patients
```

`patient_scope`: `all` = every patient the owner can reach at use time;
`granted` = only the `patient_ids` the owner can still reach; `none` = no
patient-scoped access (non-clinical scopes only). Presenting a PAT on a
credential-management route (`/auth/*`, password, MFA/passkeys, sessions,
token management, step-up) answers `403`; a step-up-gated action answers
`403 step_up_required`.

### `integration-principals` — scoped service identities (Program Phase 13)

Integrations and extensions act as a scoped principal, never
as their owner's role. A principal carries a permission subset (also bounded
by the extension manifest's declared capabilities), a patient scope and a
lifecycle. Tenant admins (`integration:manage` / `tenant:manage`) manage the
whole tenant; an integration owner manages the principal bound to their
integration. All routes are session-only — a scoped credential cannot manage
principals.

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/integration-principals` | session | — | `[IntegrationPrincipal]` | Tenant admins see every principal in the tenant; other callers see their own. |
| `GET` | `/integration-principals/permissions?integration_id=` | session | — | `{permissions: [{permission, scope}], patient_scopes}` | The assignable set for that integration (owner ∩ manifest) or the caller's own catalog. |
| `POST` | `/integration-principals` | session | `{name, kind?, integration_id?, owner_user_id?, scopes[], patient_scope?, patient_ids?, expires_at?}` | `{secret, integration_principal}` (`201`) | `kind=integration` requires `integration_id` (only the owner or a tenant admin). The secret is `ha_int_<prefix>_<secret>`, shown once. |
| `GET` | `/integration-principals/{id}` | session | — | `IntegrationPrincipal` | Another owner's id is a hidden `404`. |
| `POST` | `/integration-principals/{id}/rotate` | session | — | `{secret, integration_principal}` | Issues a replacement and marks the predecessor `rotated` in one transaction. |
| `DELETE` | `/integration-principals/{id}` | session | — | `204` | Revokes; the secret stops resolving at once (the row stays as the ledger). |

`IntegrationPrincipal` = `{id, tenant_id, owner_user_id, name, kind,
integration_id, scopes, patient_scope, patient_ids, status, secret_prefix,
last_used_at, expires_at, revoked_at, rotated_from, created_by, created_at,
updated_at}`. A principal is used exactly like a personal access token:

```bash
curl -H "Authorization: Bearer $PRINCIPAL_SECRET" /api/v1/patients
```

The route gate authorizes with the principal's own scopes (re-intersected
with the owner's live role and the manifest on every use, capped at tenant);
patient scope bounds every patient-scoped read/write. Revoked, rotated,
expired, cross-tenant and scope-less credentials answer `401`.

### `users`

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/users/me` | any | — | `UserResponse` | Returns the current user. Handles switched `SYSTEM_ADMIN` via `original_tenant_id`. |
| `GET` | `/users/{user_id}` | self / admin / manager / SYSTEM_ADMIN | — | `UserResponse` | Single-user fetch. |
| `GET` | `/users` | `ADMIN` / `MANAGER` | — | `List[UserResponse]` | List users in the caller's tenant. |
| `POST` | `/users` | `ADMIN` / `SYSTEM_ADMIN` | `UserCreate` | `UserResponse` (`201`) | Create user in a tenant. `tenant_id` defaults to caller's tenant. |
| `PUT` | `/users/{user_id}` | self / admin / manager | (query: `email?`, `role?`, `settings?`) | `UserResponse` | Role change requires admin. |
| `DELETE` | `/users/me` | any | `{password}` | `{message, erasure_id}` | Self-service erasure. Requires the account password and is refused for the last `SYSTEM_ADMIN`. |
| `POST` | `/users/{user_id}/reset-password` | `ADMIN` / `SYSTEM_ADMIN` | `{new_password}` | `204` | Admin reset. Revokes every token family for the target. A tenant `ADMIN` cannot reset a `SYSTEM_ADMIN`. |
| `POST` | `/users/{user_id}/force-logout` | `ADMIN` / `SYSTEM_ADMIN` | — | `{revoked: true}` | Bumps the target's `token_version` and revokes all sessions. |
| `DELETE` | `/users/{user_id}` | `ADMIN` / `SYSTEM_ADMIN` | — | `{message}` | Tenant-isolated for non-system admins. |

### `tenants` — tenant self-service

> Tenant **create / list / hard-delete / deactivate / reactivate / switch** are
> SYSTEM_ADMIN-only and live under `/admin/tenants` (see [Admin](#admin--admin-tenants)).
> This router exposes only what a tenant member needs to see / update their own
> tenant.

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/tenants` | any | — | `TenantResponse` | Returns the caller's own tenant. |
| `GET` | `/tenants/{tenant_id}` | self / SYSTEM_ADMIN | — | `TenantResponse` | Non-`SYSTEM_ADMIN` can only read their own tenant. |
| `PATCH` | `/tenants/{tenant_id}` | `ADMIN` / `SYSTEM_ADMIN` | `TenantUpdate` (name, description, settings) | `TenantResponse` | Self-service update only. Use `PATCH /admin/tenants/{id}` for the full surface. |
| `GET` | `/tenants/{tenant_id}/security-policy` | `ADMIN` / `MANAGER` / `SYSTEM_ADMIN` (settings:read @ tenant) | — | `TenantSecurityPolicyResponse` | The normalized tenant security policy: defaults for unset knobs, read per request. Cross-tenant → hidden `404`. |
| `PATCH` | `/tenants/{tenant_id}/security-policy` | `ADMIN` / `SYSTEM_ADMIN` (settings:write @ tenant) | `TenantSecurityPolicyPatch` (all optional; `null` clears the nullable knobs) | `TenantSecurityPolicyResponse` | Partial patch of `mfa_required_roles` / `session_idle_minutes` / `session_max_hours` / `concurrent_session_limit` / `ip_allowlist` / `patient_sharing` / `manager_scope`. Validation errors are `422`. Every change is audited (`tenant.security_policy`, fail-closed) with a changed-keys diff. Enforcement points: login (MFA-required roles, IP allowlist), issuance (concurrent limit), verification + refresh (idle / max-age / IP), grants service (patient sharing, manager scope). |

### `settings` — three-tier settings (system / tenant / user)

Settings resolve `USER > TENANT > SYSTEM > default`. Reads are role-gated;
`/system` writes require `SYSTEM_ADMIN`, `/tenant` writes require
`ADMIN`/`MANAGER` (enforced via `can_manage_level`, 403 otherwise).

| Method | Path | Auth | Body | Response |
|---|---|---|---|---|
| `GET` | `/settings/definitions` | any | — | `{definitions, categories}` (static metadata) |
| `GET` | `/settings/effective` | any | — | `{settings, sources}` (resolved values + per-key source attribution) |
| `GET` | `/settings/system` | `SYSTEM_ADMIN` | — | `{level: "system", settings}` |
| `PUT` | `/settings/system` | `SYSTEM_ADMIN` | `{key, value}` | `{message}` |
| `GET` | `/settings/tenant` | `ADMIN`/`MANAGER` | — | `{level: "tenant", settings}` |
| `PUT` | `/settings/tenant` | `ADMIN`/`MANAGER` | `{key, value}` | `{message}` |
| `GET` | `/settings/user` | any | — | `{level: "user", settings}` |
| `PUT` | `/settings/user` | any | `{key, value}` | `{message}` |

### `admin` — system operator routes

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/admin/notifications/broadcast` | `ADMIN` / `SYSTEM_ADMIN` | query: `title` *, `body?`, `severity=info\|warning\|critical`, `scope=tenant\|system`, `tenant_id?` | `{status, notification_id}` | Emits `SYSTEM`/`SYSTEM_BROADCAST` to every user in scope (`TENANT` target for tenant, `SYSTEM` target = every `SYSTEM_ADMIN` for system). Audit-logged as `admin.broadcast`. |
| `POST` | `/admin/catalogs/import/url` | `SYSTEM_ADMIN` | query: `url` * | `{message}` | Fetches a clinical-ontology catalog JSON and runs the import in the background. Audit-logged as `admin.catalog_import`. |
| `POST` | `/admin/catalogs/import/file` | `SYSTEM_ADMIN` | multipart: `file` | `{message}` | Validates + imports an uploaded catalog JSON. Audit-logged as `admin.catalog_import`. |
| `GET` | `/admin/audit` | `ADMIN` (own tenant) / `SYSTEM_ADMIN` (system-wide) | query: `tenant_id?`, `user_id?` (actor), `resource_type?`, `resource_id?`, `action?`, `outcome=ok\|denied\|error`, `date_from?`, `date_to?` (exclusive), `all=true` (SYSTEM_ADMIN only), `limit=50`, `offset=0` | `AuditListResponse` | The audit-stream viewer. Tenant admins are scoped to their own tenant: a foreign `tenant_id` is a hidden `404` and `all=true` is `403` (both audited). SYSTEM_ADMIN sees every tenant plus the system-level (NULL-tenant) rows; `tenant_id=` narrows, `all=true` is the explicit form. Rows carry the chain metadata (`chain_key`/`seq`/`prev_hash`/`entry_hash`); newest-first. |
| `GET` | `/admin/audit/export` | `ADMIN` (own tenant) / `SYSTEM_ADMIN` | query: `format=json\|csv` (default `json`), same filters as `GET /admin/audit` | `application/x-ndjson` (one JSON object per line) or `text/csv` (attachment) | Streaming audit export — same filters and scoping, newest-first (keyset on `(created_at, id)`, so same-timestamp rows are never skipped), up to 10000 rows. Every line/row carries the chain metadata and the redacted diff payloads. When the filter set matches more rows than the cap the stream is truncated and the response says so: `X-Audit-Export-Truncated: true` + `X-Audit-Export-Total: <matched>` headers (the viewer surfaces a warning). The export itself is audited (`audit.export`, fail-closed — the audit row carries `truncated`/`total_matched`), as is every denied attempt. |
| `GET` | `/admin/audit/retention` | `SYSTEM_ADMIN` | — | `AuditRetentionResponse` | The retention view: the `retention_policies` rows governing audit data (3650-day anonymize of actor/diff payloads; the app never deletes audit rows) plus `legal_holds: null` — there is no hold registry yet. Deliberately no deletion endpoint. |
| `POST` | `/admin/access-elevations` | `ADMIN` / `MANAGER` (tenant scope) / `SYSTEM_ADMIN` (system scope) | `{scope=tenant\|system, permissions: [..], duration_minutes: 1–240, reason *, metadata?}` | `ElevationResponse` | Open a break-glass request: a time-boxed widening of **held** permissions only (an elevation never grants a permission the role lacks — `403` otherwise), one live elevation per principal per scope (`409`), reason required. Starts `pending`; eligible approvers are notified. | **Step-up gated** (12C1): a `break_glass` proof from `POST /auth/step-up` is required (`403 step_up_required`). |
| `GET` | `/admin/access-elevations` | `ADMIN` (own tenant) / `SYSTEM_ADMIN` (all) | query: `tenant_id?`, `scope=tenant\|system?`, `status=pending\|active\|expired\|revoked\|denied?` | `ElevationListResponse` | The elevation ledger, newest first. Tenant admins are scoped to their own tenant: a foreign `tenant_id` is a hidden `404` and `scope=system` is `403` (both audited). SYSTEM_ADMIN narrows with `tenant_id=`/`scope=`; omitted filters answer everything. |
| `POST` | `/admin/access-elevations/{id}/approve` | second person: `ADMIN` (tenant rows) / `SYSTEM_ADMIN` (system rows) | — | `ElevationResponse` | Two-person approval: `pending → active`, the window opens now and expires after the requested duration. The requester themselves answers `403`; a role the scope does not accept answers `403`; a foreign tenant's row answers hidden `404`; a row that is no longer pending answers `409`. Every transition is audited (`elevation_request/approve/deny/revoke/expire`) and requests exercised under a window are audited as `elevation_use`. | Also step-up gated (`break_glass`) — approve is the decision that opens access; deny/revoke stay ungated. |
| `POST` | `/admin/access-elevations/{id}/deny` | same authority as approve | `{reason?}` | `ElevationResponse` | Refuse the request (`pending → denied`); the requester is notified. |
| `POST` | `/admin/access-elevations/{id}/revoke` | the requester, an eligible approver, or SYSTEM_ADMIN | — | `ElevationResponse` | Close a pending or active window early (`→ revoked`); revocation only ever removes reach. Auto-expiry runs on the beat every 15 minutes and the consultation predicate (`expires_at > now()`) is evaluated in SQL on every request — no token caching. Single-admin tenants have no approval path: recovery is the host rescue runbook in `SECURITY.md`. |
| `GET` | `/admin/seeds/export.zip` | `SYSTEM_ADMIN` | — | `application/zip` (attachment `health-assistant-seed-pack.zip`) | Streams the running instance's global taxonomy / anatomy / catalog data as the **canonical seed pack** (`manifest.json` + `items/*.json`): every registered item file, deterministic (no timestamps, sorted, hashed) and contract-valid — re-registerable through the source lane with a zero-write round trip. Read-only; never touches the server's `data/seeds/`. |

### `admin/seed-sources` — seed pack sources

The data-only pack lane's admin surface. `SYSTEM_ADMIN` manages the system lane
(system-scope sources, priorities 0–199); a tenant `ADMIN` manages their own
tenant overlays (priorities 200–999). Cross-tenant ids answer a hidden `404` —
never a `403`. Every mutation is audited (`seed.source.*`, `seed.version.*`);
the apply itself is audited by the applier (`seed.apply`). Errors are typed
detail objects: `{"code": "<TYPED_CODE>", "message": "…"}` — codes include
`INVALID_KIND`, `INVALID_PRIORITY`, `FORBIDDEN_SCOPE`, `URI_INVALID`,
`REF_INVALID`, `SUBPATH_INVALID`, `PACK_INVALID`, `PACK_NOT_FOUND`,
`SCOPE_MISMATCH`, `PRIORITY_MISMATCH`, `DIGEST_DRIFT`, `SECRET_MISSING`,
`VERSION_NOT_PENDING`, `VERSION_NOT_APPROVED`, `BUNDLED_IMMUTABLE`,
`ARCHIVE_INVALID`, `ARCHIVE_PATH_TRAVERSAL`, `ARCHIVE_ENTRY_FORBIDDEN`,
`ARCHIVE_TOO_LARGE`, `FETCH_TOO_LARGE`, `FETCH_TIMEOUT`, `NET_DENIED`,
`UNREACHABLE`, `AUTH`, `PROVIDER`.

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/admin/seed-sources` | `ADMIN` (own tenant overlays) / `SYSTEM_ADMIN` (all) | — | `[{…source, versions[…]}]` | The actor's sources with version summaries (each version carries `is_current`, `pack_id`/`pack_version`/`pack_scope`/`pack_priority`/`pack_files` and `latest_run {run_id, mode, status, started_at, stats}` — the PARTIAL probe). |
| `POST` | `/admin/seed-sources` | `ADMIN` / `SYSTEM_ADMIN` | `{kind: "file"\|"git"\|"registry", priority, scope?: "system"\|"tenant", uri?, ref_spec?, subpath?, auth_secret_ref?, tenant_id? (SYSTEM_ADMIN, tenant scope)}` | source view (`201`) | Registers a source. `bundled` is boot-only and cannot be created; git/registry URIs are https-only; priorities must sit in the scope's band (system 0–199, tenant 200–999). `auth_secret_ref` names a secret **slot** (keyring / `HA_SEED_SOURCE_SECRET_<SLOT>`), never a token. |
| `POST` | `/admin/seed-sources/{source_id}/sync` | `ADMIN` / `SYSTEM_ADMIN` | — | `{status: "registered"\|"up_to_date", versions[…], note?}` | Fetch now (per-source advisory lock; startup never fetches). A new digest lands as a **pending** version with its pins (git commit sha / registry etag); the manifest's scope/priority are pinned to the source row at registration (`SCOPE_MISMATCH`/`PRIORITY_MISMATCH` otherwise). Failures record `last_error` and keep every pin. |
| `POST` | `/admin/seed-sources/{source_id}/upload` | `ADMIN` / `SYSTEM_ADMIN` | multipart: `file` (a pack zip — `manifest.json` + `items/*.json` — or a single item-file JSON such as `medications.json`), optional fields `pack_id`, `version`, `license` | version view | File sources only. The body is read under the upload cap before parsing (`UPLOAD_TOO_LARGE`/413 otherwise); the digest lands as a pending version; re-uploading the same content returns the existing row. Traversal entries, unknown entry types and oversized archives are typed refusals. |
| `GET` | `/admin/seed-sources/{source_id}/versions` | `ADMIN` / `SYSTEM_ADMIN` | — | `[{…version, is_current}]` | The source's versions (newest first, ≤50). |
| `GET` | `/admin/seed-sources/{source_id}/runs` | `ADMIN` / `SYSTEM_ADMIN` | `?limit=25` (≤100) | `[{…run, digest}]` | The source's run history (newest first): mode, status, timing, stats, error and the version digest each run applied. |
| `GET` | `/admin/seed-sources/{source_id}/runs/{run_id}` | `ADMIN` / `SYSTEM_ADMIN` | — | `{…run, digest, items[]}` | One run in full: status/stats/error, the recorded plan document and the per-item outcomes (action + content hash + conflict payload — the durable loser/override record). A run from another source is the hidden 404 (`RUN_NOT_FOUND` for unknown ids). |
| `POST` | `/admin/seed-sources/versions/{version_id}/approve` | `ADMIN` / `SYSTEM_ADMIN` (`seed_source:approve`) | — | version view | `pending → approved` on the exact digest. The pointer advances only on a successful apply. The bundled lane refuses (`BUNDLED_IMMUTABLE`). |
| `POST` | `/admin/seed-sources/versions/{version_id}/reject` | same | `{reason?}` | version view | `pending → rejected` with the reason recorded. |
| `POST` | `/admin/seed-sources/versions/{version_id}/revoke` | same | — | `{version, restored_version_id}` | `approved → revoked`; the previous approved version **re-applies** (restoring the items the revoked version had retired) or the source is left pointer-less. |
| `GET` | `/admin/seed-sources/versions/{version_id}/plan` | `ADMIN` / `SYSTEM_ADMIN` | — | plan document | The plan preview — one documented side effect: it persists a `plan`-mode run (the durable preview history) and writes nothing else. Pending versions plan too — the preview precedes approval. |
| `POST` | `/admin/seed-sources/versions/{version_id}/apply` | `ADMIN` / `SYSTEM_ADMIN` (`seed_source:manage`) | `{"apply": false}` (default) | `{"mode": "plan", "plan": …}` or `{"mode": "apply", runs, statuses, stats}` | **Plan-only by default**; `{"apply": true}` executes the approval-checked apply under the advisory locks. Also the explicit re-apply door: re-running a version whose last apply was PARTIAL retries exactly the failed items (this is the bundled PARTIAL door). Unapproved digests are refused (`VERSION_NOT_APPROVED`). |
| `POST` | `/admin/seed-sources/{source_id}/adopt` | `ADMIN` / `SYSTEM_ADMIN` | `{"seed_key"?}` | `{cleared, seed_key?}` | The explicit "update available" adoption: clears `user_modified` on the source's provenance rows (optionally one `seed_key`) and re-applies the current version — the pack content finally wins. A failed re-apply is audited exactly once with outcome `error` and surfaces as a typed `ADOPT_APPLY_FAILED` (the committed freeze-clears stand). |
| `POST` | `/admin/seed-sources/{source_id}/disable` | `ADMIN` / `SYSTEM_ADMIN` | — | source view | A disabled source provides no keys (planners skip it). The bundled lane refuses. |
| `POST` | `/admin/seed-sources/{source_id}/enable` | `ADMIN` / `SYSTEM_ADMIN` | — | source view | Re-enable. |
| `POST` | `/admin/seed-sources/{source_id}/retire` | `ADMIN` / `SYSTEM_ADMIN` | — | `{retired, kept, source}` | Retires the source: its items flip to `retired` **only where no other active approved source provides them** (patient-referenced rows are kept); the source disables. Runs under the apply lane's advisory lock (a concurrent apply of this source waits). Rows are never deleted. |

Remote sources sync on a 6-hour worker beat (`sync-seed-sources`, per-task
time-limit override); the bundled pack applies at boot from local content and
is never fetched. See `docs/SEEDING_AND_DEMOS.md` for the pack format,
precedence, approval lifecycle and threat model.

### `admin/tenants` — tenant operator console

All routes `SYSTEM_ADMIN`-only. Audit-logged.

| Method | Path | Body / Query | Response | Notes |
|---|---|---|---|---|
| `GET` | `/admin/tenants` | query: `search?`, `is_active?`, `limit=50`, `offset=0` | `TenantListResponse` | Paginated tenant list. |
| `POST` | `/admin/tenants` | `TenantCreate` | `TenantResponse` (`201`) | Create a tenant. |
| `GET` | `/admin/tenants/{tenant_id}` | — | `TenantDetailResponse` | Single-tenant detail incl. usage stats. |
| `PATCH` | `/admin/tenants/{tenant_id}` | `TenantUpdate` | `TenantResponse` | Partial update. |
| `POST` | `/admin/tenants/{tenant_id}/deactivate` | — | `TenantResponse` | Soft-delete (`is_active = false`). |
| `POST` | `/admin/tenants/{tenant_id}/reactivate` | — | `TenantResponse` | Restore a deactivated tenant. |
| `DELETE` | `/admin/tenants/{tenant_id}` | `HardDeleteConfirm` (typed-name) | `{message}` | Hard-delete. Requires typed-name confirmation. |
| `POST` | `/admin/tenants/{tenant_id}/switch` | — | `SwitchTenantResponse` | Mint a scoped JWT for operating inside another tenant; preserves `original_tenant_id`. `400` if already in a switched session. |
| `POST` | `/admin/tenants/exit-switch` | — | `SwitchTenantResponse` | Restore the original `SYSTEM_ADMIN` session after a switch. |
| `GET` | `/admin/tenants/{tenant_id}/users` | query: `search?`, `limit=50`, `offset=0` | `TenantUserListResponse` | Paginated user list for a tenant. |
| `PATCH` | `/admin/tenants/{tenant_id}/users/{user_id}` | `UpdateTenantUser` | `TenantUserResponse` | Update a tenant user (role / active toggle). |
| `PATCH` | `/admin/tenants/{tenant_id}/users/{user_id}/mfa` | `SetTenantUserMFA` (`{enforced}`) | `TenantUserResponse` | Force/release TOTP MFA. `ADMIN` may act inside their own tenant, `SYSTEM_ADMIN` anywhere — the one route here not gated `SYSTEM_ADMIN`-only. Audit-logged as `user.mfa_enforce`. |
| `POST` | `/admin/tenants/{tenant_id}/invite` | `CreateInvitePayload` | `InviteResponse` | Mint a tenant-scoped invite token (audit-logged as `tenant.invite`). |
| `GET` | `/admin/tenants/{tenant_id}/audit` | query: `action?`, `outcome?`, `limit=50`, `offset=0` | `AuditListResponse` | Tenant-scoped `audit_events` viewer (SYSTEM_ADMIN-only; tenant admins use `GET /admin/audit`, which is scoped to their own tenant). |
| `GET` | `/admin/audit/verify` | query: `tenant_id?`, `all?` | `AuditChainVerifyResponse` / `AuditChainVerifyAllResponse` | **Audit-chain verification** (`SYSTEM_ADMIN`-only): walks one chain (default the system chain; `tenant_id=` for a tenant; `all=true` for every chain) — sequence contiguity, hash links, canonical recomputation and the head row. Answers `{chain: {chain_key, ok, entries, head_seq, first_bad_seq, reason}}`; a gap or mismatch is a tamper alarm (server-side `AUDIT_CHAIN_ALARM` critical log, no resync marker). The rows are read before the head and a head-only mismatch is re-read once before alarming, so a concurrent append committing during verification cannot false-alarm (only a mismatch persisting across the re-read is tampering). |

### `admin/tenants` — tenant operator console

All routes `SYSTEM_ADMIN`-only. Audit-logged.

#### Per-tenant SSO config (Program Phase 12B) — `/admin/tenants/{tenant_id}/sso`

`ADMIN` manages its **own** tenant (a foreign id answers a hidden `404`,
audited); `SYSTEM_ADMIN` anywhere. Route doors: reads `settings:read`,
writes `settings:write` (TENANT scope). **Secret-slot law:** the config
carries a slot *name* only — a secret value is refused (`422`); the value
lives in the OS keyring (`health-assistant/tenant-sso`) or the
`HA_SSO_CLIENT_SECRET_<SLOT>` env fallback on the host. `SYSTEM_ADMIN` is
refused for `default_role`/`group_role_map` values (SSO never grants it).

| Method | Path | Body / Query | Response | Notes |
|---|---|---|---|---|
| `GET` | `/admin/tenants/{tenant_id}/sso` | — | `SSOConfigListResponse` | The tenant's bindings (secrets appear as slot references only). |
| `POST` | `/admin/tenants/{tenant_id}/sso` | `SSOConfigCreate` | `SSOConfigResponse` (`201`) | Bind one OIDC provider (issuer unique per tenant; starts `enabled=false`). Audited (`sso_config_created`). |
| `PATCH` | `/admin/tenants/{tenant_id}/sso/{config_id}` | `SSOConfigUpdate` | `SSOConfigResponse` | Partial update (`enabled` is the safe on/off switch). Audited with the changed field names (never secret values). |
| `DELETE` | `/admin/tenants/{tenant_id}/sso/{config_id}` | — | `{message}` | Unbind the provider (existing links keep their rows; the flow is off). Audited. |
| `POST` | `/admin/tenants/{tenant_id}/sso/{config_id}/test-connection` | — | `SSOTestConnectionResponse` | Discovery + secret-slot probe — one bit per check, redacted error text; never echoes the secret or the discovery dump. Audited. |

#### SCIM tokens (Program Phase 12B) — `/admin/tenants/{tenant_id}/scim-tokens`

Same tenant boundary; door `tenant:manage` (TENANT scope).

| Method | Path | Body / Query | Response | Notes |
|---|---|---|---|---|
| `GET` | `/admin/tenants/{tenant_id}/scim-tokens` | — | `SCIMTokenListResponse` | Hash-free projections (name, scopes, activity, expiry). |
| `POST` | `/api/v1/admin/tenants/{tenant_id}/scim-tokens` | `SCIMTokenCreate` (`{name, expires_at?}`) | `SCIMTokenCreatedResponse` (`201`) | The **one-time plaintext** `token` (`ha_scim_…`) rides this response only; only the sha256 is stored. Audited (`scim.token_created`). |
| `DELETE` | `/admin/tenants/{tenant_id}/scim-tokens/{token_id}` | — | `{message}` | Revoke (`active=false`): the bearer stops resolving on the next request. Audited (`scim.token_revoked`). Double-revoke answers `409`. |

### `auth/sso` — per-tenant OIDC sign-in (Program Phase 12B)

`status`/`authorize`/`callback` are unauthenticated auth-bootstrap routes
(rate-limited with the login budget); `/auth/sso/link` is a session route.
Typed refusals carry a stable `code` (`sso_*`) — see `SECURITY.md` for the
law (verified email required, link-only, no merge, JIT capped by domain +
role map, IdP down never touches the local password path).

| Method | Path | Body / Query | Response | Notes |
|---|---|---|---|---|
| `POST` | `/auth/sso/status` | `{email}` | `{sso_available}` | Login-page probe: does this email's domain resolve an enabled config of an active tenant? One bit — no tenant enumeration. |
| `POST` | `/auth/sso/authorize` | `{email}` or `{tenant_slug}` | `{authorization_url, issuer}` | Starts the flow (code + PKCE S256 + state/nonce bound server-side, 10-minute single-use). Unknown/disabled domain → `404 sso_unavailable`; missing host secret → `503 sso_client_secret_missing`; provider unreachable → `503 sso_provider_unavailable`. |
| `GET` | `/auth/sso/callback` | query: `code`, `state`; `browser=1` for the redirect flavor | `TokenResponse` (+ §10 cookies) or `302` to the SPA | Verifies the ID token (JWKS RS256, `iss`/`aud`/`exp`/`nonce`), resolves the link, JIT-provisions or refuses: `400 sso_state_invalid` (unknown/expired/replayed state), `401 sso_token_invalid` / `sso_nonce_mismatch`, `403 sso_email_unverified` / `sso_domain_not_allowed` / `sso_jit_disabled`, `409 sso_link_required` (body carries the one-time `link_token`) / `sso_email_conflict`. Session `auth_mode="oidc"`. **Tenant policy parity (12C1):** the IP allowlist is checked before provisioning (`403 ip_not_allowed`, audited); when the tenant policy, an enrolled TOTP secret or `mfa_enforced` requires MFA and the ID token does **not** assert a second factor (`amr` RFC 8176 values, or a multi-factor `acr`), the callback answers `401 {"detail": "mfa_required", "mfa_token", "enrollment_needed", "origin": "sso"}` instead of tokens — complete at `/auth/mfa/verify`, which mints the `auth_mode="oidc"` session. The `browser=1` redirect flavor answers that challenge as JSON too (a challenge cannot ride a redirect safely) — SPAs that support MFA-over-SSO should use the JSON flavor (fetch) for the callback. |
| `POST` | `/auth/sso/link` | `{link_token}` (session) | `{linked, identity}` | The authenticated link flow: the signed-in local user confirms while the intent is fresh (10-minute, single-use, actor must own the intent's email + tenant). Audited (`auth.sso_link`). |

### `scim/v2` — SCIM 2.0 directory surface (Program Phase 12B)

Bearer-token principal (`token_scim` route class): a tenant-bounded
`scim_tokens` credential — never a session, never a JWT. Bad/missing/
revoked/expired bearer → SCIM Error-schema `401` (audited). Cross-tenant
ids answer the hidden `404`. Deprovision (DELETE or `active=false`)
deactivates the account and bumps `token_version` — every live session
dies; records are never hard-deleted here.

| Method | Path | Body / Query | Response | Notes |
|---|---|---|---|---|
| `GET` | `/scim/v2/Users` | `startIndex`, `count≤200`, `filter=userName eq "…"` | ListResponse envelope | RFC 7644 pagination (1-based `startIndex`) + the one supported filter. |
| `POST` | `/scim/v2/Users` | SCIM User (`userName` required) | SCIM User (`201` + `Location`) | Directory provisioning; the role is capped to the tenant SSO config's `default_role` (else `USER`). Duplicate email → `409`. |
| `GET` | `/scim/v2/Users/{id}` | — | SCIM User | Foreign tenant → hidden `404`. |
| `PATCH` | `/scim/v2/Users/{id}` | PatchOp (`replace` on `active`/`userName`/`name.*`/`displayName`) | SCIM User | `active=false` deprovisions (audit `scim.user_deactivated`); unknown paths answer `400`. |
| `DELETE` | `/scim/v2/Users/{id}` | — | `204` | Deprovision: deactivate + `token_version` bump (audit `scim.user_deprovisioned`); repeat deletes are idempotent. |
| `GET` | `/scim/v2/Groups` | `startIndex`, `count`, `filter=displayName eq "…"` | ListResponse envelope | Each group carries its members. |
| `POST` | `/scim/v2/Groups` | SCIM Group (`displayName` required, `members?`) | SCIM Group (`201`) | Display name unique per tenant; membership moves audited. |
| `GET` | `/scim/v2/Groups/{id}` | — | SCIM Group | — |
| `PATCH` | `/scim/v2/Groups/{id}` | PatchOp (`displayName` replace, members add/remove/replace) | SCIM Group | Membership moves re-roll affected members through the tenant's `group_role_map` (audited `scim.role_change`; never SYSTEM_ADMIN). |
| `DELETE` | `/scim/v2/Groups/{id}` | — | `204` | Members cascade; audited (`scim.group_deleted`). |

### `admin/integrations` — global integration enablement

All routes `SYSTEM_ADMIN`-only.

| Method | Path | Response | Notes |
|---|---|---|---|
| `GET` | `/admin/integrations` | `List[{domain, name, version, is_enabled, on_filesystem, description, categories, kind, capabilities}]` | Merges on-disk wire manifests (identity fields included) with DB state; stale DB-only domains carry `on_filesystem: false`. |
| `POST` | `/admin/integrations/{domain}/enable` | `{message}` | Enable globally; creates `SystemIntegration` row + re-initializes the registry. |
| `POST` | `/admin/integrations/{domain}/disable` | `{message}` | Disable globally. |

---

## Patient clinical record

### `patients`

> Patient identity and biomarker readings are managed via dedicated domain
> endpoints that return ORM-shape JSON (snake_case + app fields like
> `biomarker_id`, `normalized_value`). These are the frontend's primary API
> surface. For **canonical FHIR R4 interop** see the [`/fhir/R4/*`](#fhir-r4-facade) facade.

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/patients` | session · `patient:read` @ `own` | query: `tenant_id?`, `user_id?`, `limit=10`, `offset=0` | `List[patient]` | Grant-aware: staff roles see the tenant list; `USER` (and an assigned-scope `MANAGER`) see own patients + care-team grants. `SYSTEM_ADMIN` can override `tenant_id`. |
| `POST` | `/patients` | any | patient dict (ORM shape) | `patient` | `USER` auto-pinned to own `user_id`. |
| `GET` | `/patients/{patient_id}` | `check_patient_access` | — | `patient` | Tenant + ownership/grant enforced. |
| `PUT` | `/patients/{patient_id}` | `check_patient_access` (write) | patient dict | `patient` | Update patient info. |
| `DELETE` | `/patients/{patient_id}` | `check_patient_access` | — | `{message}` | Delete patient + all associated clinical data. Non-admin / non-`SYSTEM_ADMIN` must own the patient. |
| `PUT` | `/patients/{patient_id}/layout` | `check_patient_access` (write) | layout dict | `patient` | **Legacy single-slot** `Patient.dashboard_layout`. For multi-layout persistence use `/patients/{id}/layouts/*`. |

### `patients/{patient_id}/access` — care-team grants

Who can reach this patient's record. Door permission `patient:grant`; the
row-level reach + level is re-checked in the handler (owner = full manage,
staff per tenant scope, `read`-granted clinicians can view the ledger).

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/patients/{patient_id}/access` | session · `patient:grant` @ `grant` | — | `{items: [grant]}` | The grant ledger (active, pending and settled rows), newest first, with `grantee_label` resolved. |
| `POST` | `/patients/{patient_id}/access` | session · `patient:grant` @ `grant` | `{permission, grantee_user_id? , grantee_doctor_id?, source?, expires_at?, reason?}` | `grant` (`201`) | Staff grant (`admin`/`care_team`) → active immediately; the patient owner's share (`patient`) → `pending` until the grantee staff accepts; `guardian` is admin-only for ownerless patients. **Step-up gated** (12C1): an `grant_admin` proof is required (`403 step_up_required`), and `PATCH` on a grant raising it to `admin` needs a fresh one. Errors: `400` doctor without linked user / both-or-neither grantee, `403` source not allowed to this caller (or patient-share disabled), `404` unknown grantee/patient, `409` a live or pending grant already exists for the identity, `422` bad shape. |
| `PATCH` | `/patients/{patient_id}/access/{grant_id}` | session · `patient:grant` @ `grant` | `{permission?, expires_at?, reason?}` | `grant` | Grantor-side update (permission / expiry / reason). Clear semantics: a JSON `null` clears `expires_at`/`reason`; an omitted field is left unchanged. No status move — `409` on settled rows. |
| `DELETE` | `/patients/{patient_id}/access/{grant_id}` | session · `patient:grant` @ `grant` | — | `grant` | Revoke (→ `revoked`); the grantee loses access immediately and is notified. The patient owner may revoke the shares they initiated. |

A grant response: `{id, patient_id, grantee_user_id, grantee_doctor_id,
permission, source, status, granted_by, granted_at, accepted_at, expires_at,
reason, grantee_label}`. Break-glass is never a grant — emergency elevation
is a separate mechanism (`access_elevations`).

### `patients/{patient_id}/layouts` — dashboard layout persistence

Multi-layout dashboard persistence per `(patient_id, user_id)`. All routes pass
`check_patient_access`.

| Method | Path | Body | Response | Notes |
|---|---|---|---|---|
| `GET` | `/patients/{patient_id}/layouts` | — | `List[PatientLayoutResponse]` | List the caller's saved layouts for this patient. |
| `GET` | `/patients/{patient_id}/layouts/active` | — | `PatientLayoutResponse` (`404` if none) | The currently-active layout. |
| `POST` | `/patients/{patient_id}/layouts` | `PatientLayoutCreate` | `PatientLayoutResponse` (`201`) | Save a new layout. |
| `PUT` | `/patients/{patient_id}/layouts/{layout_id}` | `PatientLayoutUpdate` | `PatientLayoutResponse` | Update. |
| `DELETE` | `/patients/{patient_id}/layouts/{layout_id}` | — | `204` | Delete. |

### `observations` — biomarker readings

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/observations` | any | `patient_id?`, `code?`, `start_date?`, `end_date?`, `limit=100`, `offset=0` | `List[observation]` | `USER` without `patient_id` gets `{items:[], total:0}`. |
| `POST` | `/observations` | `check_patient_access` | observation dict (FHIR-shape `subject.reference` or top-level `patient_id`) | `observation` | Writes an `AuditLog` entry (provenance) and feeds the notification rules engine. |
| `GET` | `/observations/{observation_id}` | tenant-scoped | — | `observation` | 404 cross-tenant. |
| `DELETE` | `/observations/{observation_id}` | `USER` patient-access check via `subject.reference` | — | `{message}` | Audited. |

### `examinations` — clinical visits

The clinical-visit container. Holds documents, observations, medications, doctors,
category concept, organization, and event links. Patient-access is enforced via
`check_examination_access` (chains through `check_patient_access`; see [write hooks](dev/sdk/authoring-and-configuration.md#clinical-events--examinations-write-hooks)).
Responses include two optional integration-provenance fields
(`source_integration_id`, `external_id`) populated only on rows written by an
integration sync.

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/examinations` | any | `patient_id?`, `limit=50`, `offset=0` | `List[ExaminationSummaryResponse]` | `USER` without explicit `patient_id` is restricted to their own patients. |
| `POST` | `/examinations` | `get_current_user` | `ExaminationCreate` | `ExaminationResponse` | Dedupes on `(patient, date, category, notes)` for UI callers unless `auto_extract_metadata` is set; integration callers dedup on `(tenant, patient, source_integration_id, external_id)` when both provenance fields are supplied. |
| `GET` | `/examinations/{examination_id}` | `check_examination_access` | — | `ExaminationResponse` | Detail with relationships (doctors, documents, medications, observations, category, organization, event_links). |
| `PUT` | `/examinations/{examination_id}` | `check_examination_access` | `ExaminationUpdate` | `ExaminationResponse` | Date change cascades to linked Observations (`effective_datetime`) and Medications (`start_date`). |
| `DELETE` | `/examinations/{examination_id}` | `check_examination_access` | — | `{message}` | Delete + all related clinical data + document files. |
| `POST` | `/examinations/bulk-delete` | `get_current_user` | `ExaminationBulkDeleteRequest` | `{message, deleted_count}` | `USER` restricted to own patients. |
| `GET` | `/examinations/categories` | any | — | `List[str]` | Sorted list of `EXAMINATION_CATEGORY` concepts (tenant + globals). |
| `GET` | `/examinations/{examination_id}/status` | `check_examination_access` | — | `ExaminationStatusResponse` | Extraction status + per-document extraction status. |
| `GET` | `/examinations/{examination_id}/documents` | `check_examination_access` | — | list of doc dicts | Non-edited (top-level) documents. |
| `POST` | `/examinations/{examination_id}/extract` | `check_examination_access` | `ExaminationExtractRequest?` (mode: `full` \| `extract_only`) | `{message, job_id, mode}` | Manually trigger AI extraction. |
| `GET` | `/examinations/{examination_id}/logs` | `check_examination_access` | — | `List[TaskLogResponse]` | Task logs for the exam + its documents. |

### `documents`

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/documents` | any | `limit=50`, `offset=0` | list of doc dicts | Admins/managers/`SYSTEM_ADMIN` see whole tenant; `USER` only own docs. |
| `POST` | `/documents` | `get_current_user` | multipart: `file` *, `patient_id?`, `examination_id?`, `include_in_extraction?` | doc dict | Upload + kicks off OCR (Celery, or sync `BackgroundTasks` fallback) when `include_in_extraction`. Upload allowlist enforced (PDF/images/DICOM/text); 413 on `MAX_UPLOAD_SIZE` breach. |
| `GET` | `/documents/{document_id}` | owner / admin / `SYSTEM_ADMIN` | — | doc dict |  |
| `PATCH` | `/documents/{document_id}` | owner / admin / `SYSTEM_ADMIN` | `DocumentUpdate` | `DocumentResponse` | Re-triggers cumulative extraction if `include_in_extraction` flag or `extracted_text` changed. |
| `DELETE` | `/documents/{document_id}` | owner / admin / `SYSTEM_ADMIN` | — | `{message}` |  |
| `POST` | `/documents/{document_id}/edit` | owner / admin / `SYSTEM_ADMIN` | `DocumentEdit` (crop / brightness / contrast) | `DocumentResponse` | Produces a new child document. |
| `GET` | `/documents/{document_id}/presign` | `get_current_user` | — | `{url}` | Mints a short-lived presigned JWT bound to this doc — for use in `<img src>`/`<iframe>` which can't send `Authorization`. |
| `GET` | `/documents/{document_id}/download` | presigned `?token=` | — | `FileResponse` | Token claims re-verified against the live document and user (tenant match + active `user_id` — S8); forces `attachment` for active-content types; `nosniff` always set (audit A3). |
| `GET` | `/documents/{document_id}/preview?page=0` | presigned `?token=` OR `Authorization: Bearer` (tenant-checked; `SYSTEM_ADMIN` exempt) | — | image (JPEG/PNG) | DICOM frames / PDF pages converted on the fly. `X-Total-Pages` + `X-Current-Page` headers. |
| `POST` | `/documents/{document_id}/extract` | owner / admin / `SYSTEM_ADMIN` | — | `{job_id, message}` | Trigger extraction. |
| `GET` | `/documents/{document_id}/extract/status` | owner / admin / `SYSTEM_ADMIN` | — | `{status, progress, error_message}` |  |
| `POST` | `/documents/preview-temp?page=0` | `get_current_user` | multipart: `file` | image (JPEG) | Temp preview for DICOM/PDF before saving to an examination. |
| `GET` | `/documents/{document_id}/dicom-metadata` | `get_current_user` | — | `{tag → {label, value}}` | pydicom tag read; rejects non-`.dcm`. |

### `clinical-events` — journeys and episodes

> See [CLINICAL_EVENTS.md](CLINICAL_EVENTS.md) for the type-blueprint schema
> (`schedule_kind`, `metadata_schema`, category system) and how event instances
> render on the calendar.

**Event types** (the blueprint):

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/clinical-events/types` | any | — | `List[ClinicalEventTypeResponse]` | Tenant + globals, `category_concept` eager-loaded. |
| `POST` | `/clinical-events/types` | `get_current_user` | `ClinicalEventTypeCreate` | `ClinicalEventTypeResponse` | Slug globally unique; `tenant_id = caller's`. |
| `GET` | `/clinical-events/types/{type_id}/biomarkers` | any | — | `List[BiomarkerResponse]` | Correlated biomarkers via `concept_edges` (`MONITORS`, `APPROVED`). |
| `POST` | `/clinical-events/types/{type_id}/biomarkers` | `get_current_user` | `BiomarkerCorrelationCreate` | — | Bind a biomarker (idempotent on pair). |
| `DELETE` | `/clinical-events/types/{type_id}/biomarkers/{biomarker_id}` | `get_current_user` | — | `{message}` | Remove a correlation. |

**Event instances** (the journey); see [write hooks](dev/sdk/authoring-and-configuration.md#clinical-events--examinations-write-hooks)).
Responses include two optional integration-provenance fields
(`source_integration_id`, `external_id`) populated only on rows written by an
integration sync.

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/clinical-events` | any | `patient_id?`, `examination_id?`, `status?`, `active_on?`, `onset_on?`, `date_range?`, `limit?`, `offset?` | `List[ClinicalEventResponse]` | Tenant-scoped, paginated, soft-deletes excluded. |
| `POST` | `/clinical-events` | `get_current_user` | `ClinicalEventCreate` | `ClinicalEventResponse` | Create an instance. UI callers get create-always behavior; integration callers dedup on `(tenant, patient, source_integration_id, external_id)` when both provenance fields are supplied. |
| `GET` | `/clinical-events/{event_id}` | tenant-scoped | — | `ClinicalEventResponse` |  |
| `PUT` | `/clinical-events/{event_id}` | `get_current_user` | `ClinicalEventUpdate` | `ClinicalEventResponse` |  |
| `DELETE` | `/clinical-events/{event_id}` | `get_current_user` | — | `{message}` | Soft-delete (tombstone) + deletion notification. |
| `POST` | `/clinical-events/{event_id}/link-examination` | `get_current_user` | `EventExaminationLinkBase` | `ClinicalEventResponse` | Rejects duplicates. |
| `POST` | `/clinical-events/{event_id}/link-observation` | `get_current_user` | `EventObservationLinkBase` | `ClinicalEventResponse` | Rejects duplicates. |
| `POST` | `/clinical-events/{event_id}/link-anatomy` | `get_current_user` | `EventAnatomyLinkCreate` (`primary_site` / `radiates_to` / `referred_to`) | `ClinicalEventResponse` |  |
| `DELETE` | `/clinical-events/{event_id}/unlink-anatomy/{anatomy_id}` | `get_current_user` | — | `ClinicalEventResponse` |  |
| `POST` | `/clinical-events/{event_id}/occurrences` | `get_current_user` | `ClinicalEventOccurrenceCreate` | `ClinicalEventResponse` | Append a discrete episode to a journey. |
| `DELETE` | `/clinical-events/{event_id}/occurrences/{occurrence_id}` | `get_current_user` | — | `ClinicalEventResponse` | Remove one occurrence. |
| `GET` | `/clinical-events/{event_id}/insights` | `get_current_user` | — | insights dict | Type-driven journey insights: current phase, upcoming/overdue milestones, recommended biomarkers, overdue flag. Computed, not persisted. |

---

## Treatments & alerts

### `medications` — catalog + patient instances

Catalog writes use the Phase-A scope model: any role may create (USER→user-scope,
ADMIN/MANAGER→tenant, SYSTEM_ADMIN→system); update/delete gated by scope+ownership.
Patient-instance routes use `check_patient_access` / `check_medication_access`.

| Method | Path | Auth | Body / Query | Response |
|---|---|---|---|---|
| `GET` | `/medications/catalog` | any | `search?` | `List[MedicationCatalogResponse]` |
| `GET` | `/medications/catalog/{catalog_id}` | any | — | `MedicationCatalogResponse` |
| `POST` | `/medications/catalog` | scope-derived | `MedicationCatalogCreate` | `MedicationCatalogResponse` |
| `PUT` | `/medications/catalog/{catalog_id}` | scope+ownership | `MedicationCatalogUpdate` | `MedicationCatalogResponse` |
| `DELETE` | `/medications/catalog/{catalog_id}` | scope+ownership | — | `{status, message}` |
| `GET` | `/medications/catalog/{catalog_id}/usage` | any | — | usage report |
| `POST` | `/medications/catalog/{catalog_id}/reprocess` | any | — | `MedicationCatalogResponse` (re-run AI enrichment) |
| `GET` | `/medications/patient/{patient_id}` | `check_patient_access` | — | `List[MedicationRecordResponse]` |
| `POST` | `/medications/patient/{patient_id}` | `check_patient_access` | `MedicationRecordCreate` | `MedicationRecordResponse` |
| `GET` | `/medications/{medication_id}` | `check_medication_access` | — | `MedicationRecordResponse` |
| `PUT` | `/medications/{medication_id}` | `check_medication_access` | `MedicationRecordUpdate` | `MedicationRecordResponse` |
| `DELETE` | `/medications/{medication_id}` | `check_medication_access` | — | `{message}` |

### `allergies` — catalog + patient instances

Same scope model as medications. Mirrors the medications parity surface
(single-instance fetch, cross-patient usage, AI reprocess).

| Method | Path | Auth | Body / Query | Response |
|---|---|---|---|---|
| `GET` | `/allergies/catalog` | any | `search?` | `List[AllergyCatalogResponse]` |
| `GET` | `/allergies/catalog/{catalog_id}` | any | — | `AllergyCatalogResponse` |
| `POST` | `/allergies/catalog` | scope-derived | `AllergyCatalogCreate` | `AllergyCatalogResponse` |
| `PUT` | `/allergies/catalog/{catalog_id}` | scope+ownership | `AllergyCatalogUpdate` | `AllergyCatalogResponse` |
| `DELETE` | `/allergies/catalog/{catalog_id}` | scope+ownership | — | `{status, message}` |
| `GET` | `/allergies/catalog/{catalog_id}/usage` | any | — | `List[{allergy, patient}]` | Cross-patient intolerances pointing at this catalog allergen (drives the detail-page tab). |
| `POST` | `/allergies/catalog/{catalog_id}/reprocess` | any | — | `AllergyCatalogResponse` | AI re-enrich the catalog entry's description / typical_reactions (best-effort — no-op when no NLP backend is wired). |
| `GET` | `/allergies/active` | any | — | `List[AllergyIntoleranceResponse]` | For `USER` returns own patients; otherwise whole-tenant active allergies. Powers the dashboard `AllergyAlertsCard`. |
| `GET` | `/allergies/patient/{patient_id}` | `check_patient_access` | — | `List[AllergyIntoleranceResponse]` |
| `POST` | `/allergies/patient/{patient_id}` | `check_patient_access` | `AllergyIntoleranceCreate` | `AllergyIntoleranceResponse` |
| `GET` | `/allergies/{allergy_id}` | `check_allergy_access` | — | `AllergyIntoleranceResponse` |
| `PUT` | `/allergies/{allergy_id}` | `check_allergy_access` | `AllergyIntoleranceUpdate` | `AllergyIntoleranceResponse` |
| `DELETE` | `/allergies/{allergy_id}` | `check_allergy_access` | — | `{message}` |

### `vaccines` — catalog + patient immunizations

Same scope model. Mirrors the medications pattern: `VaccineCatalog` is the
CVX-coded reference (the product); `PatientImmunization` is the dose-administered
patient instance. Vaccine → disease links are graph data — approved `PREVENTS`
`concept_edges` rows seeded from `vaccines.json`'s readable `target_diseases`
refs (migration `0012` dropped the JSONB column) — queryable via
`GET /catalogs/{type}/{id}/relations`, not a row field (the write/read schemas
carry no `target_diseases`; sending one is a 422).

```
# Catalog CRUD (POST/PUT bodies are the typed write schemas, extra="forbid";
# the slug is derived server-side from the name — clients never send it)
GET    /api/v1/vaccines/catalog?search=            → List[VaccineCatalogResponse]
GET    /api/v1/vaccines/catalog/{catalog_id}       → VaccineCatalogResponse
POST   /api/v1/vaccines/catalog                    → VaccineCatalogResponse  (201; scope derived from role)
PUT    /api/v1/vaccines/catalog/{catalog_id}       → VaccineCatalogResponse  (scope + ownership gated)
DELETE /api/v1/vaccines/catalog/{catalog_id}                                  (scope + ownership gated)

# Patient immunization instances (patient-access scoped)
GET    /api/v1/vaccines/patient/{patient_id}       → List[PatientImmunizationResponse]
POST   /api/v1/vaccines/patient/{patient_id}       → PatientImmunizationResponse
GET    /api/v1/vaccines/{immunization_id}          → PatientImmunizationResponse
PUT    /api/v1/vaccines/{immunization_id}          → PatientImmunizationResponse
DELETE /api/v1/vaccines/{immunization_id}
```

FHIR: `GET /api/v1/fhir/R4/Immunization?patient={id}&date=&status=` returns a
searchset Bundle (the `PatientImmunization` row projects to a canonical R4
`Immunization` via `to_fhir_dict()`). `VaccineCatalog` projects to `Medication`.
Vaccines also appear in the unified catalog endpoints (`/catalogs/vaccine`,
`/catalogs/search?q=&types=vaccine`) and the cross-catalog graph.

### `notifications` — unified multi-source notifications

> See [NOTIFICATION_SYSTEM.md](NOTIFICATION_SYSTEM.md) for the full architecture
> (fan-out model, target resolution, rules engine, real-time WS).

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/notifications/vapid-public-key` | **none** | — | `{public_key}` | Web Push registration. |
| `POST` | `/notifications/subscribe` | any | `SubscribeRequest` | `{status, id}` | `{subscription, device_id?, user_agent?}` envelope. |
| `GET` | `/notifications/inbox` | any | `status?`, `category?`, `source?`, `patient_id?`, `limit=50` (max 100), `offset=0` | `InboxResponse` | Per-user inbox joined to the notification event. `patient_id` access-checked. |
| `GET` | `/notifications/unread-count` | any | — | `{count}` | Bell badge. |
| `PATCH` | `/notifications/{recipient_id}/read` | any | — | `{status}` | 404 cross-tenant. |
| `PATCH` | `/notifications/{recipient_id}/dismiss` | any | — | `{status}` |  |
| `POST` | `/notifications/read-all` | any | — | `{status, marked_read}` |  |
| `GET` | `/notifications/admin` | `ADMIN`/`MANAGER`/`SYSTEM_ADMIN` | `tenant_id?`, `type?`, `source?`, `category?`, `limit=50`, `offset=0` | `AdminFeedResponse` | Tenant-wide (or cross-tenant for `SYSTEM_ADMIN`). |
| `GET` | `/notifications/admin/stats` | `ADMIN`/`MANAGER`/`SYSTEM_ADMIN` | `tenant_id?` | stats dict | `by_source`, `by_category`, `delivery` (channel × status), `recipients`, `unique_recipients`, `total`. |
| `GET` | `/notifications/admin/{notification_id}/delivery` | `ADMIN`/`MANAGER`/`SYSTEM_ADMIN` | — | delivery detail | Per-recipient breakdown (sender email resolved, per-channel status). |
| `POST` | `/notifications/triggers` | any | `TriggerCreate` | trigger dict | `TriggerType`: `TIME` (one-shot) or `RECURRING` (wall-clock + optional days-of-week). `patient_id` access-checked. |
| `GET` | `/notifications/triggers` | any | `patient_id?` | list | Without `patient_id`: tenant-wide (for the Notification Center "Reminders" tab). |
| `DELETE` | `/notifications/triggers/{trigger_id}` | any | — | — | Tenant-scoped; cross-tenant is a no-op. |
| `POST` | `/notifications/triggers/{trigger_id}/test` | any | — | `{status, message}` | Fire immediately for testing. |
| `GET` | `/notifications/preferences` | any | `integration_id?` | `NotificationPreferencesResponse` | All addressable notification kinds (sources + channels + per-integration-instance types) + their current `enabled` state. Optional `integration_id` scopes to one instance (the per-instance tab). Each kind carries `manage_url` + `mutable`. |
| `PUT` | `/notifications/preferences/{kind_id}` | any | `{enabled: bool}` | `{status, kind_id, enabled}` | Enable/disable any kind. `kind_id` is `source:{SOURCE}`, `channel:{CHANNEL}`, or `integration:{integration_id}:{type_id}`. Routes to the right store (tiered settings or per-instance JSONB). 404 unknown kind; 400 on disabling an immutable kind (safety-critical). |

### `notification-rules` — biomarker threshold alerts

Rules are evaluated on every observation ingestion (`fhir_service.create_observation`
→ `notification_rule_service.evaluate_and_fire`).

| Method | Path | Auth | Body / Query | Response |
|---|---|---|---|---|
| `GET` | `/notification-rules` | any | `patient_id?`, `biomarker_id?`, `enabled?`, `limit=100` (max 200), `offset=0` | `NotificationRuleListResponse`. With `patient_id`, the caller must reach that patient. Without one, tenant-wide roles (SYSTEM_ADMIN/ADMIN, tenant-scope MANAGER) see the tenant's rules; a USER or assigned-scope MANAGER sees only rules attached to patients they can reach. |
| `POST` | `/notification-rules` | any | `NotificationRuleCreate` | `NotificationRuleRead` |
| `GET` | `/notification-rules/{rule_id}` | any | — | `NotificationRuleRead` |
| `PUT` | `/notification-rules/{rule_id}` | any | `NotificationRuleUpdate` | `NotificationRuleRead` |
| `DELETE` | `/notification-rules/{rule_id}` | any | — | `{status}` |
| `POST` | `/notification-rules/{rule_id}/test` | any | — | `{status, message}` |

---

## Catalogs & reference data

Health Assistant groups its reference vocabularies (anatomy, taxonomy/concepts,
biomarkers, medications, allergies, vaccines) under a uniform **Catalog Registry**
(`app/catalogs/`). Every catalog conforms to one CRUD/access/search/FHIR/edge
contract, exposed through a registry-driven meta-layer that **complements** (does
not replace) the domain endpoints (`/biomarkers`, `/medications`, `/anatomy`,
`/concepts`, `/allergies`, `/vaccines`).

Access is **ownership-based via scope tiers** (Phase A), not pure RBAC: every
item carries a `scope` of `system` | `tenant` | `user`. Any authenticated user may
**create** — scope is derived from role (SYSTEM_ADMIN→system, ADMIN/MANAGER→tenant,
USER→user; concepts follow `ConceptService`'s curated-taxonomy RBAC). Update/delete:
`system`→SYSTEM_ADMIN, `tenant`→ADMIN/MANAGER, `user`→creator OR ADMIN.
`CatalogPermissionDenied` → 403, uniqueness conflicts → `409`
(`{"code": "catalog_conflict", "field": ...}`), unknown/read-only body fields →
`422`. Every write appends a `CatalogAuditLog` row (Phase B).

**Reference ids are visibility-checked.** An explicit
`class_concept_id`, concept `parent_id` or anatomy/biomarker class id must
point at a row that exists and is visible to the caller — a system row
(`tenant_id IS NULL`) or the actor's own tenant. Anything else — a bogus id or
another tenant's private concept — is a typed **400**
(`CatalogReferenceError`) whose message names no hidden row, on create and
update and on both doors. Slug references (`class_concept_slug`, concept
`parent`) are resolved tenant-scoped the same way: an explicit slug that does
not resolve is a **400**, never a silently dropped link. There is no
name-string fallback: the biomarker **`category`** field was retired in favour
of `class_concept_slug` / `class_concept_id` (a payload that still sends
`category` is a **422**, same as any unknown field). A reference that slips past
the pre-checks and fails at the DB level is translated too: unique-index
violations → `409` (`{"code": "catalog_conflict", "field": ...}` — e.g. a
demote onto a colliding `code`, a promote onto a colliding `seed_key`),
foreign-key violations → `400`. No catalog write answers a bad client value
with a 500.

**Ownership is `scope`.** Catalog rows expose `scope` (`system` / `tenant` /
`user`) plus `tenant_id` and `created_by`; the former `is_custom` boolean is
gone from every catalog response, write schema and filter (`scope != 'system'`
is the custom predicate). Search indexing is descriptor-driven: each catalog
declares its full-text surface and migration `0024_catalog_cleanup` builds the
trigram/FTS indexes from it, so a searched column and its index cannot drift.

**Reference display is visibility-projected.** The serializers resolve
`class_concept_slug` / `class_concept_name` / `parent_slug` only when the
referenced concept is visible to the caller (system row or the caller's own
tenant, not soft-deleted); a legacy row whose stored link points at a hidden
concept returns `null` for the slug/name while `class_concept_id` / `parent_id`
keep their stored values. The stored FK is never rewritten. The domain read
doors (`GET /medications/catalog[/{id}]`, `GET /allergies/catalog*`,
`GET /vaccines/catalog*`, `GET /anatomy[/{id}]`) serialize through the same
shared row serializer, so the projection covers them identically.

**Reference precedence is uniform (id wins, explicit null clears).** On any
catalog update, a sent `class_concept_id` beats a `class_concept_slug` sent
alongside — an explicit `null` id clears the link and the slug is ignored
("not sent" vs "explicit null" is read off `model_fields_set` for every type:
biomarker, medication, allergy, vaccine, anatomy). On create, a non-null id
wins and otherwise the slug resolves. Either reference that is unknown or
invisible stays a `400`.

**One write contract per catalog:** `POST`/`PUT`/`DELETE`
`/catalogs/{type}` and the domain catalog routes dispatch to the same per-type
service functions (`app/services/catalog/catalog_write_service.py`). Each type
has one Pydantic create/update schema pair (`extra="forbid"`) — unknown or
system-owned fields (`scope`, `tenant_id`, `created_by`, `seed_key`, `id`,
timestamps) are rejected with 422, never silently dropped. `seed_key` is
response-only. Slugs are **server-owned where the type says so**: the vaccine
slug is always derived from the name (a client-sent slug → 422), and the
biomarker slug may be omitted — the service derives it from the name
(deterministic, unique per tenant scope via a `-2`/`-3`… suffix) while an
explicit slug stays honored (a duplicate → 409). Writes return the full
serialized row (the same keys the reads
return, including `class_concept_slug`, `preferred_unit_symbol`,
`allowed_states`, codes). Update schemas distinguish "not sent" from "explicit
null" (Pydantic `model_fields_set`): biomarker `preferred_unit_id: null` /
`class_concept_id: null` clear the reference — and the same id-wins,
explicit-null-clears precedence now applies to the class reference of every
type (a null id beats a slug sent alongside). Deletes answer
`{"status": "success", "message": "<type> item deleted"}` on both doors
(concepts delete/retire with 204).

### `catalogs` — unified meta-layer

| Method | Path | Body / Query | Response | Notes |
|---|---|---|---|---|
| `GET` | `/catalogs` | — | `{types: [...]}` | Registered catalog types + UI metadata + `edge_endpoint_type` + `search_columns` + `write` (the type's `create_fields` / `update_fields` / `supports_promote`, mirrored from its write schemas — the single source of truth for client payload allowlists). |
| `GET` | `/catalogs/relation-types` | — | `{items: [...]}` | Reference metadata for every `ConceptRelationType` (label/group/description/icon). |
| `GET` | `/catalogs/search` | `q` * (min 2), `types?`, `kind?`, `limit?` | `{results: [...]}` | Unified cross-catalog hybrid search (trigram + `websearch_to_tsquery` FTS + `ILIKE` fallback, fused via **Reciprocal Rank Fusion** k=60). `types` is a comma-separated subset; `kind` narrows the concept catalog by `ConceptKind`. |
| `GET` | `/catalogs/graph` | `types?`, `kind?`, `include_isolated?`, `limit?` | `{nodes, edges, truncated}` | Whole cross-catalog ontology graph (rootless). |
| `GET` | `/catalogs/{type}` | `search?`, `scope?`, `kind?`, `class?` (alias `class_`), `include?=relations`, `limit?`, `offset?` | `{items:[...], total}` | List items of one type. `?include=relations` annotates each item with `relation_count` + `relation_breakdown`. |
| `GET` | `/catalogs/{type}/{item_id}` | — | item dict |  |
| `POST` | `/catalogs/{type}` | typed create schema | item dict (`201`) | Validated against the type's schema (`extra="forbid"` → 422); dispatched to the same service as the domain route. |
| `PUT` | `/catalogs/{type}/{item_id}` | typed update schema | item dict | Same contract. |
| `DELETE` | `/catalogs/{type}/{item_id}` | — | `{status, message}` | Concepts retire-or-soft-delete via `ConceptService` (never a hard delete). |
| `POST` | `/catalogs/{type}/{item_id}/promote` | `{scope: "system"\|"tenant"\|"user"}` | item dict | Scope transition. user↔tenant needs `ADMIN`/`MANAGER`; any transition involving `system` needs `SYSTEM_ADMIN`. Slug collision at the target tier → `409`. **`concept` → `405`** (no scope-transition semantics). |
| `GET` | `/catalogs/{type}/{item_id}/history` | `limit?` | `{items: [...]}` | Audit trail (newest-first). Item must be visible to caller. |
| `GET` | `/catalogs/{type}/{item_id}/relations` | `depth=1..3`, `relation?` (CSV), `include_proposed?` | `{start, nodes, edges}` | Cross-catalog polymorphic graph traversal. |

`{type}` ∈ `biomarker | medication | allergy | anatomy | vaccine | concept`.

> **Concept writes dispatch to `ConceptService`** — the generic layer no longer
> 405s on `POST`/`PUT`/`DELETE /catalogs/concept`. The same kind-sync,
> retire-on-edges, curated-taxonomy RBAC (USER read-only; globals
> SYSTEM_ADMIN-only) and audit apply whichever door you enter; duplicate slugs
> → `409`. Only `promote` stays `405` for concepts.

### `biomarkers` — definitions + units + stratified reference ranges

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/biomarkers/` | any | — | `List[BiomarkerResponse]` | Global + tenant, tenant-scoped via `_tenant_scope`. |
| `GET` | `/biomarkers/{biomarker_id}` | any | — | `BiomarkerResponse` |  |
| `GET` | `/biomarkers/slug/{slug}` | any | — | biomarker dict |  |
| `POST` | `/biomarkers/` | scope-derived | `BiomarkerCreate` | `BiomarkerResponse` | `slug` is server-owned: omit it and the service derives it from the name (deterministic, unique per tenant scope via a `-2`/`-3`… suffix); an explicit slug is honored (must match `^[A-Za-z0-9_-]{1,80}$`, audit A1) and a duplicate → `409`. |
| `PATCH` | `/biomarkers/{biomarker_id}` | scope+ownership | `BiomarkerUpdate` | `BiomarkerResponse` | If `is_telemetry` changes, triggers the `migrate_biomarker_data` Celery task. |
| `DELETE` | `/biomarkers/{biomarker_id}` | scope+ownership | — | `{status, message}` | Global rows require `SYSTEM_ADMIN`. |
| `POST` | `/biomarkers/bulk-delete` | `get_current_user` | `List[UUID]` | `{status, message}` | `SYSTEM_ADMIN`/`ADMIN`/`MANAGER` can delete any tenant row; `USER` only their own user-scope rows. Never deletes system rows. |
| `POST` | `/biomarkers/{biomarker_id}/retry-migration` | `get_current_user` | — | `BiomarkerResponse` | Retry a stuck/failed telemetry↔FHIR migration (only when `meta migration_status` is `failed`/`in_progress`). |
| `POST` | `/biomarkers/{biomarker_id}/remap` | `get_current_user` | `BiomarkerRemapRequest` `{source_name, patient_id?}` | `{status, biomarker_id, observations_remapped}` | Relinks unmapped observations (those with `biomarker_id = NULL`) whose stored `code.text` matches `source_name`. |
| `GET` | `/biomarkers/units` | any | — | `List[UnitResponse]` |  |
| `POST` | `/biomarkers/units` | `get_current_user` | `UnitCreate` | `UnitResponse` | Rejects duplicate symbol with 400. |
| `GET` | `/biomarkers/{biomarker_id}/reference-ranges` | any | — | `List[BiomarkerReferenceRangeResponse]` | Stratified ranges (parent must be visible). |
| `POST` | `/biomarkers/{biomarker_id}/reference-ranges` | scope+ownership (inherited from parent) | `BiomarkerReferenceRangeCreate` | `BiomarkerReferenceRangeResponse` (`201`) |
| `PUT` | `/biomarkers/{biomarker_id}/reference-ranges/{range_id}` | scope+ownership | `BiomarkerReferenceRangeUpdate` | `BiomarkerReferenceRangeResponse` |
| `DELETE` | `/biomarkers/{biomarker_id}/reference-ranges/{range_id}` | scope+ownership | — | `{status, message}` |

### `concepts` + `concept-edges` — unified taxonomy / knowledge graph

> See [TAXONOMY.md](TAXONOMY.md) for the full taxonomy model.
> `ConceptService` is the sole concept write authority (audit + restore + RBAC +
> scope invariant). Concept writes dispatch to it from both doors — the domain
> `/concepts` routes and the generic `/catalogs/concept` routes (restore
> `/concepts/{id}/restore` exists only on the domain surface, the only
> restorable catalog).

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/concepts` | any | `kind?`, `parent_id?`, `include_retired?`, `limit?`, `offset?` | `List[ConceptResponse]` |  |
| `GET` | `/concepts/search` | any | `q` * (min 1), `kind?`, `limit?` | `List[ConceptResponse]` | Ranked trigram + alias search. |
| `POST` | `/concepts` | `get_current_user` (`SYSTEM_ADMIN` for global) | `ConceptCreate` (`kinds:[...]` or legacy `kind:"..."`) | `ConceptResponse` (`201`) |  |
| `GET` | `/concepts/{concept_id}` | any | — | `ConceptResponse` |  |
| `PUT` | `/concepts/{concept_id}` | `get_current_user` | `ConceptUpdate` | `ConceptResponse` |  |
| `DELETE` | `/concepts/{concept_id}` | `get_current_user` | — | `204` | Soft-delete (or retire if referenced by edges). Reversible via `/restore`. |
| `POST` | `/concepts/{concept_id}/restore` | `get_current_user` | — | `ConceptResponse` | Reverses a retire/soft-delete. |
| `GET` | `/concepts/{concept_id}/neighbors` | any | `relation?`, `include_proposed?` | `List[NeighborResponse]` | One-hop neighbors. |
| `GET` | `/concept-edges` | any | `src_type?`, `src_id?`, `dst_type?`, `dst_id?`, `relation?`, `include_proposed?`, `limit?` | `List[ConceptEdgeResponse]` |  |
| `GET` | `/concept-edges/schema` | any | `src_type?`, `dst_type?` | matrix slice (see below) | Pure-metadata link-schema discovery — no DB hit. Powers the LLM `get_link_schema` tool + the frontend `<LinksSection>` dropdown filter. Shapes: with both args → `{relations: [...]}`; with `src_type` only → `{<dst_type>: [...]}`; with neither → `[{src_type, dst_type, relations}]`. |
| `POST` | `/concept-edges` | `get_current_user` (`SYSTEM_ADMIN` for global) | `ConceptEdgeCreate` | `ConceptEdgeResponse` (`201`) | Validates concept endpoints exist. |
| `DELETE` | `/concept-edges/{edge_id}` | `get_current_user` | — | `204` |  |

### `anatomy` — anatomy graph + figures

Router mounted with extra prefix at `/api/v1/anatomy`. Most reads are `any` role;
figure writes/deletes and bulk import are `SYSTEM_ADMIN`.

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/anatomy` | any | `class_concept_id?`, `class?` (alias `class_`), `search?`, `limit?`, `offset?` | `AnatomyListResponse` |  |
| `POST` | `/anatomy` | `get_current_user` (`SYSTEM_ADMIN` for global) | `AnatomyStructureCreate` | `AnatomyStructureResponse` | Rejects duplicate slug. |
| `GET` | `/anatomy/{identifier}` | any | — | `AnatomyGraphNode` | By UUID or slug. |
| `PATCH` | `/anatomy/{identifier}` | `get_current_user` (`SYSTEM_ADMIN` for global) | `AnatomyStructureUpdate` | `AnatomyStructureResponse` |  |
| `DELETE` | `/anatomy/{identifier}` | `get_current_user` (`SYSTEM_ADMIN` for global) | — | `{detail}` |  |
| `POST` | `/anatomy/relations` | `get_current_user` | `AnatomyRelationCreate` | `AnatomyRelationResponse` | Edge between two structures. |
| `GET` | `/anatomy/{identifier}/related` | any | `relation_type?`, `direction=both\|outgoing\|incoming` | `AnatomyRelatedResponse` | Immediate neighbors. |
| `GET` | `/anatomy/{identifier}/graph` | any | `depth=1..3`, `relation_type?`, `direction?` | `AnatomyGraphResponse` | BFS up to `depth` hops. |
| `GET` | `/anatomy/figures` | any | `active_only?` | `List[AnatomyFigureResponse]` | Body figures (metadata only). |
| `GET` | `/anatomy/figures/{slug}` | any | — | `AnatomyFigureResponse` |  |
| `GET` | `/anatomy/figures/{slug}/image` | any | — | `FileResponse` (WebP/PNG) | Cropped image. |
| `GET` | `/anatomy/figures/{slug}/source-image` | any | — | `FileResponse` | Original uncropped image (for re-cropping). |
| `POST` | `/anatomy/figures` | `SYSTEM_ADMIN` | multipart: label, figure_key, view_key, image, slug?, source?, sort_order?, is_active? | `AnatomyFigureResponse` |  |
| `PATCH` | `/anatomy/figures/{slug}` | `SYSTEM_ADMIN` | multipart (same + `clear_source?`) | `AnatomyFigureResponse` |  |
| `DELETE` | `/anatomy/figures/{slug}` | `SYSTEM_ADMIN` | — | `{detail}` |  |
| `POST` | `/anatomy/import` | `SYSTEM_ADMIN` | `AnatomyImportPayload` | stats dict | Bulk import nodes + edges. |

### `search` + `instances` — global + per-patient search

| Method | Path | Auth | Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/search` | any | `q` * (min 2) | `{results: [{id, type, title, subtitle}]}` | Global cross-entity search over patients, examinations, documents, clinical events + the catalog dispatcher. |
| `GET` | `/instances/search` | `check_patient_access` (with `patient_id`) or `ADMIN`/`SYSTEM_ADMIN` (tenant-wide) | `q` * (min 2), `types?`, `patient_id?`, `limit?` | `InstanceSearchResponse` `{results: [{type, id, label, subtitle?, date?}]}` | Free-text search across patient-scoped record instances (exams, medications, observations, documents, events, allergies, vaccines). `USER` without `patient_id` → 403 (defense-in-depth against enumeration). |

---

## Analytics

### `analytics`

| Method | Path | Auth | Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/analytics/dashboard` | any | `patient_id?`, `period="last-30-days"` | dashboard data | `USER` without `patient_id` → `{items:[], total:0}`. |
| `GET` | `/analytics/summary` | any | `patient_id?`, `period="last-year"` | summary dict |  |
| `GET` | `/analytics/trends` | any | `biomarker_codes?`, `period="last-6-months"`, `aggregation?`, `patient_id?` | trends | `biomarker_codes` is expanded against the definition catalog (names, aliases, codes) so both mapped and unmapped observations are included. `aggregation` is a telemetry bucket (e.g. `15 minutes`, `1 day`, `1 week`). |
| `GET` | `/analytics/anomalies` | any | `biomarker_codes?`, `patient_id?` | `{anomalies}` | Telemetry anomalies from the one CAgg-based engine (rolling hourly baseline). Events persist to `telemetry_anomalies` and dedup on `(tenant, patient, device, slug, window_start, kind)`; `kind` is `spike`/`drop`, and resolved events carry `resolved_at`. |
| `GET` | `/analytics/reference-ranges` | any | — | `{slug → {min, max, unit}}` | Reads each visible biomarker's default reference range from the catalog (not a hardcoded table). Stratified ranges live under `/biomarkers/{id}/reference-ranges`. |
| `GET` | `/analytics/available-categories` | any | `patient_id?` | `{categories}` | Categories that have completed-document data for the caller/patient. |
| `GET` | `/analytics/category/{category_name}` | any | `patient_id?` | category data | Per-category rollup. |

### `telemetry` — high-frequency wearable/device ingest

Mobile-device sync (Health Connect / HealthKit) and per-instance integration
telemetry enter here. Tenant-scoped via `current_user.tenant_id`. This router is
**ingest only**: reads live under `analytics` (web) and the bridge's
schema-pinned sub-API. The TimescaleDB telemetry split, device registry,
canonical units + provenance, and CAgg anomaly engine are documented in
[TELEMETRY_AND_AGGREGATION.md](TELEMETRY_AND_AGGREGATION.md).

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/telemetry/data` | any | `TelemetrySyncPayload` `{device_id, source?, device_label?, points: [...]}` | `{uploaded, device_id, message}` | Every slug must resolve to a visible `is_telemetry` biomarker (typed 400 otherwise); the device reference resolves to a `telemetry_devices` row; `value`/`unit` store canonical units beside `raw_value`/`raw_unit`. |

---

## AI

### `ai-config` — provider/model/task-assignment CRUD

> See [AI_SYSTEM.md](AI_SYSTEM.md) for the factory, model resolution, and the
> 3-table config model. Scope checks (`verify_provider_access` /
> `verify_model_access` / `check_scope_access`) are enforced on every entry point.

| Method | Path | Auth | Body / Query | Response |
|---|---|---|---|---|
| `POST` | `/ai-config/providers` | `check_scope_access` | `AIProviderCreate` | `AIProviderResponse` (`201`) |
| `GET` | `/ai-config/provider-presets` | any | — | `ProviderPresetsResponse` (setup tile surface: enabled presets + disabled-with-reason; metadata only) |
| `POST` | `/ai-config/providers/{preset_key}/setup` | any (USER scope) | `ProviderSetupRequest` (`api_key`, `name?`, `options{curated_ids, bind_chat, bind_vision, bind_stt}`) | `ProviderSetupResponse` — fetch-first validation; 404 unknown preset; 422 `{code, suspected_vendor, message}` on classified failure (nothing persisted); 400 SSRF-guard violation |
| `PUT` | `/ai-config/providers/{provider_id}/set-default` | `verify_provider_access` + USER-scope owner | `ProviderSetDefaultRequest` (`model_name`, `task?="default"`) | `ProviderSetDefaultResponse` — capability-guarded; 409 cross-provider model; 422 capability/unknown-task |
| `GET` | `/ai-config/providers` | any | `tenant_id?`, `user_id?`, `scope?`, `is_active?=true`, `include_models?=false` | `List[AIProviderResponse]` |
| `GET` | `/ai-config/providers/{provider_id}` | `verify_provider_access` | — | `AIProviderResponse` |
| `GET` | `/ai-config/providers/{provider_id}/with-models` | `verify_provider_access` | — | `AIProviderWithModelsResponse` |
| `PUT` | `/ai-config/providers/{provider_id}` | `check_scope_access` | `AIProviderUpdate` | `AIProviderResponse` |
| `DELETE` | `/ai-config/providers/{provider_id}` | `check_scope_access` | — | `204` |
| `GET` | `/ai-config/providers/{provider_id}/fetch-external-models` | `verify_provider_access` | — | list of upstream model descriptors (SSRF-guarded; 502 on upstream failure) |
| `POST` | `/ai-config/providers/{provider_id}/models` | `verify_provider_access` | `AIModelCreate` | `AIModelResponse` (`201`) |
| `GET` | `/ai-config/providers/{provider_id}/models` | `verify_provider_access` | `is_active?=true` | `List[AIModelResponse]` |
| `GET` | `/ai-config/models/{model_id}` | `verify_model_access` | — | `AIModelResponse` |
| `PUT` | `/ai-config/models/{model_id}` | `verify_model_access` | `AIModelUpdate` | `AIModelResponse` |
| `DELETE` | `/ai-config/models/{model_id}` | `verify_model_access` | — | `204` |
| `POST` | `/ai-config/task-assignments` | `check_scope_access` | `AITaskAssignmentCreate` | `AITaskAssignmentResponse` (`201`) |
| `GET` | `/ai-config/task-assignments` | any | `tenant_id?`, `user_id?`, `scope?`, `task_type?`, `include_revoked?=false` | `List[AITaskAssignmentResponse]` |
| `GET` | `/ai-config/task-assignments/{assignment_id}` | `check_scope_access` | — | `AITaskAssignmentResponse` |
| `PUT` | `/ai-config/task-assignments/{assignment_id}` | `check_scope_access` | `AITaskAssignmentUpdate` | `AITaskAssignmentResponse` |
| `DELETE` | `/ai-config/task-assignments/{assignment_id}` | `check_scope_access` | — | `204` — revokes (`revoked_at`), the row is never deleted |
| `GET` | `/ai-config/task-assignments/active/{task_type}` | any | `tenant_id?`, `user_id?` | `AITaskAssignmentResponse` |
| `GET` | `/ai-config/summary` | any | `tenant_id?`, `user_id?`, `scope?` | `AIConfigSummary` |
| `PUT` | `/ai-config/settings` | `check_scope_access` | `AIConfigUpdate` (query: `tenant_id?`, `user_id?`, `scope?=tenant`) | `204` |
| `GET` | `/ai-config/default-for-task/{task_type}` | any | `tenant_id?`, `user_id?` | `{provider, model?}` |
| `GET` | `/ai-config/usage` | ADMIN / SYSTEM_ADMIN | `window_days?=30`, `tenant_id?` | `AIUsageResponse` | `ai_call_logs` aggregates per task: calls, errors, tokens, priced/unpriced calls, `cost_micros` (nullable when nothing is priced), avg latency. |
| `GET` | `/ai/prices` | ADMIN / SYSTEM_ADMIN | `tenant_id?` | `PricesOut` | Tenant override rows + effective built-in/local/unknown rates. |
| `PUT` | `/ai/prices` | ADMIN / SYSTEM_ADMIN | `PriceOverrideIn` | `PriceOverrideOut` | Upsert one tenant override; `provider_key` or `provider_ref` binds a preset/concrete provider. |
| `DELETE` | `/ai/prices/{override_id}` | ADMIN / SYSTEM_ADMIN | — | `204` | Delete one tenant override. |
| `GET` | `/ai/usage/costs` | ADMIN / SYSTEM_ADMIN | `window_days?=30`, `tenant_id?` | `CostsOut` | Per-provider/model call cost with priced/unpriced counts; costs stay null when unpriced. |

### `ai-assistance` — chatbot, Magic Fill, HITL, transcription

> The chatbot is agentic with a hard human-in-the-loop wall: the AI proposes
> actions via task cards, the user reviews them in a modal and explicitly
> approves/cancels/rejects. The AI never writes clinical data directly. See
> [AI_SYSTEM.md](AI_SYSTEM.md).

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/ai-assistance/assist` | any | `AIAssistanceRequest` | `AIAssistanceResponse` | Non-streaming assistance (form filling, icons, SVG, chat). |
| `POST` | `/ai-assistance/stream` | any | `AIAssistanceRequest` | SSE `text/event-stream` | Streaming chat. Rejects `task_type != "chat"` with 400. Stable SSE error codes: `connection`, `auth`, `rate_limit`, `timeout`, `generic`, `guard`. Per-session single-flight: a second concurrent turn on the same session is rejected with 409 before the stream starts. |
| `GET` | `/ai-assistance/sessions` | any | query: `patient_id?` | `List[ChatSessionSchema]` | Tenant-scoped. |
| `GET` | `/ai-assistance/sessions/{session_id}/messages` | any (ownership verified in service) | — | `List[ChatMessageSchema]` | The active-path walk, variant-decorated (`parent_id` / `variant_index` / `variant_count` / `sibling_ids`). |
| `POST` | `/ai-assistance/messages/{message_id}/edit` | any | `{content: str, context?}` | SSE `text/event-stream` | Edit-and-resend as a branch: new user sibling under the same parent, pointer flip, fresh turn; old subtree kept as a variant. 400 on non-user targets, 404 foreign. |
| `POST` | `/ai-assistance/messages/{message_id}/regenerate` | any | `{context?}` (optional body) | SSE `text/event-stream` | New assistant sibling under the target user message — earlier replies stay switchable variants. 400 on non-user targets. |
| `POST` | `/ai-assistance/messages/{message_id}/select` | any | — | `List[ChatMessageSchema]` | Level-flip exactly one branch pointer; returns the re-walked decorated path. |
| `GET` | `/ai-assistance/sessions/{session_id}/tree` | any | — | `ChatTreeSchema` | Read-only `{active_root_id, nodes[]}` branch-tree projection. |
| `DELETE` | `/ai-assistance/sessions/{session_id}` | any (ownership verified in service) | — | `{success: true}` |  |
| `GET` | `/ai-assistance/tools` | any | query: `patient_id` *, `examination_id?` | `List[AIAssistanceToolSchema]` | Built-in tools + integration-aggregated tools (failures logged, not raised). |
| `POST` | `/ai-assistance/sessions/{session_id}/tasks/{proposal_id}/resolve` | any | `HitlResolutionRequest` (CONFIRMED/DISMISSED) | `{success, task}` | HITL resolution into the `proposals` row. Idempotent: repeating the recorded outcome returns the stored card (200); a conflicting decision on a terminal proposal is 409. Does NOT perform the underlying write (the frontend commits via canonical REST). |
| `POST` | `/ai-assistance/sessions/{session_id}/resume` | any | `HitlResumeRequest` | SSE `text/event-stream` | Agent continuation after one or more HITL task cards are resolved. Guards: session ownership, target message must exist + have tasks, all tasks must be terminal. |
| `POST` | `/ai-assistance/transcribe` | any | multipart: `file` | `{text, success}` | Speech-to-text. MIME allowlist (webm/ogg/mp4/mpeg/x-m4a/wav/x-wav/flac) + size guard (`AI_STT_MAX_AUDIO_BYTES`, default 20 MiB). Audio is **ephemeral** — never persisted. Resolves a `transcription` task assignment (model advertising `audio_input`). 415 / 400 / 413 / 502 error codes. |

---

## Integrations

The integrations surface lives at `/api/v1/integrations/*` (see
[INTEGRATIONS_FRAMEWORK.md](INTEGRATIONS_FRAMEWORK.md) and the [integration SDK](dev/sdk/overview.md) for building
custom providers). Two **tokenless** inbound routes are exposed for external
systems that don't carry the platform JWT.

### Discovery + config-flow

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/integrations/available` | any | — | `List[{domain, manifest}]` | Manifests of every integration the `SYSTEM_ADMIN` has enabled (`SystemIntegration.is_enabled == True`). |
| `GET` | `/integrations/active?patient_id=*` | any | `patient_id` * | `List[{id, domain, instance_name, status, last_synced_at}]` | User's `UserIntegration` rows for a patient context. |
| `GET` | `/integrations/{domain}/documentation?file=?` | any | — | markdown dict | Reads markdown docs (docs-tree.json → README.md → DOCS.md). Path-traversal mitigated. |
| `GET` | `/integrations/{domain}/config-flow` | any | — | schema dict | The config UI schema. 400 if not enabled by admin, 404 if no config flow. |
| `POST` | `/integrations/{domain}/config-flow?patient_id=*&integration_id=?` | any | config payload | result dict | Validates + persists user config (encrypts secret fields, enforces per-user instance cap). Updates existing if `integration_id` supplied; else creates new (PENDING if OAuth/SMART, ACTIVE otherwise). |

### OAuth (Authorization Code + PKCE)

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/integrations/{domain}/oauth/start?integration_id=*&patient_id=*` | any | — | `{authorize_url, state}` | Discover + DCR + authorize URL. Stores PKCE verifier + endpoints + ids under opaque state in Redis. |
| `GET` | `/integrations/{domain}/oauth/callback?state=&code=` | one-shot Redis `state` | — | `302 redirect` | Exchanges code for tokens, flips integration to ACTIVE, redirects to the SPA `/connected` landing. |

### Instance management

| Method | Path | Auth | Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/integrations/instance/{integration_id}/details` | any | `patient_id` * | detail dict | Status, masked `user_config`, sync history (last 20), exposed biomarkers, recent data (30 obs), synced examinations, custom actions. |
| `GET` | `/integrations/instance/{integration_id}/debug-logs` | any | `patient_id` *, `limit?=200` | list of log dicts | `IntegrationDebugLog` rows. |
| `POST` | `/integrations/instance/{integration_id}/toggle-debug` | any | `patient_id` * | `{message, is_debug_enabled}` |  |
| `DELETE` | `/integrations/instance/{integration_id}` | any | `patient_id` * | `{message}` | Best-effort revokes OAuth tokens (RFC 7009). |
| `POST` | `/integrations/instance/{integration_id}/action/{action_id}` | any | `patient_id` * | provider ActionResult | 400 if the provider doesn't implement `execute_custom_action`. |
| `POST` | `/integrations/instance/{integration_id}/rotate-secret` | any | — | `{secret}` | Regenerates the HMAC `api_secret` (shown once; stored Fernet-encrypted); 500 on other failures. Runs every opt-in hook the provider supports (events / exams / catalog-proposals / HITL-proposals / documents). |

### HITL proposals (review + resolve)

Approve validates the payload against the kind's contract schema, then delegates
to the kind's server-side resolver — `catalog_proposal_service.apply_proposal`
for catalog kinds, or the canonical clinical service for the four
patient-record kinds (`create_event`, `create_examination`, `add_medication`,
`add_biomarker_to_examination`). See
[HITL proposals](dev/sdk/authoring-and-configuration.md#hitl-proposals-human-review).

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/integrations/instance/{integration_id}/proposals` | owner | `status?=proposed\|approved\|applied\|rejected\|failed\|expired\|superseded`, `limit?=100`, `offset?=0` | `List[IntegrationProposalResponse]` | Newest-first; deterministic `(created_at, id)` tiebreaker. |
| `GET` | `/integrations/instance/{integration_id}/proposals/{proposal_id}` | owner | — | `IntegrationProposalResponse` | 404 if proposal doesn't exist or belongs to another integration. Response carries `kind`, `card_variant`, `payload` (+ `payload_schema_version`), `final_payload`, `resolution_trace`. |
| `POST` | `/integrations/instance/{integration_id}/proposals/{proposal_id}/resolve` | ADMIN+ for `action=approve`; USER can `reject`/`cancel` | `{action: "approve"\|"reject"\|"cancel", payload?: Dict, note?: str}` | `IntegrationProposalResponse` (+ `applied_entity_id`, `error`) | Idempotent: repeating the recorded outcome returns 200 with the stored result; a conflicting decision on a terminal row (or a superseded proposal) returns 409. A payload that fails the kind schema (or retargets another patient) is 400 before any write. On `approve`, `applied_entity_id` is the written row's UUID; an apply error records `failed`. |

### Notifications (actionable push)

Per-integration-instance notification preferences are managed via the unified [`/notifications/preferences`](#notifications) endpoints — not integration-scoped endpoints. Each integration instance's kinds are addressable as `integration:{integration_id}:{type_id}`.

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/integrations/{domain}/notification-action/{integration_id}/{action_id}` | any (tenant-scoped; caller must own the integration) | JSON passthrough | provider ActionResult | Dispatches a clicked action button (`type="post"`) on an integration-authored notification to `provider.handle_notification_action`. 400 if the extension does not declare the `notify` capability. |

### Inbound machine routes (HMAC — secrets are mandatory)

Every webhook/API-capable integration instance is **auto-provisioned with a
per-instance HMAC secret at creation** (Fernet-encrypted with row binding, shown
**once** in the config-flow response — store it immediately). The integration
UUID is an identifier, never a credential: instances without a secret are
rejected with 401 (fail-closed). Bodies are capped at 40 MiB.

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/integrations/{domain}/webhook/{integration_id}` | **mandatory** HMAC via `webhook_secret` | raw Request body (≤40 MiB) | `{message, metrics_synced}` | Signature headers: `X-Webhook-Signature`, `X-Webhook-Signature-256`, `X-Hub-Signature-256` (GitHub; `sha256=<hex>` tolerated). **Replay protection:** with `X-Webhook-Timestamp` the canonical timestamped scheme applies (±5-min skew); bare body-MACs are deduplicated via a 10-minute Redis replay guard. Calls `provider.handle_webhook`, maps observations, splits telemetry vs FHIR, writes `IntegrationSyncLog`, dispatches post-sync notifications. |
| `ANY` | `/integrations/{domain}/api/{integration_id}/{path:path}` | **mandatory** HMAC via `api_secret` | raw Request body (≤40 MiB) | provider-defined | Two-way API proxy. Requires `X-Api-Signature` = HMAC-SHA256 of `METHOD\n<path>[?query]\n<timestamp>\n<raw_body>` **and** a mandatory `X-Api-Timestamp` (±5-min skew; no timestamp → 401). The MAC covers the **query string**, so query params can't be tampered on a captured request. `GET /status` without a signature returns only `{status, server_time}` (pairing probe + clock resync); see [the typed errors](dev/sdk/reference.md#the-bridge-error-vocabulary-bridgeerror)
for the notification-action contract.

---

## Operations

### `task-monitor`

Tenant-scoped except `SYSTEM_ADMIN` (audit B1), and **patient-scoped**:
restricted roles (USER; a MANAGER under
`manager_scope=assigned`) see only rows whose patient they can reach — own
records or through an active grant. Documents with no patient stay
uploader-only for those roles. Cross-tenant/unreachable retries return 404.

| Method | Path | Auth | Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/task-monitor/documents/processing` | any | `patient_id?`, `status?`, `limit?=50` | list of doc dicts | Stuck-doc debug view. Non-sensitive metadata only. |
| `GET` | `/task-monitor/examinations/processing` | any | `patient_id?`, `status?`, `limit?=50` | list of exam dicts | Stuck-exam debug view. |
| `POST` | `/task-monitor/documents/retry/{document_id}` | any | — | `{message, document_id}` | Reset to `uploaded` + re-enqueue OCR. 400 if already `completed`. |
| `POST` | `/task-monitor/examinations/retry/{examination_id}` | any | — | `{message, examination_id}` | Clear extraction status for re-pickup. |
| `GET` | `/task-monitor/stats` | any | — | `{documents, examinations, timestamp}` | Aggregate counts per status + stalled-task counts (>10 min). |

### `ws` — real-time streams

Auth via the `["bearer", <jwt>]` `Sec-WebSocket-Protocol` subprotocol (deprecated
`?token=` query fallback). 30s server-side keepalive ping. The frontend's
`useNotificationStream` hook auto-reconnects with 5s backoff and falls back to a
30s unread-count poll if the socket can't open.

| Method | Path | Subscribe channel | Notes |
|---|---|---|---|
| `WS` | `/ws/tasks` | `tenant:{tenant_id}:tasks` | Live task-progress stream (document/examination status + progress). |
| `WS` | `/ws/notifications` | `user:{user_id}:notifications` | Per-user notification stream. |

### `export`

See [EXPORT_IMPORT.md](EXPORT_IMPORT.md) for the full format/scopes/restore spec.

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/export` | `get_current_user` (SMART-style scope authz) | `BackupRequest` `{scope: patient\|group\|system, export_type: fhir_only\|full_backup\|catalog_only, patient_ids?}` | `ExportJobResponse` | Enqueues the Celery `export_backup` task. `patient` scope — any role (`USER` limited to a single own patient); `group` — `MANAGER`+; `system` — `ADMIN`+. `SYSTEM_ADMIN` bypasses. |
| `GET` | `/export/jobs` | any | `limit?=50` | `ExportJobListResponse` | USER sees only their own patient-scoped jobs; MANAGER+ the tenant's. |
| `GET` | `/export/jobs/{job_id}` | any | — | `ExportJobResponse` | Object-gated: USER → own patient-scoped job only (404 otherwise). |
| `GET` | `/export/jobs/{job_id}/download` | any | — | `application/octet-stream` | FHIR Bundle `.fhir.json`, full-backup `.zip`, or catalog `.catalog.json`. Same object gate as job status. |

### `import`

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/import/backup` | `get_current_user` | multipart: `file` *, `auto_map_biomarkers?`, `use_ai_normalization?` | `ImportJobResponse` | Enqueues Celery. Accepts ZIP or bare FHIR Bundle / catalog JSON. |
| `POST` | `/import/fhir` | `get_current_user` | multipart: `file` (`.json`), `patient_id?`, `validate?`, `auto_map_biomarkers?`, `use_ai_normalization?` | dict | Synchronous FHIR Bundle import (smaller bundles). |
| `POST` | `/import/csv` | `get_current_user` | multipart: `file` (`.csv`), `patient_id?`, `delimiter?`, `has_header?` | dict | Synchronous CSV import (legacy). |
| `POST` | `/import/ocr` | `get_current_user` | multipart: `file`, `patient_id?` | dict | OCR extraction. Provider/model/key resolve from the `ocr` AI task assignment (scoped resolution + BYOK key) — no model configuration crosses the request. |
| `GET` | `/import/jobs` | any | `limit?=50` | `ImportJobListResponse` | USER sees only their own jobs; MANAGER+ the tenant's, newest first. |
| `GET` | `/import/jobs/{job_id}` | any | — | `ImportJobResponse` | Object-gated: USER → own job only (404 otherwise). Status + `restore_result` (created/updated counts, `manifest_verified`, `fhir_validated`). |

### `doctors`

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/doctors` | any | `tenant_id?` (`SYSTEM_ADMIN`), `user_id?` (`SYSTEM_ADMIN`) | `List[DoctorResponse]` |  |
| `GET` | `/doctors/{doctor_id}` | any | — | `DoctorResponse` | 404 outside tenant. |
| `POST` | `/doctors` | any | `DoctorCreate` | `DoctorResponse` (`201`) | Bound to caller's tenant. |
| `PUT` | `/doctors/{doctor_id}` | any | `DoctorUpdate` | `DoctorResponse` |  |
| `DELETE` | `/doctors/{doctor_id}` | any | — | `204` |  |

### `organizations`

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/organizations` | any | — | `List[Organization]` | Tenant-scoped. |
| `GET` | `/organizations/{organization_id}` | any | — | `OrganizationWithDetails` | Enriched with doctors + departments (units). |
| `POST` | `/organizations` | any | `OrganizationCreate` | `Organization` (`201`) |  |
| `PUT` | `/organizations/{organization_id}` | any | `OrganizationUpdate` | `Organization` |  |
| `DELETE` | `/organizations/{organization_id}` | any | — | `204` |  |

---

## FHIR R4 facade

> **Interop surface only** — the frontend uses the domain endpoints above.
> Canonical FHIR R4 for external systems, export/import, and SMART-on-FHIR.
> See [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md) for the developer guide.

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/fhir/R4/metadata` | **none** (FHIR spec) | `CapabilityStatement` built dynamically from `RESOURCE_REGISTRY`. Cacheable 5 min. |
| `GET` | `/fhir/R4/{resource_type}` | any | Search-type interaction → FHIR searchset `Bundle` with pagination links. Honors `_id`, `_lastUpdated`, `_count`, `_sort`, `_format` + per-resource params (`patient`, `code`, `date`, …). OperationOutcome 404 for unknown types. |
| `GET` | `/fhir/R4/{resource_type}/{resource_id}` | any | Read interaction (ETag). 410 Gone for soft-deleted resources. |
| `POST` | `/fhir/R4/{resource_type}` | any | Create — `201` + `Location` + `ETag` + `Last-Modified`. |
| `PUT` | `/fhir/R4/{resource_type}/{resource_id}` | any | Update (full replacement). `If-Match` honored → 412 on version mismatch. |
| `DELETE` | `/fhir/R4/{resource_type}/{resource_id}` | any | Soft-delete → subsequent reads return 410 Gone. Returns `204`. |

**20 registered resource types:** Patient, Observation, Condition (← ClinicalEvent),
EpisodeOfCare (← ClinicalEvent journey view), Encounter (← ExaminationModel),
AllergyIntolerance, MedicationStatement, MedicationRequest (both ← Medication
via `intent` discriminator), Medication (← MedicationCatalog, read-only),
Substance (read-only), Immunization (patient dose records; REST CRUD at `/vaccines/*`),
DiagnosticReport, DocumentReference (← DocumentModel), Device, Communication,
Organization, Practitioner, Provenance (immutable), plus two **computed**
terminology resources — CodeSystem and ValueSet — that project disease-kind
concepts from the `concepts` table without a dedicated backing table.

**Provenance-on-write:** every facade create/update/delete records a `Provenance`
(best-effort via `provenance_service.record_provenance()`).

---

## Error handling

Errors flow through one global handler in `app/main.py` that returns
`{message, detail, correlation_id}` and logs server-side. The global handler
maps the domain exception hierarchy (`DomainError`, `NotFoundError`,
`AuthorizationError`, `ValidationError`, `ConflictError`, `ConcurrencyError`) to
their HTTP statuses; access-check helpers in `app/services/auth/access.py` raise these.

Example shape (HTTP 500, `DEBUG=False`):

```json
{
  "message": "Internal server error",
  "detail": "An internal error occurred. Please try again later.",
  "correlation_id": "7e2cf4b8-9b5d-4e3a-8c1f-2a3b4c5d6e7f"
}
```

In development (`DEBUG=True`) the `detail` field carries the verbose exception
text for easier debugging.

### HTTP status codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 201 | Created |
| 202 | Accepted (async operation) |
| 204 | No Content (delete success) |
| 400 | Bad Request / `ValidationError` |
| 401 | Unauthorized (missing/invalid token) |
| 403 | Forbidden (role/tenant/patient-access/scope denied — `AuthorizationError` / `CatalogPermissionDenied`) |
| 404 | Not Found (also returned for cross-tenant calls — no leak) |
| 405 | Method Not Allowed (promote on catalogs without a scope transition — concept) |
| 409 | Conflict (`ConflictError` — e.g. catalog promote permission denied; sync already running; a conflicting HITL decision on a terminal proposal) |
| 410 | Gone (FHIR R4 facade: tombstone of a soft-deleted resource) |
| 412 | Precondition Failed (FHIR R4 facade: `If-Match` version mismatch) |
| 413 | Payload Too Large (upload exceeds `MAX_UPLOAD_SIZE`) |
| 415 | Unsupported Media Type (e.g. audio transcription MIME outside allowlist) |
| 422 | Validation error |
| 429 | Too Many Requests — auth endpoints (`/auth/login`, `/register`, `/refresh`, `/invite`) are Redis-backed rate-limited per client IP; `/integrations/instance/{id}/sync` returns 429 on upstream rate limit. Degrades open if Redis is down. |
| 500 | Internal Server Error |
| 502 | Bad Gateway (upstream AI/OCR/STT provider failure) |

---

## See also

- [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md) — canonical FHIR R4 interop surface
- [TENANCY_AND_USER_MANAGEMENT.md](TENANCY_AND_USER_MANAGEMENT.md) — tenant isolation, RBAC, invite/bootstrap flows
- [CLINICAL_EVENTS.md](CLINICAL_EVENTS.md) — clinical-events type blueprint + journey schema
- [NOTIFICATION_SYSTEM.md](NOTIFICATION_SYSTEM.md) — unified notification fan-out model
- [TELEMETRY_AND_AGGREGATION.md](TELEMETRY_AND_AGGREGATION.md) — TimescaleDB telemetry split + OHLC aggregation
- [TAXONOMY.md](TAXONOMY.md) — unified taxonomy / knowledge graph
- [AI_SYSTEM.md](AI_SYSTEM.md) — AI provider factory, agentic chat, HITL wall
- [INTEGRATIONS_FRAMEWORK.md](INTEGRATIONS_FRAMEWORK.md) — integrations discovery; the [integration SDK doc set](dev/sdk/overview.md) covers the SDK
- [EXPORT_IMPORT.md](EXPORT_IMPORT.md) — backup format / scopes / restore

When running locally, visit **http://localhost:8000/docs** (Swagger UI) or
**http://localhost:8000/redoc** for the always-up-to-date OpenAPI rendering of
every route, request body, response model, and query parameter.
