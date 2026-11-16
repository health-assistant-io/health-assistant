# Tenancy & User Management

Health Assistant is a self-hosted, open-source health records platform with a multi-tenant architecture. This document explains how the system handles tenant isolation, organizational hierarchies, roles, and identity linking — from a single-user installation to a multi-department clinic.

## 1. The Data Model

Health Assistant separates **isolation boundaries** (Tenant), **organizational structure** (Organization, recursive), and **clinical identities** (Patient, Doctor) into distinct peer entities rather than a strict containment hierarchy.

### Tenant — The Isolation Boundary
The **Tenant** is the absolute boundary for data isolation.
- **Single-user / small group**: A Tenant represents one installation.
- **Clinic use**: A Tenant represents a medical group or a SaaS customer.
- **Isolation**: Users in Tenant A can *never* see data in Tenant B.

### Organization — Recursive Grouping
An **Organization** represents a physical or logical grouping within a Tenant. Organizations are **recursive** via a self-referential `part_of_id` foreign key (FHIR `Organization.partOf`), so a tree of arbitrary depth can be modeled — there is no fixed number of tiers.
- **Default**: On first registration, the system automatically creates a "Default Household" organization (type `HOUSEHOLD`). This serves as the primary container for the people in the tenant. The name is a default — it can be renamed, and `HOUSEHOLD` is just one of six org types.
- **Clinic/Hospital**: Represents a specific branch or facility.
- **Department**: A Department is simply an Organization whose `part_of_id` points to a parent Organization (typically of type `CLINIC` or `HOSPITAL`). It is not a separate model or table.
- **Org Types**: `HOUSEHOLD`, `CLINIC`, `HOSPITAL`, `DEPARTMENT`, `PROVIDER_GROUP`, `OTHER`.

### Clinical Identities — Peer Entities, Not Contained
Users, Patients, and Doctors are **tenant-scoped peers**; they are not nested inside Organizations or Departments:
- A **User** (login account) belongs to exactly one Tenant and has *no* direct link to any Organization or Department.
- A **Patient** belongs to a Tenant and may optionally link to one User via `user_id`.
- A **Doctor** belongs to a Tenant, may optionally link to one User via `user_id`, and is associated to Organizations (including Departments) many-to-many via the `organization_doctors` association table.
- **Examinations** carry an optional `organization_id`, so they can be grouped by Department (e.g., "Cardiology", "Dental").

### Cardinalities
| Relationship | Cardinality | Mechanism |
| :--- | :--- | :--- |
| User → Patient | 1 : N | `Patient.user_id` (soft, nullable, not unique) |
| User → Doctor | 1 : N | `Doctor.user_id` (soft, nullable, not unique) |
| Organization → child Organization | 1 : N | `Organization.part_of_id` (self-ref) |
| Department ↔ Doctor | N : N | `organization_doctors` association |
| Organization → Examination | 1 : N | `Examination.organization_id` |

---

## 2. Roles and Permissions

Health Assistant implements a dual-scope Role-Based Access Control (RBAC) system.

| Role | Scope | Description |
| :--- | :--- | :--- |
| **SYSTEM_ADMIN** | Global | The system owner. Can manage all tenants, global AI configurations, and system-wide settings. The first user registered in a new installation is automatically promoted to this role. |
| **ADMIN** | Tenant | The Tenant owner (e.g., the person who set up the installation, or a Clinic Manager). Can manage users and organizations within their own tenant. |
| **MANAGER** | Tenant | Administrative staff or senior caregivers. Can view all data in the tenant and manage clinical records. With the `manager_scope=assigned` tenant setting, a MANAGER's patient reach narrows to their own records plus care-team grants. |
| **USER** | Tenant | Standard user. Access is typically restricted to their own records unless granted wider permissions. |

---

## 2b. Care-Team Grants (sharing a patient record)

Beyond ownership (`Patient.user_id`) and role scope, access to a specific patient's record can be granted per-principal through **patient access grants**. A grant names exactly one grantee — a user account, or a doctor directory entry (which must itself be linked to a login) — and carries:

