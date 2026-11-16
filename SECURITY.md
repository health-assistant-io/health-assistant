# Security Policy

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue:

- **Email**: constliakos@gmail.com (put "health-assistant security" in the subject), or
- **GitHub Security Advisory**: use "Report a vulnerability" on the
  [Security tab](https://github.com/health-assistant-io/health-assistant/security/advisories/new).

Health Assistant stores real clinical records (PHI) — please include a
description, the affected version (`docker compose exec backend python -c
"from app.core.config import settings; print(settings.VERSION)"` or Settings →
About), and reproduction steps. You will get an acknowledgement within a few
days and a fix timeline once the issue is triaged. Fixes land as patch
releases; reporters are credited in the release notes unless they prefer
otherwise.

## Scope

Health Assistant is a **server product** (family identity class S): always authenticated,
multi-tenant, self-hosted. This repo
(`core/`) holds the FastAPI backend, the web SPA, and the integrations
framework. The supported security scope is:

- the backend API (`backend/app`) — the family identity surface
  (`/api/v1/auth/*`, `/api/v1/me/*`, `/api/v1/admin/*`, `/api/v1/users/*`,
  `/api/v1/tenants/*`) and the clinical domain API, plus the external FHIR
  R4 facade (`/api/v1/fhir/R4/*`) and its OAuth2 token endpoint,
- the integrations framework (`integrations/`): the machine/device HMAC
  surface (`/{domain}/webhook/{id}`, `/{domain}/api/{id}/{path}`), the
  Health Assistant Bridge (the Android app's backend surface) with its
  Python/TS/Kotlin SDKs, and the FHIR-server/webhook providers,
- the datastore: PostgreSQL 16 (`neuronection_health`, TimescaleDB image,
  `neuronection_health_owner`/`neuronection_health_app` roles), Redis (jti store + rate
  limits + pub/sub), and the `uploads/` document store,
- the web SPA (`frontend/`) as served by the shipped nginx configs,
- the deployment configurations (`docker/`, nginx HTTP/TLS variants,
  CI pipelines), and the at-rest encryption of provider/API keys.

Out of scope by design: the Android companion app itself (sibling repo
`../app/` has its own policy — only the in-repo bridge SDK and the backend
half of the protocol are ours), the public demo stack (sibling checkout
`../demo/`, referenced below), and any exposure an operator adds beyond the
shipped configs (proxy chains not matching `HA_TRUSTED_PROXY_COUNT`,
plain-HTTP internet exposure, disabled `HA_COOKIE_SECURE`, and so on).

## Family baseline (enforced in CI where possible)

- Secrets never committed; `.env` gitignored; `.env.example` value-free.
- API keys: Fernet-encrypted in the database under the `HA_DATA_KEY`
  family (health is a server product — the keyring path does not apply),
  never in code, env files of clients, or logs; masked (`***…`) on read.
- AI model output is untrusted input: schema-validated, allowlisted tools,
  prompt-injection guard on user input (high-risk patterns block by
  default).
- Dependencies: Dependabot + pip-audit; lockfiles committed.

## Authorization model (permission catalog, scopes, route gate)

Authorization is one model, defined in code under `backend/app/core/authz/`
(not scattered role strings and not DB rows):

- **Permission catalog** — a closed vocabulary of `resource:action`
  permissions (`patient:read`, `export:system`, `seed_source:approve`, …).
  The role → permission defaults for the four system roles
  (SYSTEM_ADMIN / ADMIN / MANAGER / USER) live in code, are pinned by tests,
  and generate the role matrix. Destructive deletes are ADMIN/SYSTEM_ADMIN
  in the baseline; self-erasure for regular users goes through the audited
  lifecycle request path.
- **Access scopes** — `system` / `tenant` / `own` / `grant` narrow who a
  permission reaches (care-team grants are the `grant` scope). **Break-glass
  is not a permission**: it is a time-boxed, audited elevation recorded in
  `access_elevations`, never a role grant.
- **Central authorize + route gate** — one central authorize(permission,
  scope, resource) seam: every route declares its authorization policy where
  it is defined (or holds a reviewed inventory entry while the rollout
  migration is in progress); a new route with
  neither fails CI, the inventory allowlist can only shrink (removed keys
  are recorded and may not return), and token-principal surfaces — the FHIR
  R4 facade, presigned downloads (5-minute TTL tokens bound to
  `doc_id` **and** `tenant_id`, re-verified against the live document and
  the active user on every download/preview — S8), OAuth token/revoke,
  machine HMAC
  webhook/API routes, WebSockets — are pinned route classes with their own
  policy mechanisms, never allowlist filler.
  **Rollout status (Program Phase 6, part A — enforcement is live):** the
  central route-policy resolver (`backend/app/core/authz/route_policy.py`)
  is the declaration of record — every route resolves to a permission plus
  a required access scope, or to an explicit auth-only self-service rule,
  or to its token-principal class. The app-level gate
  (`backend/app/core/authz/enforce.py`, registered as an app dependency)
  enforces it before every HTTP route: a session route without a verified
  principal answers 401, a role that lacks the permission or does not hold
  it at the required scope answers 403 (gate denials carry an
  `X-Authz-Denied: 1` response header — the SPA's discriminator so its
  "access denied" toast fires only for the gate, never for the resource
  layer's row-level 403s), token/public classes are left to
  their own mechanisms, and a route that resolves to nothing fails closed
  with 403. WebSocket handshakes run the same policy check in the two WS
  handlers. `RoleChecker`/`require_admin` remain as defense-in-depth pins
  (redundant checks, not a compat shim). The per-route table is
  `docs/dev/AUTHZ_ROUTE_POLICIES.md`; the class baseline is
  `backend/app/core/authz/route_inventory.json` (the allowlist is the class
  baseline, not a dumping ground). Scope mapping note (the Phase-6B fix):
  the patient collection list (`GET /api/v1/patients`) requires
  `patient:read` at **`own`** scope, not tenant — the handler pins `USER`
  accounts to `Patient.user_id == caller`, so they pass the gate and get a
  list of their own records, while staff roles (`MANAGER`/`ADMIN`/
  `SYSTEM_ADMIN`) hold the permission at a wider scope (`tenant`/`system`),
  which covers `own`, and see the tenant-wide list. The row-level detail
  route keeps `patient:read @ grant` (ownership/hidden-404 answers stay
  with the resource layer). User administration (`user:read`) is
  ADMIN-side: `USER` and `MANAGER` are denied at the gate, and
  `PUT /users/{id}` (user:role_assign @ tenant) is admin-only — profile
  self-service lives at `/settings/user`.
- **Grant-aware query filter (Program Phase 7A)** — patient-level grants
  (`patient_access_grants`: care team, patient share, guardian; ranked
  `read < write < admin`, optional expiry, per-source lifecycle) reach
  **every** list/search/analytics/export surface through
  `accessible_patient_ids()` / `patient_access_filter()`
  (`backend/app/services/auth/access.py`) — own ∪ active non-expired
  grants, tenant-wide for ADMIN/SYSTEM_ADMIN and for MANAGER under the
  default `manager_scope=tenant` (an `assigned` tenant narrows its
  managers to own ∪ grants, read per request). Lists never rely on
  per-resource checks alone — that is the list-path leak class. A read
  grant never satisfies a write (`check_patient_access(..., required=...)`
  at every write site), and **export is never implied by a grant**
  (`check_patient_export_access`: role scope/ownership only). Document
  visibility derives from patient access, not `owner_id`
  shortcuts.

The generated matrix (role → permission defaults plus the full route
inventory by principal class) is at
[`docs/dev/AUTHZ_MATRIX.md`](docs/dev/AUTHZ_MATRIX.md); regenerate it with
`cd backend && HA_ENV_FILE=.env.test uv run python scripts/authz_inventory.py matrix`.

### Break-glass elevations (Program Phase 12A)

Break-glass is a **first-class elevation row** in `access_elevations`,
never a grant source and never a role permission:

- **Request** — a holder of `break_glass:request` (ADMIN/MANAGER;
  SYSTEM_ADMIN for `scope=system` rows, which are instance-level with a
  NULL tenant) opens a request: a reason, a **subset of the permissions the
  requester already holds** (an elevation widens the effective scope of
  held permissions; it never grants a permission the role lacks), and a
  duration capped at 240 minutes (one live elevation per principal per
  scope).
- **Two-person approval** — the approver is a second person (a DB CHECK
  enforces `requested_by <> approved_by`): a `tenant` elevation needs a
  second **ADMIN of the same tenant**, a `system` elevation a second
  **SYSTEM_ADMIN**. Approval stamps `approved_at`/`starts_at` and
  `expires_at = starts_at + duration`; the window auto-expires (the beat
  sweep `expire_access_elevations` flips due rows to `expired` every 15
  minutes, and the consultation predicate `expires_at > now()` is
  evaluated in SQL regardless).
- **Consultation** — the central gate consults active elevations for the
  caller on every session request (one indexed query; nothing is cached
  into the token, so an approval, an early revoke or the expiry are
  honored on the next request). A tenant elevation widens its listed
  permissions to **tenant** reach; a system elevation to **system** reach
  for its SYSTEM_ADMIN requester only — a tenant window can never open a
  system-scoped surface. Requests authorized under a window are audited
  (`elevation_use`, fail-closed) with the elevation id, permission, path
  and method.
- **Audit + notification** — every transition (`elevation_request` /
  `elevation_approve` / `elevation_deny` / `elevation_revoke` /
  `elevation_expire`) writes an audit row, and the affected parties are
  notified; eligible approvers are notified at request time. Refused
  decisions are audited too (fail-closed `elevation_approve_denied` /
  `elevation_deny_denied` / `elevation_revoke_denied`, including the
  hidden cross-tenant 404 and self-approval attempts), so the ledger
  answers "who tried" as well as "who decided".
- **Single-admin tenants have no elevation path**: the only
  eligible approver is the requester, self-approval is refused, and the
  request simply stays pending. Recovery is the **host rescue runbook**
  below — there is no self-approval, no time-delayed fallback, and no
  bypass endpoint.

**Host rescue runbook (single-admin lockout).** When the only admin of a
self-hosted instance is locked out (lost TOTP, demoted role, SSO outage),
the person who controls the deployment recovers from the **host console** —
a genuinely separate authentication factor:

1. Stop the app services (`docker compose stop app worker beat`).
2. From the host, run the ops tooling against the database:
   `docker compose run --rm app uv run python scripts/create_system_admin.py
   --email <admin@example.com> --password <new-strong-password>` restores
   (or re-promotes) the admin account. To clear a lost TOTP enrollment for
   an existing admin, clear the two MFA columns on the user row with a
   single `psql` statement (`UPDATE users SET mfa_enforced = false,
   mfa_pending = NULL, mfa_secret_enc = NULL, mfa_recovery_codes = NULL,
   token_version = token_version + 1 WHERE email = '<admin@example.com>'` —
   `mfa_enabled` is derived from `mfa_secret_enc`, so nulling it clears the
   enrollment, nulling `mfa_recovery_codes` drops the unused codes, and the
   `token_version` bump revokes any hijacked live sessions).
3. Record the intervention in your ops log; the application side records
   the resulting admin actions in the audit stream at the next write.
4. Start the app services again (`docker compose start app worker beat`).

Host access is the factor an attacker must hold independently; the runbook
adds zero API surface, works with zero other users, and matches the
self-hosted deployment model.

### Enterprise identity: per-tenant SSO + SCIM (Program Phase 12B)

**Per-tenant OIDC SSO** (`tenant_sso_configs`). Each tenant
binds its own OpenID Connect provider (issuer + client). The client secret
never crosses the API: configurations carry a **secret slot name**, and the
value resolves only on the host — OS keyring (`health-assistant/tenant-sso`)
first, `HA_SSO_CLIENT_SECRET_<SLOT>` env fallback second. The flow is
authorization-code + PKCE (S256) with server-side `state`/`nonce` (single-use,
TTL-bounded); the ID token verifies against the provider's JWKS (RS256),
with `iss`/`aud`/`exp`/`nonce` all enforced. The laws:

- **`email_verified` is required** — an unverified provider address never
  signs in or links (typed refusal `sso_email_unverified`).
- **Identity links are link-only** (`user_identities`, unique `(issuer, subject)`) —
  one external identity maps to exactly one local account in exactly one
  tenant. Accounts are **never merged**, in either direction.
- **Same verified email, no link yet** → the typed 409 `sso_link_required`
  carrying a one-time link intent: the local user signs in with their
  password and confirms the link while authenticated (`POST /auth/sso/link`).
  A verified address belonging to another tenant answers `sso_email_conflict`.
- **JIT provisioning is capped twice**: the address domain must be in the
  config's `allowed_domains`, and `jit_enabled` must be on. The provisioned
  role comes from the `group_role_map` (first mapped group) or
  `default_role` — both validated to {USER, MANAGER, ADMIN}. **SSO is never
  a path to SYSTEM_ADMIN.**
- **Group→role re-mapping runs on every login**; every actual change is
  audited (`sso_role_change`) and **bumps `token_version`** (plus a hot-layer
  sweep), so a demotion takes effect immediately — the admin/SCIM rule, not
  the token's next refresh.
- **One enabled config per email domain**: a domain already claimed by
  another enabled binding (any tenant) is refused with a typed 409, and an
  enabled binding's `issuer` is immutable (identity links are keyed on
  `(issuer, subject)`; disable it first).
- Internal sessions are minted by the product (`auth_mode="oidc"`); the IdP
  never signs an internal API token.
- **The local password path is untouched**: a disabled, misconfigured or
  unreachable IdP can only fail the SSO flow (typed `sso_*` codes) — the
  local login and the break-glass runbook above keep working.

**SCIM 2.0** (`/api/v1/scim/v2/Users|Groups`). Directory clients present a
**tenant-bounded hashed bearer** token (`scim_tokens`): only the sha256 of the
value is stored, the plaintext is shown once at creation (admin API) and
never stored or logged; revocation flips `active` (kept as the
accountability ledger); a token's reach is exactly its tenant (a deleted
tenant cascades its tokens — every stale bearer 401s at once). SCIM
delete — or `active=false` — is **deprovision**: the account is deactivated
and its `token_version` bumped, so every live session dies at the next
request; records are never hard-deleted by a directory client. Groups carry
no role of their own: the tenant SSO config's `group_role_map` re-roles
members when membership moves (audited, same non-SYSTEM_ADMIN cap).

### Step-up re-authentication (Program Phase 12C1)

A live session proves the account signed in at some point; five actions
need proof that the owner is at the keyboard **now**:

| Action | Gated surface |
| :--- | :--- |
| `role_change` | `PUT /users/{id}` when the role actually changes |
| `export` | `POST /export` (a full-record archive is bulk PHI egress) |
| `break_glass` | `POST /admin/access-elevations` and its `/approve` (deny/revoke only narrow access and are not gated) |
| `grant_admin` | `POST /patients/{id}/access`, and `PATCH` when it raises a grant to `admin` |
| `mfa_change` | `/me/mfa/enroll` + `/confirm` (TOTP), `/me/mfa/passkeys/options` + `/verify` (registration) and the passkey delete |

`POST /auth/step-up` verifies the factors the account has — the local
password when one is set, TOTP/recovery when MFA is enrolled — and records
a proof keyed `stepup:{fid}:{action}` in the token store: **session-bound**
(the sign-in family, not the bearer, so a rotated access token keeps it),
**action-bound** (a role-change proof never authorizes an export),
**window-bound** (10 minutes; the payload carries `issued_at`, so expiry is
decided without trusting the store's clock) and **single-family** (another
device never sees it). An SSO-only account uses a fresh federated sign-in
instead: the session carries the IdP's `amr`/`auth_time` and a
second-factor assertion inside the window counts (`methods: ["federated"]`)
— and a registered passkey proves the same actions through
`POST /auth/step-up/passkeys/{options,verify}` (`methods: ["webauthn"]`,
program 12C2), recording the same family-bound proof.

Enforcement never degrades open: an unreadable or missing proof answers the
typed `403 {"code": "step_up_required", "action": …, "methods": [...]}` —
`methods` always rides the denial, and is empty with `reason:
"no_session_family"` for a principal that cannot step up at all (an opaque
`pat`, a bare auth token) — and `step_up_unavailable` when the account has
no usable factor. So a stolen bearer alone cannot change a role, export the
record, open an elevation, hand a patient to a third party or enroll a new
factor. Because an enrolled TOTP secret is itself an accepted step-up
factor, the self-service TOTP enrollment is gated too: otherwise a hijacked
session could mint one and bootstrap proofs for every other action (the
login-time forced-enrollment door, driven by the challenge token, is a
different surface and stays open). Normal reads never pay the gate. Every
minted proof and every refusal is audited (`auth.step_up`).

### SSO parity: the IdP is a first factor, never a policy exemption

The per-tenant SSO callback enforces the same tenant security policy as
`POST /auth/login` (program 12C1):

- **IP allowlist** — checked **before** any provisioning (the flow state
  names the tenant), so a denied address leaves no JIT account behind:
  `403 ip_not_allowed`, audited denied.
- **MFA requirement** — a local TOTP secret, `mfa_enforced`, or a
  policy-listed role forces the existing single-use `mfa_challenge`
  (`401 {"detail": "mfa_required", "mfa_token", "enrollment_needed",
  "origin": "sso"}`). The proof of a second factor by the IdP is accepted
  only when it evidences one — an RFC 8176 second-factor `amr` value
  (`otp`, `hwk`, `mfa`, …) or a multi-factor `acr` (`urn:…:acr:mfa`, a
  numeric level ≥ 2). Absence is not evidence. The challenge completes at
  `POST /auth/mfa/verify`, which mints the session with
  `auth_mode="oidc"` (never a silent downgrade to the password path).
- **Policy flips** apply to the next sign-in, not to live sessions — MFA
  gates sign-in, not an established bearer (the same rule as the password
  path).

The IdP's `amr` / `auth_time` travel onto the session as product claims so
step-up freshness can be evaluated; they are dropped at refresh, so
federated freshness decays with the sign-in.

### Session devices and sign-in alerts (Program Phase 12C1)

`auth_sessions` (migration `0019_session_metadata`) carries the device
facts: `ip` and `user_agent` as observed at issuance, and `last_seen_at`
written on verification with a **≥60 s throttle** (a Redis `SET NX` tick,
so a busy request stream does not turn into a write stream; the stamp is
bookkeeping and degrades to "no stamp" rather than failing a request).
`GET /me/sessions` returns them per family plus `current`.

`POST /me/sessions/revoke-others` signs out every other device **without**
the `token_version` bump of `/auth/logout-all`: the caller keeps working,
each revoked family's refresh chain dies at once and its bound access jti
leaves the hot layer, and the sweep is audited (`session.revoke_others`).

A successful sign-in (password, MFA completion or SSO) whose
`(ip, user agent)` matches **no live family** of the account emits a
`SECURITY_EVENT` notification (`category: security`, severity `warning`,
in-app + push — never email). The event type is safety-critical in the
notification registry: muting the whole `SYSTEM` source does not silence
it (channel preferences still apply), and a 24-hour dedup key per device
prevents a crash-looping client from flooding the inbox.

### Passkeys / WebAuthn (Program Phase 12C2)

A passkey is a WebAuthn Level 2 credential stored in
`webauthn_credentials` (migration `0020_webauthn`): the credential id, the
COSE public key, the signature counter, transport hints, the AAGUID and the
backup flags. The private key never leaves the authenticator; the database
holds no secret that can authenticate on its own. RLS is the NOT-NULL-tenant
policy (the table is a `users`-anchored record erased by the user/tenant
cascade), so a passkey is only ever visible inside its tenant.

Ceremony posture:

- **User verification is required** for registration and every assertion
  (possession alone is not enough), and the RP id/origin are checked against
  `HA_WEBAUTHN_RP_ID`/`HA_WEBAUTHN_ORIGIN`. Outside development an
  unconfigured instance refuses with `503 webauthn_not_configured` — there
  is no localhost fallback in production.
- **Challenges are single-use and five-minute-lived** in the hot layer and
  are consumed *before* signature verification, so a replay (including a
  replayed malformed attempt) fails closed.
- **Clone detection** — a non-zero signature counter that fails to advance
  disables the credential, audits `auth.passkey_clone_detected` and refuses
  the attempt. The counter is read from `authenticatorData` **only after**
  the assertion verifies (the field is attacker-controlled before that), so
  knowing a credential id is never enough to disable someone's passkey.
  Syncable credentials are stored with their backup flags; a zero counter is
  not treated as evidence either way.
- **Registration/deletion are step-up gated** (`mfa_change`, same
  password/TOTP/existing-passkey proof surface). The **last** remaining
  factor cannot be deleted while `mfa_enforced` or a policy-listed role
  requires MFA; removing one of several factors is allowed.
- **A passkey assertion satisfies the MFA policy** (it is a second factor
  and a login method). Password sign-in then answers the usual
  `mfa_required` challenge with `methods: ["webauthn", "totp"]`; a
  passkey-only account gets `enrollment_needed:false`. A passkey assertion
  can also mint a step-up proof (`methods:["webauthn"]`) for role changes,
  exports, break-glass, admin grants and factor changes.
- **Audit** — `mfa.passkey_register`, `mfa.passkey_delete`,
  `auth.passkey_login`, `auth.passkey_clone_detected` and the step-up proof
  are written fail-closed; refusals are recorded with the cause.

**Lost-device recovery.** A passkey is one factor, not the only one by
design. Recover with TOTP (or a TOTP recovery code) if enrolled, or with a
fresh SSO sign-in for an SSO-only account, then delete the lost credential
in Settings → Security and register a replacement. The host rescue runbook
above also covers passkeys: clearing `mfa_enforced`, `mfa_pending` and
`mfa_secret_enc` on the user row plus deleting that user's
`webauthn_credentials` rows (and bumping `token_version`) restores console
access without exposing an API bypass.

### Scoped API tokens / personal access tokens (Program Phase 12C2B)

Members can mint **personal access tokens** for CLI/scripts/home-automation
clients (`api_tokens`, migration `0021_api_tokens`; `pat` token kind — the
family's one opaque, non-JWT kind):

- **Secret handling.** The value is `ha_pat_<lookup-prefix>_<secret>`,
  returned **once** at creation and never stored, logged or echoed. The
  row keeps only the prefix (indexed lookup) and a sha256 of the whole
  value (unique, constant-time compare); 32 random bytes make a slow hash
  unnecessary. The hash-free projection is all that list/detail/audit ever
  render.
- **Least privilege, re-checked on every use.** A token carries a
  permission subset of its owner (validated at create, update and
  rotation) and the route gate authorizes with the **token's set, not the
  owner's role**. Each request re-intersects it with the owner's live
  role — a demotion narrows outstanding tokens immediately — and the
  effective scope is capped at **tenant**, so even a SYSTEM_ADMIN owner's
  token never sets `app.system`. Break-glass elevations are never
  consulted for a PAT.
- **Patient scope.** `all` = the owner's live reach, `granted` = the
  explicit `patient_ids` allowlist intersected with that reach, `none` =
  no patient-scoped access. `check_patient_access`,
  `check_patient_export_access` and `accessible_patient_ids` branch on
  the PAT principal before any role shortcut, so a revoked care-team
  grant, ownership change or `manager_scope` flip takes effect on the
  next request; export keeps its stricter own-only rule for USER owners.
- **No escalation.** A PAT cannot mint a session, refresh, another token,
  change a password, enroll a factor, revoke devices or step up — those
  credential-management surfaces answer 403, and step-up-gated actions
  answer `403 step_up_required` (no family means no proof can exist).
  Expired, revoked, malformed and cross-tenant credentials all answer the
  same 401.
- **Lifecycle and audit.** Rotation issues a replacement and revokes the
  predecessor in one transaction (`rotated_from`); revocation keeps the
  row as the accountability ledger; the owner or tenant cascade erases
  rows. `api_token.created|updated|rotated|revoked` are transactional,
  `api_token.used` is throttled to one row per token per minute, and
  `api_token.denied` records failed resolutions. Tenant RLS covers the
  table; the pre-auth lookup runs in the auth layer's system scope and
  the resolved principal plants only the tenant GUC.

### Scoped integration & extension principals

Integrations and extensions no longer act as their owner's role. Each
machine identity is an `integration_principals` row (migration
`0022_integration_principals`): an accountable owner plus the **only**
permissions the identity exercises.

- **No owner impersonation.** `resolve_integration_actor` returns the
  principal (owner id for audit + the permission ceiling), not a token
  carrying the owner's role. Service-layer writes still attribute
  `created_by`/`updated_by` to the owner; sync turns and bearer requests
  stamp the principal and write `integration_principal.used` with both
  ids (throttled — a read path never writes, so concurrent request turns
  cannot deadlock on the audit chain).
- **Three-way intersection at every use.** Stored `scopes` ∩ the owner's
  **live** role permissions ∩ the extension manifest's derived capability
  permissions. A demotion, a manifest change or a consent revocation
  narrows or stops the very next call; an empty intersection refuses
  (never a fallback).
- **Patient scope** mirrors PATs (`all|granted|none` + allowlist
  intersected with the owner's live reach), enforced by
  `check_patient_access`, `check_patient_export_access` and
  `accessible_patient_ids` before any role shortcut. Revocation mid-sync
  is therefore immediate; a connector principal is provisioned with
  exactly its integration's patient.
- **Credential handling.** An admin/owner-created principal receives
  `ha_int_<prefix>_<secret>` **once**; only the indexed prefix and a
  sha256 persist. Internally-provisioned connector principals carry no
  bearer at all. Rotation revokes the predecessor in the same transaction;
  revoked, rotated, expired, cross-tenant and scope-less credentials all
  answer 401 and write `integration_principal.denied`.
- **No escalation.** A principal is capped at tenant scope (a
  SYSTEM_ADMIN owner's principal never sets `app.system`), is never
  consulted for break-glass, cannot manage principals/tokens or reach any
  credential-management surface (403), and has no path to mint a session,
  refresh, factor or step-up proof.
- **Host-enforced extension tiers.** The manifest declaration is the grant:
  T2 receives exactly the declared capability set (consent-intersected) and
  no database handle; T3 MCP tool calls map their risk class to the
  principal's scopes; T0 is data-only; an undeclared capability or data
  class is refused even when the provider object implements the hook.
- **Tenant isolation.** RLS covers the table; the pre-auth lookup runs in
  the auth layer's system scope and the resolved principal plants only the
  tenant GUC; the integration FK is `SET NULL` so an erased connector keeps
  the accountability row while the owner/tenant cascade erases it.

### Tenant security policy (Program Phase 8B)

Every tenant carries a security policy in its `tenants.settings` JSONB
(normalized on read, validated on write, **read per request — never cached
into tokens**, so a flip applies mid-session):

- `mfa_required_roles` — roles (subset of ADMIN/MANAGER/USER) whose members
  must complete MFA (TOTP **or** a passkey) before a session is issued;
  `SYSTEM_ADMIN` is always required (never stored, never waivable).
  Policy-required enrollment rides the same forced-enrollment challenge
  flow as admin-forced MFA, and the last remaining factor cannot be
  self-disabled while the policy lists the caller's role.
- `session_idle_minutes` / `session_max_hours` — the per-family window,
  enforced on **every** token verification via the family's Redis anchor
  (`sesspol:{fid}`; degrades open on a Redis outage) and on **refresh**
  from the authoritative `auth_sessions` row (max-age revokes the family).
- `concurrent_session_limit` — at issuance, live families beyond the limit
  evict the oldest-**created** first (the longest-lived device — not
  usage-based LRU): the refresh chain is revoked and the device's bound
  access jti is dropped from the hot layer; every eviction is audited
  (`session.evict`).
- `ip_allowlist` — optional CIDR allowlist gating session issuance
  (`POST /auth/login`) and refresh; denials are typed (`ip_not_allowed`)
  and audited. Deployments behind a reverse proxy must run the ASGI server
  with `--proxy-headers` (trusted `FORWARDED_ALLOW_IPS`) so the checked
  address is the real client.
- `patient_sharing` / `manager_scope` — the Phase-2 grant knobs
  (`patient_sharing` is the policy-API field of the stored
  `allow_patient_share` key), honored by the grants service per request.

Reads: `GET /tenants/{id}/security-policy` (gate `settings:read @ tenant` —
staff reads). Writes: `PATCH /tenants/{id}/security-policy`
(`settings:write @ tenant` plus the ADMIN door; SYSTEM_ADMIN manages any
tenant; cross-tenant is a hidden 404). Every change is audited
(`tenant.security_policy`, required/fail-closed) with a before/after diff
of exactly the changed keys; the audit row commits in the same transaction
as the change, so a policy change cannot land unrecorded. The audit
viewer/export is itself tenant-
scoped (`audit:read`): tenant admins see and export their own tenant's
stream, SYSTEM_ADMIN sees the system-wide view — export streams CSV/JSONL
with the chain metadata, is audited (`audit.export`), and reports a
truncation via `X-Audit-Export-Truncated`/`X-Audit-Export-Total` headers
when the 10 000-row cap bites.

## Row-level security (the second wall)

Below the application's `tenant_id` filters (which remain mandatory — RLS
is never a replacement), every tenant-scoped table carries Postgres
row-level security policies reading transaction-local GUCs planted at
every transaction begin by the engine's `after_begin` hook from
contextvars (`backend/app/core/tenant_context.py`, migration `0016_rls`,
tests run as the runtime role in `tests/test_rls_row_level_security.py`):

| GUC | Meaning | Set by |
|---|---|---|
| `app.tenant_id` | the principal's tenant (UUID; empty/missing = no access) | the auth layer from the verified principal |
| `app.system` | `on` only for the system actor | the auth layer (SYSTEM_ADMIN role), service jobs at their boundary, and boot-time database work (`app.core.boot`) — never from request data |
| `app.patient_id` | facade principal's patient binding (reserved; no policy consumes it yet) | the facade dependency |
| `app.actor` | observability only — visible in `pg_stat_activity` | same as above |

Policy shapes (fail closed — a missing or reset GUC is the empty string,
which parses as NULL and matches nothing): NOT NULL tenant tables get one
`FOR ALL` policy `tenant_id = <ctx> OR <system>`; nullable-tenant tables
split read/write — catalogs and broadcasts keep their system (`NULL`)
rows readable by every tenant while writes stay `tenant_id = <ctx> OR
<system>` (a tenant can never mint or repoint a system row); audit-flavor
tables (`audit_events`, `audit_chain_heads`, `catalog_audit_log`,
`erasure_requests`, `task_logs`, `ai_call_logs`, `seed_sources`) hide
NULL-tenant rows from plain tenants, and the system flag reads on
**every** row — not only NULL ones — so the system actor has full reach:
that is what the SYSTEM_ADMIN audit viewer/export, the hash-chain
verifier's foreign-tenant walk, the cross-tenant erasure ledger and the
AI-usage stats run on (pinned by app-role tests; the earlier
`system AND tenant_id IS NULL` shape blinded all of them). Tenant-derived
child tables (no `tenant_id` column) ride an `EXISTS` policy on their
parent — whose own policy enforces inside the subquery, so chains compose
(`seed_deferred_refs` → `seed_source_versions` → `seed_sources`); the
parent `EXISTS` materializes once (hashed SubPlan) with the GUC reads
hoisted, never per-row.

Every context surface is explicit — there is no ambient system flag:

- **HTTP requests** — the session verifier plants the principal's context
  before its own user lookup (a switched SYSTEM_ADMIN resolves through its
  `original_tenant_id` — the system flag stays role-driven, so a switched
  admin keeps its operator reach; the planted tenant GUC stays the home
  tenant as an identity-honest observability value, while the operation
  tenant lives on the token claim); the facade plants tenant + patient
  from the api token; presigned download/preview plant the
  signature-verified token's tenant; `app.system` is never derived from
  request data.
- **Boot** — the application lifespan's database steps (instance-fact
  initialization, the digest-gated bundled seed apply, integration
  registry load, demo seeding, the first-run setup-token zero-user count,
  stuck-examination cleanup) run inside explicit `boot:<step>` system
  scopes (`app/core/boot.py`, pinned by the app-role boot test) — boot
  time has no request context, and a closed context answers zero rows.
- **Auth bootstrap** (no principal exists yet) — the email/user/
  client/integration resolutions in login, refresh, register, MFA verify,
  setup, `oauth/token` and the tokenless-HMAC webhook/API routes run in an
  explicit system scope that is restored on exit; the tenant context is
  planted the moment the tenant is known.
- **Workers** — every Celery task runs inside a system scope planted at
  the task boundary (`async_task`) and restored on exit; the worker
  engine's sessions carry the same hook.
- **Audit lane** — `log_audit_action` plants the event's own chain context
  (tenant chain → tenant GUC; system chain → the system flag) on its
  dedicated session, regardless of the ambient context.
- **Migrations** run as the table owner (`neuronection_health_owner`),
  which RLS exempts by default — `FORCE ROW LEVEL SECURITY` is
  deliberately not set so data migrations keep cross-tenant reach; the
  runtime role (`neuronection_health_app`) owns no table (pinned by test).

**Deploy constraint (rolling deploys).** The policies read session GUCs
that only the new code plants. The order is one-directional: **ship the
new code first, then migrate** (or stop-then-migrate). A *new* process
against a not-yet-migrated database is fine — the GUCs are planted but no
policies exist yet, so they are inert. An *old* process against an
already-migrated database **fails closed on everything** (reads answer
zero rows, writes are refused outright), so the availability gap is short
and bounded by compose recreating the app services after the one-shot
`migrate` service completes. In compose the
`migrate` service already gates every app service via
`service_completed_successfully`, so a normal upgrade migrates before the
new containers start and no old process survives the migration window;
do not reorder that (no migrate-while-old-containers-run, and never
migrate before pulling the new images).

The exception inventory is code and test (`0016_rls` +
`test_rls_row_level_security.py`): Timescale's `telemetry_data` hypertable
cannot carry RLS (`FeatureNotSupported` on compressed hypertables — the
compression stays; the raw table remains granted for **writes, erasure
and retention**, whose service-layer tenant filters stay mandatory), and
every aggregate read goes through GUC-filtered scoped views — the
`telemetry_hourly/daily/monthly` continuous aggregates are never
RLS-covered (they read Timescale's internal materialization) so the raw
caggs are **revoked** from the runtime role and the `telemetry_*_scoped`
views — with the GUC filter inside the view definition — are the only
granted aggregate surface, while the trends live-aggregate path reads
`telemetry_live_scoped` (same GUC-filter shape over the raw hypertable)
instead of the raw table; global/instance tables (`tenants`, `units`,
`ai_task_defs`, `instance_settings`, …) have no tenant column;
`alembic_version` is revoked outright; runtime-managed langgraph
checkpoint tables sit outside the migration lane.

## Threat model

The family identity & auth standard (identity-auth §20, distributed
internally with the family conventions) defines these surfaces; this
table is the single source of truth for health's answers. Everything
below describes the code as it ships on `main` — gaps are listed
separately, never papered over.

| Surface | Answers for this repo |
|---|---|
| Auth surface | Unauthenticated (each Redis rate-limited per IP, `HA_RATELIMIT_AUTH` + per-account email window `HA_RATELIMIT_AUTH_EMAIL`; IP from the rightmost `HA_TRUSTED_PROXY_COUNT` XFF hops — spoofable headers ignored at 0): `POST /api/v1/auth/login` (20/min), `/auth/refresh` (30/min; needs a valid `nx_refresh` cookie or Bearer body), `/auth/setup` (5/min; only while uninitialized — 410 after; one-time setup token required off-localhost, bootstrap serialized by `pg_advisory_xact_lock`), `/auth/register` (5/min; invite-token-gated **and** `HA_REGISTRATION_ENABLED`), `/auth/demo-login` (404 unless `instance_settings.demo_mode=true`), `/auth/mfa/verify` + `/auth/mfa/enroll` (answer the 5-min single-use `mfa_challenge` JWT), plus read-only public metadata: `GET /auth/setup-status` (never echoes the setup token), `GET /config/public`, `GET /ai-config/provider-presets`, `GET /notifications/vapid-public-key`, `GET /fhir/R4/metadata`, `/.well-known/smart-configuration`, `/health`; `/docs`·`/redoc`·`/openapi.json` only in dev/test or `ENABLE_API_DOCS=true`. Everything else requires a `session` JWT via the single verification path (`authenticate_session_token`: signature/kind under the session key family → instance state → Redis jti store → live user row incl. `ver`); the domain router additionally blocks `api`-kind tokens (`require_session_token`), and the facade rejects session tokens (`get_api_principal`). There is no blanket 401 middleware — the session principal is still resolved per request by the single verification path, but authorization is centrally enforced: the app-level route gate (`app.core.authz.enforce`) checks every session route's permission + required scope from the central resolver (`app/core/authz/route_policy.py`) before the route runs — a new route inherits the declared policy of its path family, and a route that resolves to no policy fails closed with 403; per-endpoint `get_current_user` / `RoleChecker` / `require_admin` + ownership helpers remain as the principal source and defense-in-depth pins. §7: bcrypt (72-byte-fit, dummy-hash verify when the email is unknown), passwords ≥10 chars at register/setup, lockout 5 consecutive failures ⇒ 423 for 15 min (`HA_AUTH_LOCKOUT_*`), generic `Invalid email or password`, 401/403/404/423/429 semantics per contract, `WWW-Authenticate` on Bearer 401s. |
| Instance mode | `auth_mode`/`demo_mode` are rows in `instance_settings` — **DB-authoritative, per-request** reads (`instance_state.get_state`), unknown/unreadable values fail closed to `authenticated`/`demo_mode=false` (DB outage included). `HA_AUTH_MODE`/`HA_DEMO_MODE` are consumed exactly once when initializing an empty DB (boot `instance_state.initialize()` or `/auth/setup`); post-init env flips are ignored with a loud warning, and `HA_AUTH_MODE=open` is refused outright — §4.4, health never runs open. **§4.5 runtime transitions are NOT implemented** (no `PATCH /admin/instance`; no runtime API touches either key — see known gaps): a server product's mode is fixed at init, which we document as the actual state rather than the contract's transition surface. Demo guard: `HA_DEMO_MODE=true` under `APP_ENV=production` refuses to boot unless `DEMO_MODE_ACCEPT_UNAUTHENTICATED=true` is set explicitly. A copied/restored DB keeps its mode, users, tenants, and audit trail; the signing keys (`HA_SESSION_KEY`/`HA_REFRESH_KEY`) are never in the DB, so a bare DB copy cannot mint tokens — but it does carry `auth_sessions` rows and sealed secrets (see secrets at rest). Demo tokens (`auth_mode=demo`) are re-checked against the live DB fact on every request **and** every refresh rotation (S-7), so a demo family dies the moment the instance stops being a demo. |
| Session storage | §10 cookie triple set by login/setup/demo-login/refresh/tenant-switch: `nx_access` (`__Host-nx_access` under `HA_COOKIE_SECURE=true`; HttpOnly, SameSite=Lax, Secure, Path `/`), `nx_refresh` (HttpOnly, Path `/api/v1/auth` — cannot take the `__Host-` prefix, narrower path), `nx_csrf` (JS-readable, rotates with every issuance). CSRF: pure-ASGI double-submit middleware — non-safe `/api/*` requests carrying any session/CSRF cookie must echo `nx_csrf` in `X-CSRF-Token` (constant-time compare, 403 on mismatch); Bearer requests and the auth bootstrap prefixes (login/refresh/register/setup/demo-login/mfa, health/docs) are exempt; **logout is deliberately CSRF-gated**. localStorage/sessionStorage tokens are forbidden and actively scrubbed (`frontend/src/api/axios.ts` removes legacy keys); login/refresh responses still carry tokens in the JSON body for §9 user clients (the Android app). Refresh: 7-day rolling / 30-day absolute, one `auth_sessions` row per device ("family": `refresh_jti_hash`, `expires_at`, `absolute_expires_at`, `revoked_at`, `client_label` device hint); rotated on every use — replay of a rotated jti revokes the whole family + bumps `token_version` ⇒ **423**; the user row is re-loaded every refresh (claims rebuilt from the DB, not the old token). Redis jti store is the hot revocation layer (logout kills access tokens immediately; degrades open on Redis outage — the DB family is authoritative). Revocation paths: `POST /auth/logout` (family + access jti), `POST /auth/logout-all`, `DELETE /me/sessions/{id}`, admin user deletion (`token_store.revoke_everything`), refresh reuse, and access-TTL expiry (60 min default, 24 h cap). Sessions list: `GET /me/sessions`. |
| Trust boundaries | **Browser SPA ↔ backend:** §10 cookies + CSRF; deny-by-default CORS (`FRONTEND_URL` in prod, LAN regex in dev); `TrustedHostMiddleware` pins the Host header against redirect-uri poisoning; security headers (`nosniff`, `X-Frame-Options: DENY`, Referrer-Policy, HSTS) on every response; WS handshakes authenticate from the `nx_access` cookie (or `Sec-WebSocket-Protocol: ["bearer", token]` for §9 clients — the `?token=` query fallback is removed) **and** verify `Origin` against `HA_WS_ALLOWED_ORIGINS`/APP_URL/FRONTEND_URL/same-host (1008 otherwise); `/api/v1/ws/tasks` subscribes by tenant, `/api/v1/ws/notifications` by user id — server-side derived, never client-chosen. **User ↔ agent/AI:** model output is untrusted input — pydantic schema validation on every provider response, allowlisted tools, prompt-injection guard (`app/utils/prompt_guard.py`, high-risk patterns block by default), OCR/extraction results land as proposals/validated records, and BYOK provider keys are Fernet-sealed at rest and never echoed (masked on read). **Service class:** external systems use OAuth2 client-credentials (`POST /oauth/token`, client secret + registrable SMART scopes) and may only touch the FHIR facade (`aud`-checked `api` tokens, `require_fhir_scopes("read"/"write")` per route, `patient/`-scoped clients bound to one patient) — `api` tokens are 401'd on every domain route, session tokens are 401'd on the facade. **Machine/device class (the headline, identity-auth §9 third row):** the integrations HMAC bridge — `ANY /api/v1/integrations/{domain}/api/{integration_id}/{path}` (GET/POST/PUT/DELETE) and `POST /{domain}/webhook/{integration_id}`. Credential: a per-instance `api_secret`/`webhook_secret` (256-bit `token_urlsafe(32)`), **provisioned automatically at instance creation, shown exactly once**, stored Fernet-sealed inside `user_integrations.user_config` as `{"_encrypted", "_kid"}` with the instance id as context binding (cut-and-paste between rows fails), rotatable by the owner (`POST /integrations/instance/{id}/rotate-secret` — new secret returns once, old one dies instantly). Request auth: `X-Api-Signature` = HMAC-SHA256 over `METHOD\n<path>[?query]\n<timestamp>\n<raw_body>` (both leading-slash path forms accepted), constant-time compare, **`X-Api-Timestamp` mandatory**, ±5-min skew (300 s) folded into the MAC — no unlimited replays; the MAC covers the query string; bodies read once under a 40 MB cap; per-IP **and** per-integration rate limits. Fail-closed: no secret configured ⇒ 401 (the integration UUID is an identifier, never a credential — the pre-audit "UUID-only" mode is gone; provisioning without a Fernet key yields an instance the machine routes refuse). `GET /status` unsigned stays the QR-pairing connectivity probe but returns only `{status, server_time}`. Pairing: the UI's "Show pairing code" rotates the secret and renders a QR carrying `base|instance|secret` (three-segment form the Android app parses) — no user token ever reaches the device. Blast radius: the `user_integrations` row is bound to exactly one patient (`patient_id`) and one owner (`user_id`); the provider resolves its actor via `resolve_integration_actor` — the **owner's** identity with the role **re-fetched from the DB on every call**, so every bridge write goes through the same service layer, `check_patient_access` gate, tenant scoping, and audit provenance as an interactive request (integration-driven writes are auditable to a real person; no service account exists). Webhooks: same secret discipline — HMAC over the raw body (timestamped canonical form preferred; bare body-MAC + Redis replay guard accepted), no secret ⇒ 401. **Doc drift:** `integrations/health_assistant_bridge/docs/authentication.md` still sells "Mode 1 — UUID-only (default)" and "leave the secret blank to revert" — the platform rejects both; see known gaps. |
| Data isolation | Multi-tenant by construction: `tenants` + `TenantMixin.tenant_id` on every domain row (FK `ON DELETE CASCADE`; hypertable tables drop the FK and rely on app-level cleanup), and **every query filters `tenant_id`** — the cross-tenant case is a hidden **404** (`check_patient_access` and friends select by id AND tenant; existence never leaks). **Second wall (Phase 11):** every tenant-scoped table also carries Postgres row-level security reading transaction-local context GUCs planted by the engine from the verified principal (`app.tenant_id`/`app.system`, fail-closed; see "Row-level security" above) — a bug or an injected query that drops the `tenant_id` filter still cannot read another tenant's rows; documented exceptions (Timescale hypertable/caggs, global tables) and their mitigations live in the same section. Roles: `SYSTEM_ADMIN`/`ADMIN`/`MANAGER`/`USER` (`RoleChecker` — SYSTEM_ADMIN passes every gate); `USER` is additionally restricted to rows reachable through `fhir_patients.user_id` ownership (`check_*_access` raise 404 cross-tenant, 403 for an in-tenant but unowned patient). Invite-only registration: single-use jti-consumed invite JWTs bound to tenant (+optional email), `SYSTEM_ADMIN` ungrantable by invite (refused at mint **and** at verify — bootstrap is the only grantor). Tenant switching: `POST /admin/tenants/{id}/switch` mints a scoped token (`role=SYSTEM_ADMIN`, `tenant_id`=target, `original_tenant_id` preserved; no nested switches; target re-validated on refresh; the **demo principal is refused** — H7). FHIR facade: SMART scopes (`patient/*.rs` etc.) enforced per route, `patient/`-compartment clients bound to one patient; api-vs-session kind separation both directions (see trust boundaries). There is **no profile layer** in health (Class S extension): the care context is patient ownership, not `X-Profile-Id`. |
| Secrets at rest | Per-purpose key families (identity-auth §8): `HA_SESSION_KEY` signs all session-family JWTs (session/api/invite/download/mfa_challenge), `HA_REFRESH_KEY` signs refresh only, `HA_DATA_KEY` (legacy alias `INTEGRATION_SECRET_KEY`) is the Fernet at-rest key and **never signs**; no key derived from another; verification is family-locked so cross-family tokens fail. Server boot guards: non-dev `APP_ENV` refuses to boot without strong env-pinned `HA_SESSION_KEY`/`HA_REFRESH_KEY` (≥32 chars, non-placeholder, non-trivial), partial pins (exactly one of the two) fail closed in **every** environment, weak database credentials are refused outside dev (compose additionally makes `POSTGRES`/`REDIS`/`FLOWER` passwords required). Rotation ring: `HA_DATA_KEY_PREVIOUS` (alias `INTEGRATION_SECRET_KEY_PREVIOUS`, comma-separated) decrypts-only so rotated ciphertext keeps working; `_kid` tags identify values awaiting re-sealing. Sealed at rest under the DATA family with row-context binding: integration `api_secret`/`webhook_secret` (instance-id context), `ai_providers.api_key` (`enc::` prefix, masked on read), TOTP `mfa_secret_enc`. OS keyring: N/A — server product; the family's keyring rule is replaced by env+boot-guard per §8. **Disk-thief story:** the DB alone gets bcrypt password hashes, sha256 refresh-jti hashes, and ciphertext it cannot open (no `HA_DATA_KEY`); the DB **plus** the env/`.env` (all three keys) additionally gets token forgery for any live session (until `ver`/rotation) and every sealed integration/AI secret; the `uploads/` volume holds **unencrypted original documents** (PDFs/images — the raw PHI, protect the volume and backups together); Redis holds only jti/rate-limit/pub-sub state, not secrets. |
| Audit | `audit_events` (actor `user_id`, `action`, `resource_type`/`resource_id`, `tenant_id`, `outcome` ok/denied/error, JSONB `old_value`/`new_value` diffs, timestamp) written through the single chokepoint `log_audit_action` — by default on its own short-lived session over a **dedicated audit pool** (`AUDIT_DB_POOL_SIZE`, default 4; the row commits even if the caller rolls back), or — for the same-transaction shape below — appended to the caller's session, **hash-chained per tenant** (`chain_key`/`seq`/`prev_hash`/`entry_hash`; appends serialize on the chain's head row in `audit_chain_heads`; genesis prev_hash = 64 zeros; the canonical hash covers the immutable fields only so sanctioned erasure/retention anonymization never false-alarms), and **fail-closed**: writes/admin/grants/auth events and PHI-read outcomes pass `required=True` — a failed audit write raises `AuditWriteError` and the action answers 500 instead of succeeding unrecorded; the security-critical admin/policy mutations (tenant security policy, user role/active changes and admin password resets — the member-MFA force, invite minting, force-logout and admin-initiated deletion keep the default fail-closed lane) additionally write their audit row **in the caller's transaction** (`session=`), so the action and its audit row commit or roll back together, while reads keep the dedicated fail-closed lane. The only best-effort lane is the post-rollback denial/error record inside `@audit_read` (the original status must propagate); its failures alarm at CRITICAL (`audit_alarm`), never silently. Diff payloads are **redacted at write time** (PHI map: identity/name shapes, birth dates, contact/address fields, mrn/ssn, free-text clinical fields, filenames — ids, statuses, timestamps and HTTP codes pass through), so the stored window is PHI-free; erasure and the 3650-day audit retention still null the diffs on top. Covered: sensitive clinical **reads** via `@audit_read` (Patient, Observation, Document, Examination, Medication, Allergy, Immunization/Vaccine, ClinicalEvent — denials incl. hidden-404s recorded with outcome `denied`); all clinical **writes** (create/update/delete + layout/import/sync paths with redacted before/after diffs); every **admin action** (tenant CRUD/switch, tenant-user role/active changes, MFA forcing, invites, user deletion, catalog imports, broadcasts); **auth events** including denials (login ok/invalid/locked/disabled, demo-login, MFA challenge/verify, refresh-reuse `auth.refresh_reuse`, logout, register, setup); and **authz-gate denials** (`authz_denied` — the central route gate owns its 403s, request-scoped dedupe, required-fail-closed with a system-chain fallback when the caller's tenant row is gone). **Tamper-evidence:** `verify_audit_chain` walks each chain (sequence contiguity, `prev_hash` linkage, canonical `entry_hash` recomputation, head row) — a gap or mismatch is a tamper alarm (`AUDIT_CHAIN_ALARM` critical; there is no resync marker) and `GET /admin/audit/verify` (SYSTEM_ADMIN; `?tenant_id=` per tenant, `?all=true` for every chain) returns the per-chain status for operators. Readers: `GET /admin/audit` (SYSTEM_ADMIN — full cross-tenant stream incl. system-level rows; a tenant ADMIN sees the same surface scoped to their own tenant, a foreign `tenant_id` a hidden 404 and `all=true` a refused 403) and `GET /admin/tenants/{id}/audit` (SYSTEM_ADMIN, per-tenant view). Erasure: subject erasure keeps audit rows as proof-of-existence but redacts their JSONB diff (`old_value`/`new_value` → NULL) — the chain stays verifiable (only unhashed payload fields change); the erasure ledger (`erasure_requests`) records what was erased and replays on restore. Tenant hard-delete (`DELETE /admin/tenants/{id}` + confirm name) runs the tenant walker (all tenant rows, hypertable rows, files, checkpoints) and leaves the system-level `tenant.delete` audit tombstone with `tenant_id=NULL`; the deleted tenant's chain rows and head row cascade away together (a dead tenant's chain vanishes whole, not as a gap alarm). |
| Admin surface | Bootstrap: the **first SYSTEM_ADMIN** is created by the first-run wizard `POST /auth/setup` (or the `create_system_admin.py` CLI) — one-time setup token per `SETUP_TOKEN_MODE` (`log` = minted + logged once; `env` = launcher-injected; `time` = grace window then token; `disabled` = firewalled deployments only), advisory-lock race guard, 410 once initialized, token cleared after. There is **no open registration path to admin**: `/auth/register` requires an invite that can grant at most `ADMIN`; SYSTEM_ADMIN is refused at invite mint and verify (defense in depth). Privilege changes: `PATCH /admin/tenants/{tid}/users/{uid}` (role ∈ USER/MANAGER/ADMIN, `is_active`) — SYSTEM_ADMIN can neither be granted nor demoted from this surface; `PATCH .../users/{uid}/mfa` forces TOTP MFA (the member enrolls at next login; enforced accounts cannot self-disable MFA without the password). ADMIN/MANAGER are tenant-scoped (own tenant only); SYSTEM_ADMIN is cross-tenant and can switch into tenants with scoped tokens (demo principal refused). Blast radius of SYSTEM_ADMIN: read the full cross-tenant audit stream, manage tenants/users/invites/integrations, import catalogs, broadcast notifications; of ADMIN: everything within the tenant incl. demoting/deactivating members and forcing MFA (audit reads are tenant-scoped — an ADMIN sees and exports only its own tenant's stream; the cross-tenant view incl. the system chain is SYSTEM_ADMIN-only). Guard rails **not** implemented: no last-ADMIN protection (an ADMIN can demote/deactivate the tenant's last admin — recovery requires SYSTEM_ADMIN) and no self-action blocks; role changes **do** bump `token_version` and revoke outstanding tokens immediately (the changed member re-logs-in; only a self role-change via `PUT /users/{id}` skips the bump, landing at next refresh). Admin user deletion revokes all tokens immediately. |
| Deployment exposure | Server product — no loopback mode; the backend binds `0.0.0.0:8000` inside the compose networks and is fronted by nginx. Stacks (`docker/`): `docker-compose.standalone.yml` (canonical single-host: app services + **bundled nginx**, TLS-ready, backup sidecar behind `--profile backup`) and `docker-compose.prod.yml` (no bundled nginx — BYO proxy); datastore is TimescaleDB `timescale/timescaledb:latest-pg16`, database `neuronection_health` (test: `neuronection_health_test`), with split roles — `neuronection_health_owner` (runs migrations) vs least-privilege `neuronection_health_app` (runtime DML; created by `init-roles.sh`, required env forces strong passwords). TLS: the default `nginx.conf` is **HTTP-only** (loopback/VPN/dev) — internet-facing deployments must mount `nginx-TLS.conf` (certbot webroot ACME, HSTS, TLSv1.2/1.3, 60 MB body cap) and set `HA_COOKIE_SECURE=true`; `HA_TRUSTED_PROXY_COUNT` must match the proxy chain or per-IP rate limits key on the proxy. Flower (Celery dashboard) runs behind nginx at `/flower/` with `--basic-auth` (`FLOWER_USER`/`FLOWER_PASSWORD`, required). Backups: `backup` sidecar takes scheduled `pg_dump -Fc` of `neuronection_health` + the uploads volume to the `backups/` bind mount (`BACKUP_INTERVAL_HOURS`/`BACKUP_KEEP`); archives are **unencrypted** — protect them like the database; the supported restore is the throwaway-target drill (`docker/README.md` → "Backup & restore": `scripts/restore.sh <archive> --target <new-db>` — the live DB is refused — verify, then cut over by re-pointing `.env`). API docs are disabled in production unless `ENABLE_API_DOCS=true` (the authenticated surface is not advertised). Demo: the public demo stack lives **outside this repo** (`../demo/docker-compose.demo.yml`): database `neuronection_health_demo`, `demo-net` is `internal: true` (**zero egress** — no OpenAI/FHIR/SMTP/MCP outbound), AI/OCR off, `APP_ENV=production` + `HA_DEMO_MODE=true` admitted only via the explicit `DEMO_MODE_ACCEPT_UNAUTHENTICATED=true` opt-in, seeded synthetic-only by `scripts/seed_demo.py` (refuses any target not named `*_demo` or not demo-flagged), reset daily by cron, never restored into production. |

