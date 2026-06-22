# Tenancy and User Management

Health Assistant is designed to be "Home-First, Clinic-Ready." This document explains how the system handles multiple users, organizational hierarchies, and identity linking.

## 1. The Hierarchy Model

Health Assistant uses a three-tier hierarchy to manage data isolation and clinical context:

### Tier 1: Tenant (The Installation/Group)
The **Tenant** is the absolute boundary for data isolation. 
- **Home Use**: A Tenant represents the entire family or a single installation.
- **Clinic Use**: A Tenant represents a Medical Group or a SaaS customer.
- **Isolation**: Users in Tenant A can *never* see data in Tenant B.

### Tier 2: Organization (Household or Facility)
An **Organization** represents a physical or logical grouping within a Tenant.
- **Household**: In home setups, the system automatically creates a "Default Household" organization. This serves as the primary container for family members.
- **Clinic/Hospital**: Represents a specific branch or facility.
- **Org Types**: `HOUSEHOLD`, `CLINIC`, `HOSPITAL`, `DEPARTMENT`, `PROVIDER_GROUP`.

### Tier 3: Departments (Clinical Units)
Organizations can be recursive. A **Department** is simply an Organization that is "part of" another Organization.
- **Usage**: Use departments to group examinations (e.g., "Cardiology", "Dental").
- **Clinical Tracking**: Doctors can be associated with multiple departments, allowing them to track their work across the facility.

---

## 2. Roles and Permissions

Health Assistant implements a dual-scope Role-Based Access Control (RBAC) system.

| Role | Scope | Description |
| :--- | :--- | :--- |
| **SYSTEM_ADMIN** | Global | The system owner. Can manage all tenants, global AI configurations, and system-wide settings. The first user registered in a new installation is automatically promoted to this role. |
| **ADMIN** | Tenant | The Tenant owner (e.g., Head of Household or Clinic Manager). Can manage users and organizations within their own tenant. |
| **MANAGER** | Tenant | Administrative staff or senior caregivers. Can view all data in the tenant and manage clinical records. |
| **USER** | Tenant | Standard user. Access is typically restricted to their own records unless granted wider permissions. |

---

## 3. Identity Linking

A unique feature of Health Assistant is the separation and linking of **Identity** (who logs in) and **Clinical Records** (the data).

### Why Link?
In a household, the person who manages the app (the User) is often also a patient (the Patient record) or a caregiver (the Doctor record). Linking allows the system to:
- Automatically show "My Dashboard" when you log in.
- Associate login accounts with professional licenses for doctors.

### How it Works:
- **`DoctorModel`** and **`Patient`** models both contain an optional `user_id`.
- When a `user_id` is present, the clinical profile is "owned" by that login account.
- **Home Setup**: Usually, you will create a Patient record for yourself and link it to your User account.

### Unlinking:
- Links can be removed at any time from the **User Detail** page.
- Unlinking removes the connection but **preserves the clinical data**. The Patient or Doctor record remains in the database, just without an owner.

---

## 4. Smart Patient Context

To ensure data security and a smooth user experience, Health Assistant uses a "Smart Context" engine:

1. **Automatic Context**: If a User is linked to exactly one Patient record, that patient is automatically selected as the "active context" upon login.
2. **Single-Patient Default**: In new home installations with only one patient, that patient is selected by default.
3. **Secure Switching**: If an Admin manages multiple patients, they must manually select the context. The system will never show data from a random patient.
4. **Session Cleanup**: Logging out explicitly wipes the patient context from the browser to prevent data residue for the next user.

---

## 5. Workflows

### Home Setup (Zero-Config)
1. **Register**: The first user registers via `/auth/register` **without** supplying a `tenant_id`.
2. **Auto-Provision**: The system detects this is the first user and:
   - Assigns them the `SYSTEM_ADMIN` role.
   - Creates a new `Tenant` automatically.
   - Creates a "Default Household" `Organization` automatically.
3. **Ready**: The user can immediately start adding family members as Patients.

Subsequent self-onboarding users (e.g. another household setting up their
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
   (default 7 days). `SYSTEM_ADMIN` is **never** grantable via invite —
   that role is bootstrap-only by design.
   ```bash
   curl -X POST https://your-host/api/v1/auth/invite \
        -H "Authorization: Bearer $ADMIN_JWT" \
        -d "email=newmember@family.com" -d "role=USER"
   # → { "invite_token": "...", "tenant_id": "...", "role": "USER", "expires_in_days": 7 }
   ```
2. **Invitee registers** — `POST /api/v1/auth/register` with both
   `tenant_id` and `invite_token`. The server verifies the token's
   signature, expiry, tenant binding, and (if set) email binding.
   ```bash
   curl -X POST https://your-host/api/v1/auth/register \
        -H "Content-Type: application/json" \
        -d '{"email":"newmember@family.com", "password":"...", '\
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
2. **Create Departments**: The Admin creates child Organizations of type `DEPARTMENT`.
3. **Invite Staff**: Admin mints invite tokens (see above) for doctors and
   staff, assigning them to the relevant Departments.
4. **Link Doctors**: Clinical `Doctor` records are created and linked to the staff's `User` accounts via `user_id`.

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

The schema enforces cascading deletes so data cannot be orphaned.

### Tenant Deletion
Deleting a `Tenant` row CASCADEs to **all tenant-owned tables** via
`tenant_id` foreign keys (`ON DELETE CASCADE`). This includes users,
patients, observations, medications, examinations, documents, alerts,
notifications, AI config, chat sessions, etc.

**Exception**: `telemetry_data` is a TimescaleDB hypertable where FK
constraints aren't reliably supported. The `tenant_id` column has no FK;
a periodic cleanup job is responsible for purging telemetry rows after
their tenant is deleted.

### Patient Deletion
Deleting a `Patient` CASCADEs to **their entire clinical record**:
medications, allergies, clinical events, examinations, documents,
devices, chat sessions, alerts, notifications, layouts, and
user integrations. No patient-owned row is orphaned.

### Soft-Delete (FHIR Facade)
The 9 FHIR-exposed models (`Patient`, `Observation`, `DiagnosticReport`,
`Medication`, `AllergyIntolerance`, `Organization`, `Examination`,
`ClinicalEvent`, `Document`) mix in `SoftDeleteMixin`. The FHIR facade
sets `deleted_at = now()` instead of hard-deleting; subsequent reads
return `410 Gone` (not `404 Not Found`) so callers can distinguish
"never existed" from "was deleted".