| Field | Values |
| :--- | :--- |
| `permission` | `read` < `write` < `admin` (ranked; a `read` grant never satisfies a write operation) |
| `source` | `admin` (admin surface) · `care_team` (patient Access tab) · `patient` (self-service share) · `guardian` (admin-granted, ownerless patients only). Break-glass is deliberately **not** a grant source — emergency elevations live in `access_elevations`. |
| `status` | `pending` → `active` → `revoked` / `expired`; `pending` → `declined` |
| `expires_at` | optional; an expired grant stops conferring access immediately, and a Celery beat sweep (every 15 minutes) flips its status to `expired` — audited, with the grantee and grantor notified |

Lifecycle rules:

- **Staff grants** (`admin` / `care_team`) are **active immediately**. Only staff (ADMIN/MANAGER) can hold or receive a care-team grant.
- **Patient-initiated shares** (`patient` source, by the record's owner) stay **pending** until the invited staff member accepts (or declines) them — gated by the tenant's `allow_patient_share` setting.
- **Guardian grants** are admin-only and require a patient with **no** owner account (the guardian logs in with their own user).
- A grantee identity (user or linked doctor) holds at most one **active** grant per patient — a second live grant is refused (409), and a revoked/expired grant's slot is reusable. Every transition (create / accept / decline / revoke / update / expiry) is audited, and the affected parties are notified. Accepting a share re-validates it: the invitee must still be an active staff member (403 after a demotion) and `allow_patient_share` must still be on (409 after a policy flip).
- **Export is never implied by a grant**: `export:patient` rides role scope/ownership only.

Grants reach **every** query surface — patient lists, examinations, global search, analytics per-patient paths, allergy alerts, documents (including presigned downloads), and task monitoring — so a granted clinician works the granted record exactly like their own.

### Tenant access settings

Two knobs live in the tenant's `settings` JSONB (read per request; a flip applies on the caller's next request, never token-cached):

| Key | Values (default first) | Effect |
| :--- | :--- | :--- |
| `manager_scope` | `tenant` (default) \| `assigned` | `assigned` narrows every MANAGER in the tenant to their own records + care-team grants, instead of the whole tenant. |
| `allow_patient_share` | `true` (default) \| `false` | `false` refuses patient-initiated shares (`source=patient`). |

Set them via the tenant admin surface (`PATCH /tenants/{id}` with a `settings` object; SYSTEM_ADMIN managing another tenant can use the richer `PATCH /admin/tenants/{id}`); unknown values fall back to the defaults on read.

### Tenant security policy

The security policy is the same `settings` JSONB, surfaced as a validated,
normalized API — `GET /tenants/{id}/security-policy` (staff read) and
`PATCH /tenants/{id}/security-policy` (ADMIN / SYSTEM_ADMIN; cross-tenant is
a hidden 404). Every change is audited (`tenant.security_policy`,
fail-closed) with a before/after diff of exactly the changed keys, and
enforcement reads the policy **per request** — nothing is cached into
tokens, so a flip applies mid-session. Unknown stored values fall back to
the defaults on read; invalid writes are 422.

| Key (settings) | Policy field | Default | Effect |
| :--- | :--- | :--- | :--- |
| `mfa_required_roles` | `mfa_required_roles` | `[]` | Roles (subset of `ADMIN`/`MANAGER`/`USER`) whose members must complete MFA — **TOTP or a passkey** — before a session is issued. `SYSTEM_ADMIN` is **always required** and can never be listed or waived. Unenrolled members ride the forced-enrollment challenge flow (`enrollment_needed: true` → `POST /auth/mfa/enroll` → `POST /auth/mfa/verify`). **SSO satisfies it too**: the callback accepts an IdP-asserted second factor (`amr`/`acr`) and otherwise answers the same challenge, whose `methods` list names the local factors (passkey and/or TOTP) the member can use. |
| `session_idle_minutes` | `session_idle_minutes` | unset | Per-session inactivity window (5–10080). Verification denies with `session_idle_expired` after the gap. |
| `session_max_hours` | `session_max_hours` | unset | Hard session-lifetime cap (1–8760). Per-request verification denies with `session_max_age_expired`; refresh revokes the family once the authoritative row is past the cap (the 30-day §8 family cap still applies when unset). |
| `concurrent_session_limit` | `concurrent_session_limit` | unset | Max live sign-in families per user (1–100). At issuance, families beyond the limit evict the **oldest-created** (ordering by family creation, not usage) — refresh chain revoked, bound bearer dropped — and the eviction is audited (`session.evict`). |
| `ip_allowlist` | `ip_allowlist` | `[]` | Optional CIDR allowlist for session issuance (`POST /auth/login`, the SSO callback and `POST /auth/refresh`). Outside it: `403 ip_not_allowed`, audited. Empty = unrestricted; proxy deployments must run `--proxy-headers` so the real client address is checked. |
| `allow_patient_share` | `patient_sharing` | `true` | `false` refuses patient-initiated shares (the grants service honors it per request). The policy-API field is `patient_sharing`; the stored key keeps the Phase-2 name. |
| `manager_scope` | `manager_scope` | `tenant` | Same knob as the table above, surfaced here so the whole policy is managed in one place. |