## Data lifecycle: erasure, retention, backups

This section is the operator contract for data-subject erasure and bounded
retention (constitution rule 10).

**Guarantees**

- Every mapped table carries a lifecycle class (`record | operational |
  transient`) in `contracts/data-model.lifecycle.txt`, mirrored in
  `app/core/data_lifecycle.py`. Gate D6 fails any unclassified table.
- Erasure is one ordered cascade walker (`app/services/erasure.py`), never
  per-table ad-hoc deletes: children before parents, subject row last, one
  transaction. Dry-run returns the per-table impact report without deleting;
  the real run's counts are pinned equal to the dry run by tests.
- Covered end-to-end for `user`, `patient` and `tenant` subjects: FHIR and
  clinical rows, documents (files unlinked), the `telemetry_data` hypertable,
  notifications + Web Push/mobile registrations, LangGraph checkpoint
  threads, chat/proposals, export artifacts, `integration_credentials`, and
  the sealed `user_config` secret wrappers (`_oauth`, `api_secret`,
  `webhook_secret`).
- Audit proof-of-existence survives erasure: `audit_events` rows are kept but
  their JSONB diff is redacted; AI call rows keep token/cost history with the
  subject id nulled. The erasure ledger (`erasure_requests` `done` rows) is
  the durable record of what was erased.
