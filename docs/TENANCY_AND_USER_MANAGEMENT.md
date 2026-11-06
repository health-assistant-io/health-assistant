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
| **MANAGER** | Tenant | Administrative staff or senior caregivers. Can view all data in the tenant and manage clinical records. |
| **USER** | Tenant | Standard user. Access is typically restricted to their own records unless granted wider permissions. |

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
  JSONB diff is redacted.
- `done` rows are the ledger. A restore replays them before serving traffic
  (`scripts/restore.sh` runs `backend/scripts/replay_erasure_ledger.py` right
  after `pg_restore`; `POST /api/v1/data-lifecycle/ledger/replay` re-runs it on
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