### Step-up re-authentication and session devices

Four actions need a fresh proof of presence on top of the live session, not
just a valid bearer: **role change** (`PUT /users/{id}` when the role
actually changes), **export** (`POST /export`), **break-glass**
(`POST /admin/access-elevations` and its `/approve`), and an **admin-level
patient grant** (`POST /patients/{id}/access`, plus `PATCH` raising a grant
to `admin`). Each answers `403 {"code": "step_up_required", "action": …}`
until `POST /auth/step-up` verifies the account's own factors — the local
password when set, TOTP/recovery when MFA is enrolled — and records a proof
keyed `stepup:{fid}:{action}`: bound to the sign-in family, to that one
action and to a 10-minute window. A registered passkey proves the same
actions through `POST /auth/step-up/passkeys/{options,verify}`
(`methods: ["webauthn"]`). An SSO-only account uses a fresh IdP-asserted
second factor instead (`methods: ["federated"]`). Normal reads never
require it; every proof and refusal is audited (`auth.step_up`).

### Passkeys (WebAuthn)

Members can register passkeys (Touch ID/Windows Hello or a security key)
from Settings → Security: `POST /me/mfa/passkeys/options` → the browser's
`navigator.credentials.create` → `POST /me/mfa/passkeys/verify`, and
`GET`/`DELETE /me/mfa/passkeys` list and remove them. Registration and
deletion require a fresh `mfa_change` step-up proof (password/TOTP or an
existing passkey), so a stolen live session cannot enroll a new
authenticator or strip the account's factors. A passkey satisfies the
tenant MFA policy and signs in with
`POST /auth/passkeys/{options,verify}` — user verification is required,
challenges are single-use and five-minute-lived, and a signature-counter
regression is a clone alarm that disables the credential and audits
`auth.passkey_clone_detected`. The last remaining factor cannot be deleted
while `mfa_enforced` or a policy-listed role requires MFA; TOTP recovery
codes remain the recovery path for TOTP, and a lost passkey is recovered
with another factor (or a fresh SSO sign-in) plus a replacement
registration. Instance configuration: `HA_WEBAUTHN_RP_ID` and
`HA_WEBAUTHN_ORIGIN` (https outside development/test; unconfigured
production answers `503 webauthn_not_configured`).

Session families are the device record. `auth_sessions` carries the
issuance `ip`/`user_agent` and a `last_seen_at` stamp (throttled to one
write per minute per family), and `GET /me/sessions` returns them so a user
can recognise — and revoke — a device. Three levels of sign-out:
`DELETE /me/sessions/{id}` (one device), `POST /me/sessions/revoke-others`
(every other device, the caller keeps working — no `token_version` bump),
and `POST /auth/logout-all` (everything, including the caller, via the
`token_version` bump). Concurrency, idle and max-age windows come from the
tenant policy (above). A sign-in from a device the account has never used
emits a `SECURITY_EVENT` notification (see `NOTIFICATION_SYSTEM.md`).

The admin audit surface (`GET /admin/audit`) is the audit viewer for both
roles: tenant admins are scoped to their own tenant (a foreign
`tenant_id` is a hidden 404, `?all=true` is refused); SYSTEM_ADMIN sees the
full stream plus the system chain, narrowable by `tenant_id`. Rows carry
the chain metadata (`chain_key`/`seq`/`prev_hash`/`entry_hash`), and
`GET /admin/audit/export?format=csv|json` streams the same filters with
chain metadata included — the export itself is audited.

---

## 2d. Row-Level Security (the second wall)