- **Backups:** the ledger is replayed against every restore before traffic is
  served — `scripts/restore.sh` runs
  `backend/scripts/replay_erasure_ledger.py` against the restored throwaway
  target (before cutover can serve it), and
  `POST /api/v1/data-lifecycle/ledger/replay` (SYSTEM_ADMIN) re-runs it on a
  live instance — so a restored backup cannot resurrect an erased subject.
  Crypto-shred removes sealed ciphertext references; residual backup copies
  age out under the backup TTL.

**Operator knobs**

- `retention_policies` is data. A `SYSTEM_ADMIN` edits it through
  `GET/PATCH /api/v1/data-lifecycle/retention-policies` or directly in the
  DB; the daily `apply_retention_policies` job reads the rows. Action is
  `delete` or `anonymize`; scope is a lifecycle class or one table; a disabled
  row is an explicit "no pruning". `proposals` prunes terminal rows only and
  `audit_events` anonymizes its diff.
- Checkpoint pruning reads the `checkpoints` policy row (default 30 days).
  The old `AI_CHECKPOINT_RETENTION_DAYS` env var is retired — there is no
  env/table dual source.
- The daily `purge_orphaned_lifecycle_rows` job removes telemetry and push
  rows whose tenant/patient/user is gone (the hypertable has no FK).
