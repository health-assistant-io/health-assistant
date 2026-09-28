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

Health Assistant is a **server product** (family identity class S,
ADR-0013): always authenticated, multi-tenant, self-hosted. This repo
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
- the datastore: PostgreSQL 16 (`neuro_health`, TimescaleDB image,
  `neuro_health_owner`/`neuro_health_app` roles), Redis (jti store + rate
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

## Threat model

The family identity & auth standard (identity-auth §20, distributed
internally with the family conventions) defines these surfaces; this
table is the single source of truth for health's answers. Everything
below describes the code as it ships on `main` — gaps are listed
separately, never papered over.

| Surface | Answers for this repo |
|---|---|
| Auth surface | Unauthenticated (each Redis rate-limited per IP, `HA_RATELIMIT_AUTH` + per-account email window `HA_RATELIMIT_AUTH_EMAIL`; IP from the rightmost `HA_TRUSTED_PROXY_COUNT` XFF hops — spoofable headers ignored at 0): `POST /api/v1/auth/login` (20/min), `/auth/refresh` (30/min; needs a valid `nx_refresh` cookie or Bearer body), `/auth/setup` (5/min; only while uninitialized — 410 after; one-time setup token required off-localhost, bootstrap serialized by `pg_advisory_xact_lock`), `/auth/register` (5/min; invite-token-gated **and** `HA_REGISTRATION_ENABLED`), `/auth/demo-login` (404 unless `instance_settings.demo_mode=true`), `/auth/mfa/verify` + `/auth/mfa/enroll` (answer the 5-min single-use `mfa_challenge` JWT), plus read-only public metadata: `GET /auth/setup-status` (never echoes the setup token), `GET /config/public`, `GET /ai-config/provider-presets`, `GET /notifications/vapid-public-key`, `GET /fhir/R4/metadata`, `/.well-known/smart-configuration`, `/health`; `/docs`·`/redoc`·`/openapi.json` only in dev/test or `ENABLE_API_DOCS=true`. Everything else requires a `session` JWT via the single verification path (`authenticate_session_token`: signature/kind under the session key family → instance state → Redis jti store → live user row incl. `ver`); the domain router additionally blocks `api`-kind tokens (`require_session_token`), and the facade rejects session tokens (`get_api_principal`). There is **no blanket 401 middleware** — auth is a per-endpoint dependency (`get_current_user` / `RoleChecker` / `require_admin`), so every new endpoint must state its authz story (§19; see known gaps). §7: bcrypt (72-byte-fit, dummy-hash verify when the email is unknown), passwords ≥10 chars at register/setup, lockout 5 consecutive failures ⇒ 423 for 15 min (`HA_AUTH_LOCKOUT_*`), generic `Invalid email or password`, 401/403/404/423/429 semantics per contract, `WWW-Authenticate` on Bearer 401s. |
| Instance mode | `auth_mode`/`demo_mode` are rows in `instance_settings` — **DB-authoritative, per-request** reads (`instance_state.get_state`), unknown/unreadable values fail closed to `authenticated`/`demo_mode=false` (DB outage included). `HA_AUTH_MODE`/`HA_DEMO_MODE` are consumed exactly once when initializing an empty DB (boot `instance_state.initialize()` or `/auth/setup`); post-init env flips are ignored with a loud warning, and `HA_AUTH_MODE=open` is refused outright — §4.4, health never runs open. **§4.5 runtime transitions are NOT implemented** (no `PATCH /admin/instance`; no runtime API touches either key — see known gaps): a server product's mode is fixed at init, which we document as the actual state rather than the contract's transition surface. Demo guard: `HA_DEMO_MODE=true` under `APP_ENV=production` refuses to boot unless `DEMO_MODE_ACCEPT_UNAUTHENTICATED=true` is set explicitly. A copied/restored DB keeps its mode, users, tenants, and audit trail; the signing keys (`HA_SESSION_KEY`/`HA_REFRESH_KEY`) are never in the DB, so a bare DB copy cannot mint tokens — but it does carry `auth_sessions` rows and sealed secrets (see secrets at rest). Demo tokens (`auth_mode=demo`) are re-checked against the live DB fact on every request **and** every refresh rotation (S-7), so a demo family dies the moment the instance stops being a demo. |
| Session storage | §10 cookie triple set by login/setup/demo-login/refresh/tenant-switch: `nx_access` (`__Host-nx_access` under `HA_COOKIE_SECURE=true`; HttpOnly, SameSite=Lax, Secure, Path `/`), `nx_refresh` (HttpOnly, Path `/api/v1/auth` — cannot take the `__Host-` prefix, narrower path), `nx_csrf` (JS-readable, rotates with every issuance). CSRF: pure-ASGI double-submit middleware — non-safe `/api/*` requests carrying any session/CSRF cookie must echo `nx_csrf` in `X-CSRF-Token` (constant-time compare, 403 on mismatch); Bearer requests and the auth bootstrap prefixes (login/refresh/register/setup/demo-login/mfa, health/docs) are exempt; **logout is deliberately CSRF-gated**. localStorage/sessionStorage tokens are forbidden and actively scrubbed (`frontend/src/api/axios.ts` removes legacy keys); login/refresh responses still carry tokens in the JSON body for §9 user clients (the Android app). Refresh: 7-day rolling / 30-day absolute, one `auth_sessions` row per device ("family": `refresh_jti_hash`, `expires_at`, `absolute_expires_at`, `revoked_at`, `client_label` device hint); rotated on every use — replay of a rotated jti revokes the whole family + bumps `token_version` ⇒ **423**; the user row is re-loaded every refresh (claims rebuilt from the DB, not the old token). Redis jti store is the hot revocation layer (logout kills access tokens immediately; degrades open on Redis outage — the DB family is authoritative). Revocation paths: `POST /auth/logout` (family + access jti), `POST /auth/logout-all`, `DELETE /me/sessions/{id}`, admin user deletion (`token_store.revoke_everything`), refresh reuse, and access-TTL expiry (60 min default, 24 h cap). Sessions list: `GET /me/sessions`. |
| Trust boundaries | **Browser SPA ↔ backend:** §10 cookies + CSRF; deny-by-default CORS (`FRONTEND_URL` in prod, LAN regex in dev); `TrustedHostMiddleware` pins the Host header against redirect-uri poisoning; security headers (`nosniff`, `X-Frame-Options: DENY`, Referrer-Policy, HSTS) on every response; WS handshakes authenticate from the `nx_access` cookie (or `Sec-WebSocket-Protocol: ["bearer", token]` for §9 clients — the `?token=` query fallback is removed) **and** verify `Origin` against `HA_WS_ALLOWED_ORIGINS`/APP_URL/FRONTEND_URL/same-host (1008 otherwise); `/api/v1/ws/tasks` subscribes by tenant, `/api/v1/ws/notifications` by user id — server-side derived, never client-chosen. **User ↔ agent/AI:** model output is untrusted input — pydantic schema validation on every provider response, allowlisted tools, prompt-injection guard (`app/utils/prompt_guard.py`, high-risk patterns block by default), OCR/extraction results land as proposals/validated records, and BYOK provider keys are Fernet-sealed at rest and never echoed (masked on read). **Service class:** external systems use OAuth2 client-credentials (`POST /oauth/token`, client secret + registrable SMART scopes) and may only touch the FHIR facade (`aud`-checked `api` tokens, `require_fhir_scopes("read"/"write")` per route, `patient/`-scoped clients bound to one patient) — `api` tokens are 401'd on every domain route, session tokens are 401'd on the facade. **Machine/device class (the headline, identity-auth §9 third row):** the integrations HMAC bridge — `ANY /api/v1/integrations/{domain}/api/{integration_id}/{path}` (GET/POST/PUT/DELETE) and `POST /{domain}/webhook/{integration_id}`. Credential: a per-instance `api_secret`/`webhook_secret` (256-bit `token_urlsafe(32)`), **provisioned automatically at instance creation, shown exactly once**, stored Fernet-sealed inside `user_integrations.user_config` as `{"_encrypted", "_kid"}` with the instance id as context binding (cut-and-paste between rows fails), rotatable by the owner (`POST /integrations/instance/{id}/rotate-secret` — new secret returns once, old one dies instantly). Request auth: `X-Api-Signature` = HMAC-SHA256 over `METHOD\n<path>[?query]\n<timestamp>\n<raw_body>` (both leading-slash path forms accepted), constant-time compare, **`X-Api-Timestamp` mandatory**, ±5-min skew (300 s) folded into the MAC — no unlimited replays; the MAC covers the query string; bodies read once under a 40 MB cap; per-IP **and** per-integration rate limits. Fail-closed: no secret configured ⇒ 401 (the integration UUID is an identifier, never a credential — the pre-audit "UUID-only" mode is gone; provisioning without a Fernet key yields an instance the machine routes refuse). `GET /status` unsigned stays the QR-pairing connectivity probe but returns only `{status, server_time}`. Pairing: the UI's "Show pairing code" rotates the secret and renders a QR carrying `base|instance|secret` (three-segment form the Android app parses) — no user token ever reaches the device. Blast radius: the `user_integrations` row is bound to exactly one patient (`patient_id`) and one owner (`user_id`); the provider resolves its actor via `resolve_integration_actor` — the **owner's** identity with the role **re-fetched from the DB on every call**, so every bridge write goes through the same service layer, `check_patient_access` gate, tenant scoping, and audit provenance as an interactive request (integration-driven writes are auditable to a real person; no service account exists). Webhooks: same secret discipline — HMAC over the raw body (timestamped canonical form preferred; bare body-MAC + Redis replay guard accepted), no secret ⇒ 401. **Doc drift:** `integrations/health_assistant_bridge/docs/authentication.md` still sells "Mode 1 — UUID-only (default)" and "leave the secret blank to revert" — the platform rejects both; see known gaps. |
| Data isolation | Multi-tenant by construction: `tenants` + `TenantMixin.tenant_id` on every domain row (FK `ON DELETE CASCADE`; hypertable tables drop the FK and rely on app-level cleanup), and **every query filters `tenant_id`** — the cross-tenant case is a hidden **404** (`check_patient_access` and friends select by id AND tenant; existence never leaks). Roles: `SYSTEM_ADMIN`/`ADMIN`/`MANAGER`/`USER` (`RoleChecker` — SYSTEM_ADMIN passes every gate); `USER` is additionally restricted to rows reachable through `fhir_patients.user_id` ownership (`check_*_access` raise 404 cross-tenant, 403 for an in-tenant but unowned patient). Invite-only registration: single-use jti-consumed invite JWTs bound to tenant (+optional email), `SYSTEM_ADMIN` ungrantable by invite (refused at mint **and** at verify — bootstrap is the only grantor). Tenant switching: `POST /admin/tenants/{id}/switch` mints a scoped token (`role=SYSTEM_ADMIN`, `tenant_id`=target, `original_tenant_id` preserved; no nested switches; target re-validated on refresh; the **demo principal is refused** — H7). FHIR facade: SMART scopes (`patient/*.rs` etc.) enforced per route, `patient/`-compartment clients bound to one patient; api-vs-session kind separation both directions (see trust boundaries). There is **no profile layer** in health (Class S extension): the care context is patient ownership, not `X-Profile-Id`. |
| Secrets at rest | Per-purpose key families (identity-auth §8, plan 16 H4): `HA_SESSION_KEY` signs all session-family JWTs (session/api/invite/download/mfa_challenge), `HA_REFRESH_KEY` signs refresh only, `HA_DATA_KEY` (legacy alias `INTEGRATION_SECRET_KEY`) is the Fernet at-rest key and **never signs**; no key derived from another; verification is family-locked so cross-family tokens fail. Server boot guards: non-dev `APP_ENV` refuses to boot without strong env-pinned `HA_SESSION_KEY`/`HA_REFRESH_KEY` (≥32 chars, non-placeholder, non-trivial), partial pins (exactly one of the two) fail closed in **every** environment, weak database credentials are refused outside dev (compose additionally makes `POSTGRES`/`REDIS`/`FLOWER` passwords required). Rotation ring: `HA_DATA_KEY_PREVIOUS` (alias `INTEGRATION_SECRET_KEY_PREVIOUS`, comma-separated) decrypts-only so rotated ciphertext keeps working; `_kid` tags identify values awaiting re-sealing. Sealed at rest under the DATA family with row-context binding: integration `api_secret`/`webhook_secret` (instance-id context), `ai_providers.api_key` (`enc::` prefix, masked on read), TOTP `mfa_secret_enc`. OS keyring: N/A — server product; the family's keyring rule is replaced by env+boot-guard per §8. **Disk-thief story:** the DB alone gets bcrypt password hashes, sha256 refresh-jti hashes, and ciphertext it cannot open (no `HA_DATA_KEY`); the DB **plus** the env/`.env` (all three keys) additionally gets token forgery for any live session (until `ver`/rotation) and every sealed integration/AI secret; the `uploads/` volume holds **unencrypted original documents** (PDFs/images — the raw PHI, protect the volume and backups together); Redis holds only jti/rate-limit/pub-sub state, not secrets. |
| Audit | `audit_events` (actor `user_id`, `action`, `resource_type`/`resource_id`, `tenant_id`, `outcome` ok/denied/error, JSONB `old_value`/`new_value` diffs, timestamp) written through the single best-effort chokepoint `log_audit_action` — own short-lived session (the row commits even if the caller rolls back), never raises (failures log WARNING with traceback). Covered: sensitive clinical **reads** via the `@audit_read` decorator on single-record GETs (Patient, Observation, Document, Examination, Medication, Allergy, Immunization/Vaccine, ClinicalEvent — denials incl. hidden-404s recorded with outcome `denied`); all clinical **writes** (create/update/delete + layout/import/sync paths with before/after diffs); every **admin action** (tenant CRUD/switch, tenant-user role/active changes, MFA forcing, invites, user deletion, catalog imports, broadcasts); and **auth events** including denials (login ok/invalid/locked/disabled, demo-login, MFA challenge/verify, refresh-reuse `auth.refresh_reuse`, logout, register, setup). Readers: `GET /admin/audit` (SYSTEM_ADMIN — full cross-tenant stream incl. system-level rows) and `GET /admin/tenants/{id}/audit` (SYSTEM_ADMIN, per-tenant view; tenant ADMINs have no audit reader). Erasure: no API edits or deletes audit rows — only direct DB access (or `pg_restore`/backup retention). One cascade caveat: tenant hard-delete (`DELETE /admin/tenants/{id}` + confirm name) purges that tenant's rows via `ON DELETE CASCADE`, leaving only the system-level `tenant.delete` tombstone written with `tenant_id=NULL`. |
| Admin surface | Bootstrap: the **first SYSTEM_ADMIN** is created by the first-run wizard `POST /auth/setup` (or the `create_system_admin.py` CLI) — one-time setup token per `SETUP_TOKEN_MODE` (`log` = minted + logged once; `env` = launcher-injected; `time` = grace window then token; `disabled` = firewalled deployments only), advisory-lock race guard, 410 once initialized, token cleared after. There is **no open registration path to admin**: `/auth/register` requires an invite that can grant at most `ADMIN`; SYSTEM_ADMIN is refused at invite mint and verify (defense in depth). Privilege changes: `PATCH /admin/tenants/{tid}/users/{uid}` (role ∈ USER/MANAGER/ADMIN, `is_active`) — SYSTEM_ADMIN can neither be granted nor demoted from this surface; `PATCH .../users/{uid}/mfa` forces TOTP MFA (the member enrolls at next login; enforced accounts cannot self-disable MFA without the password). ADMIN/MANAGER are tenant-scoped (own tenant only); SYSTEM_ADMIN is cross-tenant and can switch into tenants with scoped tokens (demo principal refused). Blast radius of SYSTEM_ADMIN: read the full cross-tenant audit stream, manage tenants/users/invites/integrations, import catalogs, broadcast notifications; of ADMIN: everything within the tenant incl. demoting/deactivating members and forcing MFA (audit reads are SYSTEM_ADMIN-only). Guard rails **not** implemented: no last-ADMIN protection (an ADMIN can demote/deactivate the tenant's last admin — recovery requires SYSTEM_ADMIN) and no self-action blocks; role changes do **not** bump `token_version` (new role takes effect at next refresh; access tokens ≤60 min). Admin user deletion revokes all tokens immediately. |
| Deployment exposure | Server product — no loopback mode; the backend binds `0.0.0.0:8000` inside the compose networks and is fronted by nginx. Stacks (`docker/`): `docker-compose.standalone.yml` (canonical single-host: app services + **bundled nginx**, TLS-ready, backup sidecar behind `--profile backup`) and `docker-compose.prod.yml` (no bundled nginx — BYO proxy); datastore is TimescaleDB `timescale/timescaledb:latest-pg16`, database `neuro_health` (test: `neuro_health_test`), with split roles — `neuro_health_owner` (runs migrations) vs least-privilege `neuro_health_app` (runtime DML; created by `init-roles.sh`, required env forces strong passwords). TLS: the default `nginx.conf` is **HTTP-only** (loopback/VPN/dev) — internet-facing deployments must mount `nginx-TLS.conf` (certbot webroot ACME, HSTS, TLSv1.2/1.3, 60 MB body cap) and set `HA_COOKIE_SECURE=true`; `HA_TRUSTED_PROXY_COUNT` must match the proxy chain or per-IP rate limits key on the proxy. Flower (Celery dashboard) runs behind nginx at `/flower/` with `--basic-auth` (`FLOWER_USER`/`FLOWER_PASSWORD`, required). Backups: `backup` sidecar takes scheduled `pg_dump -Fc` of `neuro_health` + the uploads volume to the `backups/` bind mount (`BACKUP_INTERVAL_HOURS`/`BACKUP_KEEP`); archives are **unencrypted** — protect them like the database; the documented restore drill (`docker/README.md` → "Backup & restore drill": stop stack, fresh volume, `scripts/restore.sh <archive> --yes`, verify login) is the supported path (in-place volume downgrade is refused). API docs are disabled in production unless `ENABLE_API_DOCS=true` (the authenticated surface is not advertised). Demo: the public demo stack lives **outside this repo** (`../demo/docker-compose.demo.yml`): database `neuro_health_demo`, `demo-net` is `internal: true` (**zero egress** — no OpenAI/FHIR/SMTP/MCP outbound), AI/OCR off, `APP_ENV=production` + `HA_DEMO_MODE=true` admitted only via the explicit `DEMO_MODE_ACCEPT_UNAUTHENTICATED=true` opt-in, seeded synthetic-only by `scripts/seed_demo.py` (refuses any target not named `*_demo` or not demo-flagged), reset daily by cron, never restored into production. |

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
4. **No self-serve password change, no admin password reset, no account
   self-deletion.** `PATCH /me/password`, admin reset-password, and
   `DELETE /me` from the §12 table do not exist; the only password paths
   are initial set (setup/register) and MFA-disable confirmation. A
   forgotten password requires direct DB intervention today.
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