Tenancy is enforced **twice**. The application layer keeps its mandatory
`tenant_id` filters; beneath it, every tenant-scoped table carries Postgres
row-level security policies (migration `0016_rls`) reading
transaction-local settings that the database engine plants from the
verified principal at the start of every transaction:

| Setting | Meaning | Who sets it |
| :--- | :--- | :--- |
| `app.tenant_id` | The caller's tenant (UUID). Empty or missing means **no rows** — the policies fail closed. | The auth layer, from the verified session/api/presign token principal. |
| `app.system` | `on` only for the system actor: SYSTEM_ADMIN sessions, the auth bootstrap lookups (login email resolution, refresh, register, MFA verify, OAuth client lookup, machine-webhook integration lookup), Celery jobs at their task boundary, and boot-time database work (`app.core.boot` — `boot:<step>` scopes). Never derived from request data. | The auth layer / service code, explicitly, with restore-on-exit. |
| `app.patient_id` | The facade principal's patient binding. Reserved for patient-scoped policies (Phase 13); planted so the inventory is complete. | The facade dependency. |
| `app.actor` | Observability only (principal id / task name — visible in `pg_stat_activity`); never read by policies. | Same as above. |

Policy shapes per table class:

- **Tenant-owned tables** (NOT NULL `tenant_id`): rows are visible and
  writable only when `tenant_id` matches the context or the system flag is
  on. Cross-tenant INSERT/UPDATE/DELETE is refused by the database, and a
  row can never be repointed to another tenant.
- **Mixed tables** (nullable `tenant_id` — seeded catalogs, broadcasts,
  the global doctor directory): system (`NULL`-tenant) rows are readable
  by every tenant, but only the system actor can write them — a tenant
  can never mint or modify a system row. Audit-flavor tables
  (`audit_events`, `audit_chain_heads`, `catalog_audit_log`,
  `erasure_requests`, `task_logs`, `ai_call_logs`, `seed_sources`) hide
  their system rows from plain tenants, and the system flag reads on
  **every** row (not only NULL ones): the system actor — a SYSTEM_ADMIN
  session, switched or not — sees every tenant's rows plus the system
  chain, which is what the cross-tenant audit viewer/export, the chain
  verifier's `?tenant_id=<foreign>` walk, the erasure ledger and the
  AI-usage stats run on.
- **Tenant-derived children** (no `tenant_id` column — chat messages,
  clinical-event links/occurrences, seed-pack chains, `auth_sessions`): an
  `EXISTS` policy through the parent, whose own policy enforces inside the
  subquery, so multi-hop chains compose. The parent `EXISTS` materializes
  once (hashed SubPlan) with the GUC reads hoisted — never evaluated per
  row.

Named exceptions (documented in the migration and pinned by the RLS test
suite, which runs against the **runtime role**, never the migration owner):
the `telemetry_data` hypertable cannot carry RLS (Timescale refuses it on
compressed hypertables — the raw table stays granted for **writes,
erasure and retention**, whose service-layer tenant filters remain
mandatory), and every aggregate read goes through GUC-filtered scoped
views: the `telemetry_hourly/daily/monthly` continuous aggregates are
never RLS-coverable — the raw aggregates are revoked from the runtime
role, and the `telemetry_*_scoped` views (with the tenant filter inside
the view definition) are the only aggregate surface queries may touch,
while the trends live-aggregate path reads `telemetry_live_scoped` (the
same filter shape over the raw hypertable) instead of the raw table.
`alembic_version` is revoked outright; global vocabulary tables
(`tenants`, `units`, `ai_task_defs`, `instance_settings`, …) have no
tenant column and need no policy.

Deployment note: the runtime role is the least-privilege
`neuronection_health_app` (DML only, owns no table — the owner exemption
never applies to it); migrations run as `neuronection_health_owner`.
`FORCE ROW LEVEL SECURITY` is deliberately not set so owner-run data
migrations keep cross-tenant reach. **Rolling-deploy order is
one-directional: new code first, then migrate** (or stop-then-migrate).
A new process against a not-yet-migrated database is fine — the planted
GUCs are inert until policies exist. An old process against an
already-migrated database fails closed on everything (reads answer zero
rows, writes are refused), a short availability gap bounded by compose
recreating the app services after `migrate`. The
compose `migrate` service already gates every app service
(`service_completed_successfully`), so a normal upgrade keeps the order —
do not reorder it.