- **Erasure SLA vs backup TTL:** a request erases the live database
  synchronously (dry-run and real run are one request each). Backups are kept
  by `BACKUP_KEEP` archives at `BACKUP_INTERVAL_HOURS` (defaults: 14 daily
  backups ≈ 14 days), **below the erasure SLA**: after one TTL window no
  backup copy contains the subject, and any restore inside the window is
  neutralised by the ledger replay. Operators with a shorter erasure SLA must
  lower `BACKUP_KEEP` (or delete archives) so the TTL never exceeds it.
- **Legal assumption (stated once):** erasing a patient's clinical record is
  permitted where the operator deploys. Where record-retention law applies,
  the operator sets the applicable retention/erasure action to `anonymize`
  instead of `delete` (the vocabulary carries both) or disables the delete
  row; the baseline implementation anonymizes audit diffs and keeps the
  record when the action is `anonymize`, so a jurisdiction that requires the
  clinical payload retained should not schedule a `delete` policy at all.

**Known limits**

- The erasure ledger stores the subject id and the impact report; the
  report's counts are metadata, not record content.
- `anonymize` currently has a payload adapter for audit diffs (`old_value`/
  `new_value`); other tables fall back to "no anonymizable payload columns"
  and are skipped (logged) rather than half-anonymized. Do not rely on
  `anonymize` for clinical-record retention until a payload adapter exists —
  disable the delete instead.
- Uploaded document files and export archives are plain files on disk: an
  erasure unlinks them, but filesystem snapshots taken before the erasure
  follow the same TTL rule as database backups.

Known gaps (documented, not papered over):

1. **Instance-mode transitions (§4.5) are not implemented.** There is no
   `PATCH /admin/instance` and no runtime API for `auth_mode`/`demo_mode`;
   both are init-only DB facts. For a server product this is deliberate
   (the modes that matter — authenticated, demo — are set at provisioning),
   but it means the contract's audited transition surface simply does not
   exist here yet.
2. **No blanket auth middleware / no route-scan guard.** Authentication is
   a per-endpoint dependency; an endpoint added without `get_current_user`
   ships public. The §19 review rule covers this, but there is no CI test
   enumerating routes to enforce it mechanically (a scan currently relies
   on review; the public set is exactly the one listed in the auth row).
3. **List-endpoint reads are not audited.** `@audit_read` covers
   single-record GETs; the list endpoints (`GET /patients`,
   `GET /observations`, `GET /documents`, …) and `/search` return clinical
   data without an audit row — a tenant member can enumerate without a
   trail beyond access logs.
4. **No self-serve password change, no admin password reset.** Account
   self-deletion **does** exist (`DELETE /api/v1/users/me` → the erasure
   walker + token revocation), but `PATCH /me/password` and an admin
   reset-password path do not; the only password paths are initial set
   (setup/register) and MFA-disable confirmation. A forgotten password
   requires direct DB intervention today.