## 2e. Break-Glass Elevations

When an emergency legitimately needs wider reach than a role's standing
grants, staff use a **break-glass elevation** — a time-boxed,
two-person-approved, fully-audited window (table `access_elevations`):

1. **Request** (`POST /api/v1/admin/access-elevations`, permission
   `break_glass:request`): the ADMIN/MANAGER states a reason, the
   **subset of the permissions they already hold** that needs widening, a
   scope (`tenant`, or `system` — SYSTEM_ADMIN only) and a duration
   (1–240 minutes). The row starts `pending`; one live elevation per
   principal per scope.
2. **Second-person decision** (`POST .../{id}/approve` / `deny`): a
   **second ADMIN of the same tenant** decides tenant windows; a **second
   SYSTEM_ADMIN** decides system windows. Self-approval is refused (DB
   CHECK + service), approval opens the window immediately
   (`starts_at`/`expires_at` stamped at approval).
3. **The window** — the central authorization gate consults active
   elevations per request (one indexed query; the token never carries
   elevation state): the listed permissions widen to the elevation's scope
   (tenant elevations never reach system-scoped surfaces; system
   elevations belong to SYSTEM_ADMIN requesters only) until
   `expires_at`. Every request exercised under a window writes an
   `elevation_use` audit row.
4. **End of the window** — auto-expiry (15-minute beat sweep, plus an
   `expires_at > now()` predicate evaluated in SQL on every
   consultation), early `revoke` (the requester or an eligible approver;
   SYSTEM_ADMIN may contain any window), or expiry/denial — all audited
   (`elevation_request/approve/deny/revoke/expire`) and notified.

Elevations are **not grants**: care-team grants stay patient-scoped
(§2b); an elevation is principal-scoped, never carries a permission the
requester's role lacks, and never touches patient-level sharing.
**Single-admin tenants** have no elevation path — the only eligible
approver would be the requester; recovery is the host rescue runbook
(`SECURITY.md` §Break-glass), run by the person who controls the
deployment.

---

## 3. Identity Linking

A unique feature of Health Assistant is the separation and linking of **Identity** (who logs in) and **Clinical Records** (the data).

### Why Link?
Often, the person who manages the app (the User) is also a patient (the Patient record) or a caregiver (the Doctor record). In a clinic, a doctor's login maps to their professional Doctor profile. Linking allows the system to:
- Automatically show "My Dashboard" when you log in (when a single Patient is linked).
- Associate login accounts with professional licenses for doctors.

### How it Works — One-to-Many
Linking is **one-to-many**, not one-to-one:
- **`DoctorModel`** and **`Patient`** both contain an optional `user_id` foreign key. Neither column is unique-constrained, so a single User can be linked to **multiple** Patient records *and* **multiple** Doctor records simultaneously (e.g., someone who is both a patient and a caregiver for multiple people).
- When a `user_id` is present on a clinical record, that record is "owned" by the login account.
- **Home Setup**: Usually, you will create a Patient record for yourself and link it to your User account. A caregiver may additionally create Doctor records for themselves and link them.

### Unlinking:
- Links can be removed at any time from the **User Detail** page.
- Unlinking removes the connection but **preserves the clinical data**. The Patient or Doctor record remains in the database, just without an owner.

---

## 3b. Enterprise Identity — Per-Tenant SSO & SCIM

A tenant can bind its **own OpenID Connect provider** so members sign in
with their work identity instead of a local password. Configuration lives
in `tenant_sso_configs` (per `(tenant, issuer)`); the admin console manages
it with a **secret slot name** — the client secret itself lives only on the
host (OS keyring `health-assistant/tenant-sso`, or the
`HA_SSO_CLIENT_SECRET_<SLOT>` env fallback).

**The link law (D2: one identity = one tenant).** An external identity is
the `(issuer, subject)` pair, stored link-only in `user_identities` —
globally unique, bound to exactly one account in exactly one tenant.
`email_verified` is required; accounts are **never merged**:

- Existing link → sign in (`auth_mode="oidc"`; internal sessions are always
  minted by Health Assistant, never by the IdP).