5. **No admin force-logout / role-change token invalidation.** There is no
   `POST /admin/users/{id}/force-logout`, and `update_tenant_user` does
   not bump `token_version` — a demoted or deactivated user keeps their
   access token up to its 60-minute expiry (deactivation does 401 at the
   live-user check; a role *change* does not until refresh). User deletion
   does revoke everything immediately.
6. **No last-ADMIN / self-action guard rails.** An ADMIN can demote or
   deactivate the last ADMIN of their tenant (recovery needs a
   SYSTEM_ADMIN); nothing blocks an admin acting on their own row.
7. **Bridge doc drift (machine/device class).**
   `integrations/health_assistant_bridge/docs/authentication.md` still
   documents "Mode 1 — UUID-only (default)" and "leave the secret
   empty/blank to … revert to UUID-only mode". The platform is fail-closed:
   instances are provisioned with a mandatory secret, and requests without
   a valid signature are 401 — there is no UUID-only mode to revert to.
   (`client-setup.md` repeats the "omit for UUID-only mode" framing.)
   Doc fix pending; the code is the truth.
8. **Rate limiting degrades open.** If Redis is unreachable, auth rate
   limits are skipped (documented design: availability over defence in
   depth — lockout still holds via the DB).
9. **Unsigned `GET /status` pairing probe** leaks integration liveness +
   server time to anyone holding the instance UUID (deliberate QR-pairing
   affordance; returns nothing else).
10. **WS `/tasks` is tenant-scoped, not patient-scoped** — every tenant
    member sees task-progress metadata for the tenant (titles/status, not
    record bodies).
11. **OIDC delegation (§14) is not implemented** — the `oidc_issuer`/
    `oidc_subject` columns exist but no login flow does; optional per the
    contract.

## AI-specific notes

The AI trust boundary is documented in the repository: model output is
untrusted input. Provider responses are schema-validated before use, tools
are allowlisted, user prompts pass the injection guard (blocking on
high-risk patterns by default), and extraction/mapping results are
validated (observation value contracts, biomarker definitions) before they
can persist. Reportable issues include any path where model output reaches
clinical storage, the tool surface, or exports without passing those
gates. BYOK provider keys are Fernet-sealed at rest
(`ai_providers.api_key`) and are read only at the moment of an outbound
call; they are masked in every response. See
[docs/AI_SYSTEM.md](docs/AI_SYSTEM.md) and [docs/API.md](docs/API.md).

## Supported versions

The latest `main` and the most recent release tag receive security fixes.