- Verified email matches an existing local account without a link → typed
  **409 `sso_link_required`** + a one-time link intent: the user signs in
  with their password and confirms the link while authenticated.
- No local account and JIT enabled → provisioning, capped by the config's
  `allowed_domains` and the role map — `group_role_map` (first mapped
  group) else `default_role`, both restricted to USER/MANAGER/ADMIN.
  **SSO is never a path to SYSTEM_ADMIN.**

Group→role mapping re-runs on every login (a demotion lands when the
groups change), and every role change is audited (`sso_role_change`).

The callback applies the **same tenant gates** as the password login
(program 12C1): the IP allowlist (checked before provisioning, so a blocked
address leaves no JIT account) and the MFA requirement. The IdP's `amr`/
`acr` satisfies MFA only when it evidences a second factor — otherwise the
local single-use `mfa_challenge` is answered and completes at
`/auth/mfa/verify` with an `auth_mode="oidc"` session. `amr`/`auth_time`
travel onto the session (dropped at refresh) so an SSO-only account can
step up while its federated sign-in is fresh. A policy flip applies to the
next sign-in, never to a live session.

**SCIM 2.0** (`/scim/v2/Users|Groups`) lets a directory push the same
state: tenant-bounded bearer tokens (sha256 at rest, one-time plaintext at
creation, revocation is instant), Users and Groups CRUD with RFC 7644
pagination/filtering, deprovision = deactivate + `token_version` bump
(all live sessions die at once), and group membership moves re-rolling
member roles through the same `group_role_map` (audited). The local
password path and the break-glass runbook are untouched — an SSO outage
can only fail the SSO flow.

### Scoped personal access tokens (Program Phase 12C2B)

An account can mint **personal access tokens** (`api_tokens`, `pat` token
kind) for CLI/scripts/home-automation clients. They are tenant-bounded on
three independent levels and never cross a boundary:

- the row carries the owner's `tenant_id` and the owner FK (cascade on
  either erasure), and the pre-auth lookup resolves the owner **and**
  verifies the owner/tenant pairing — a mismatch resolves to nothing;
- the tenant RLS policy covers the table, and the resolved principal plants
  **only** the tenant GUC (a PAT never sets `app.system`, even for a
  SYSTEM_ADMIN owner);
- the route gate uses the token's permission subset (capped at tenant
  scope), re-intersected with the owner's live role on every request; the
  patient scope (`all|granted|none`) is intersected with the owner's live
  patient reach, so a care-team grant revoked in another tenant session
  narrows the token on its next call.

Token management is strictly self-service (`/me/api-tokens`): one owner,
one tenant, owner-only listing/update/rotation/revocation (another owner's
id is a hidden 404), and a PAT itself can never manage tokens.

### Scoped integration & extension principals (Program Phase 13)

Integrations and extensions are tenant-bounded **service identities**
(`integration_principals`), not owner sessions:

- every row carries the tenant, the accountable owner FK and (for a
  connector) its `user_integrations` row; tenant/owner deletion cascades,
  the integration FK is `SET NULL` so the ledger survives a connector
  deletion;
- the stored scope set is always a subset of the owner's live role and the
  extension manifest's declaration, re-intersected on every use — a role
  change, manifest change or consent revocation takes effect immediately;
- patient scope (`all|granted|none` + allowlist) is intersected with the
  owner's live reach, so a care-team grant revoked in another session
  narrows the principal on its next call;
- tenant admins (`integration:manage`/`tenant:manage`) manage every
  principal in the tenant; an integration owner manages the principal bound
  to their own integration; a scoped credential can manage neither. Another
  tenant's id is a hidden 404 and RLS is the second wall.

---

## 4. Smart Patient Context

Because one User may be linked to multiple Patient records (see §3), the system needs a way to decide which patient's data to display. Health Assistant uses a "Smart Context" engine:

1. **Automatic Context**: If a User is linked to *exactly one* Patient record, that patient is automatically selected as the "active context" upon login.
2. **Single-Patient Default**: In single-patient installations, that patient is selected by default.
3. **Secure Switching**: If a User is linked to two or more patients (or an Admin manages multiple patients), they must manually select the context. The system will never show data from a random patient.
4. **Session Cleanup**: Logging out explicitly wipes the patient context from the browser to prevent data residue for the next user.

---

## 5. Workflows

### Self-Service Setup (Zero-Config)
1. **Register**: The first user registers via `/auth/register` **without** supplying a `tenant_id`.
2. **Auto-Provision**: The system detects this is the first user and:
   - Assigns them the `SYSTEM_ADMIN` role.
   - Creates a new `Tenant` automatically.
   - Creates a "Default Household" `Organization` automatically.
3. **Ready**: The user can immediately start adding people as Patients.

Subsequent self-onboarding users (e.g. another user setting up their
own installation) follow the same path: they omit `tenant_id`, get their
own new tenant, and become `ADMIN` of it. Only the very first registration
in the entire database becomes `SYSTEM_ADMIN` — this check is
race-protected by a Postgres advisory lock so two concurrent bootstrap
registrations cannot both promote.

### Joining an Existing Tenant (Invite Token Required)

Anyone who knows a `tenant_id` cannot register inside that tenant — that
would be a tenant-impersonation hole. Joining an existing tenant requires
a short-lived **invite token** minted by that tenant's admin.

1. **Admin mints invite** — `POST /api/v1/auth/invite` (ADMIN, MANAGER,
   or SYSTEM_ADMIN only). The admin can optionally bind the token to a
   specific email, choose a role (USER/ADMIN/MANAGER), and set an expiry
   (default 7 days, **capped at 30**). Invites are **single-use** — the jti
   is consumed atomically on first registration.
   `SYSTEM_ADMIN` is **never** grantable via invite — or via
   `PUT /api/v1/users/{id}` / `POST /api/v1/users` by a tenant ADMIN/MANAGER
   (enforced server-side); only an existing SYSTEM_ADMIN (or first-run bootstrap)
   can create that role. Changing a user's role **revokes all their live
   tokens** so the new role takes effect immediately (they just re-login).
   ```bash
   curl -X POST https://your-host/api/v1/auth/invite \
        -H "Authorization: Bearer $ADMIN_JWT" \
        -H "Content-Type: application/x-www-form-urlencoded" \
         -d "email=newmember@example.com" -d "role=USER"
   # → { "invite_token": "...", "tenant_id": "...", "role": "USER", "expires_in_days": 7 }
   ```
2. **Invitee registers** — `POST /api/v1/auth/register` with both
   `tenant_id` and `invite_token`. The server verifies the token's
   signature, expiry, tenant binding, and (if set) email binding.
   ```bash
   curl -X POST https://your-host/api/v1/auth/register \
        -H "Content-Type: application/json" \
         -d '{"email":"newmember@example.com", "password":"...", '\
           '"tenant_id":"...", "invite_token":"..."}'
   ```
3. **Failure modes** (all return 403):
   - Missing `invite_token` field.
   - Token signature invalid (tampered) or expired.
   - Token's `tenant_id` ≠ request's `tenant_id`.
   - Token bound to a different email than the request.

### Clinical Setup
1. **Tenant Admin**: Registers via the bootstrap path (becomes `ADMIN` of
   their new tenant) OR is invited by a `SYSTEM_ADMIN` cross-tenant.
2. **Create Facilities & Departments**: The Admin creates Organizations of
   type `CLINIC` or `HOSPITAL`, then creates child Organizations of type
   `DEPARTMENT` with `part_of_id` pointing to the parent facility.
3. **Invite Staff**: Admin mints invite tokens (see above) for doctors and
   staff, who join as tenant-level Users. Note: Users themselves are not
   assigned to Departments — see step 4.
4. **Link Doctors & Assign to Departments**: Clinical `Doctor` records are
   created, linked to the staff's `User` accounts via `user_id`, and
   associated to one or more Departments via the `organization_doctors`
   association table (many-to-many).

---

## 6. CLI Administration

For system administrators, a command-line script is provided to manage the initial setup and promote users.

### Create or Promote System Admin
To create a new system administrator or promote an existing user:
```bash
python backend/scripts/create_system_admin.py --email admin@example.com --password "password"
```

**Arguments:**
- `--email`: The email address for the account.
- `--password`: The password (only used if creating a new user).
- `--tenant`: Optional name for the initial tenant if one needs to be created.

---

## 7. Data Deletion Semantics

Hard delete is the default (constitution rule 10). Deletion runs through one
ordered **erasure walker** — children before parents, the subject row last —
rather than scattered per-table deletes. The walker writes an
`erasure_requests` row (the **erasure ledger**), offers the subject's export
first, and stores the per-table impact report (`report` JSONB,
`report_schema_version`).

### Erasure requests (`/api/v1/data-lifecycle/erasures`)
- `subject_type` is `user | patient | tenant`; `dry_run` (the default) returns
  the impact report **without deleting**. Re-submit with `dry_run=false` to
  execute.
- RBAC: `USER` may erase themselves and their own patient; `ADMIN` may erase
  any user/patient in their tenant; `SYSTEM_ADMIN` may erase any tenant.
  Cross-tenant subjects are a hidden `404` (existence never leaks).
- Self-service account deletion is `DELETE /api/v1/users/me`; admin deletion
  is `DELETE /api/v1/users/{id}` and `DELETE /api/v1/patients/{id}`. All three
  call the same walker.
- The walker removes, in one transaction: hypertable rows
  (`telemetry_data`), checkpoint threads, notification/push rows, chat and
  proposals, documents (files unlinked first), clinical record tables,
  integration credentials (sealed `sealed_blob` rows **and** the sealed
  `user_config` `_oauth`/`api_secret`/`webhook_secret` wrappers), and finally
  the subject row. `audit_events` rows survive as proof-of-existence but their
  JSONB diff is redacted — and the per-tenant audit hash chain keeps verifying:
  the chain hash covers the immutable fields only, so the
  sanctioned redaction never false-alarms the tamper verifier.
- `done` rows are the ledger. A restore replays them before serving traffic
  (`scripts/restore.sh` runs `backend/scripts/replay_erasure_ledger.py`
  against the restored throwaway target, before cutover;
  `POST /api/v1/data-lifecycle/ledger/replay` re-runs it on
  a live instance), so a backup cannot resurrect an erased subject. `tenant_id` on the ledger is
  `ON DELETE SET NULL` because a tenant erasure must outlive its tenant.

### Tenant Deletion
Deleting a `Tenant` row (`DELETE /api/v1/admin/tenants/{id}`, typed-name
confirmation) now runs the tenant walker: every tenant-scoped table,
hypertable rows, push registrations, checkpoint threads and upload/export
files are removed, then the tenant row. The old manual-telemetry cleanup
caveat is gone — `telemetry_data`/`telemetry_anomalies` are deleted by
`tenant_id`, and the daily `purge_orphaned_lifecycle_rows` job sweeps any
orphan left by a restore.

### Patient / user Deletion
A patient erasure removes that entire clinical record (FHIR rows,
examinations, documents and their files, clinical events and links,
telemetry, notifications, chat, proposals, layouts, integration secrets).
A user erasure additionally removes the account, every patient it owns,
sessions, push registrations, export/import jobs and artifacts, and
anonymizes their audit/AI traces.

### Retention
Bounded retention is **data**, not code: `retention_policies` (scope = class
or table, action `delete | anonymize`, `days`, `timestamp_column`,
`enabled`) is seeded at boot and editable by a `SYSTEM_ADMIN` through
`GET/PATCH /api/v1/data-lifecycle/retention-policies`. The daily
`apply_retention_policies` job reads those rows; changing a row changes the
next cutoff. Registry-bounded vocabularies (`ai_task_defs`, `proposal_kinds`)
carry deliberately disabled rows.

### Soft-Delete (FHIR Facade)
Hard delete remains the rule. Exactly the declared tombstone set mixes in
`SoftDeleteMixin` — the FHIR-R4-exposed resources (`Patient`, `Observation`,
`DiagnosticReport`, `Medication`, `AllergyIntolerance`, `Organization`,
`Device`, `Communication`, `Examination`, `ClinicalEvent`, `Document`,
`Concept`, `PatientImmunization`). The FHIR facade sets `deleted_at = now()`
instead of hard-deleting; reads then return `410 Gone` for a deleted resource
so callers can distinguish "never existed" from "was deleted". The set is
pinned by `tests/test_schema_parameters.py::test_tombstone_set_is_exactly_the_declared_one`
and `tests/test_data_lifecycle.py`. A data-subject erasure is **not** a
facade delete: it hard-deletes the record and keeps the ledger row as the
proof instead.

