# Health Assistant - API Documentation

## Overview

Health Assistant provides RESTful APIs for interacting with the health data platform.

**Base URL**: `http://localhost:8000/api/v1`  
**API Docs**: `http://localhost:8000/docs`

## Authentication

All API endpoints (except `/auth/login`, `/auth/register`, and
`/auth/invite` — the latter requires a JWT but is the issuance mechanism
for the second one) require authentication.

### JWT Token

Include your JWT token in the `Authorization` header:

```
Authorization: Bearer <your-jwt-token>
```

### Tenant & patient scoping

Every authenticated request carries a `tenant_id` (from the JWT). All
list/read/write endpoints are **tenant-scoped** — a caller can only see
rows whose `tenant_id` matches their own. Cross-tenant calls return
`404` (not `403`) so the existence of a row in another tenant is not
leaked. The deliberate exception is the **`SYSTEM_ADMIN`** role, which
bypasses the tenant filter (operator visibility — see
[TENANCY_AND_USER_MANAGEMENT.md](TENANCY_AND_USER_MANAGEMENT.md)).

For patient-scoped endpoints (anything that takes a `patient_id`), a
`USER`-role caller must additionally be the patient's linked user
(`Patient.user_id == current_user.user_id`); `ADMIN` and `MANAGER` see
all patients in their tenant. The canonical check is
`check_patient_access` in `app/api/v1/endpoints/utils.py`.

### Login Endpoint

```http
POST /api/v1/auth/login
```

**Request Body:**
```
username=test@example.com
password=your-password
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 86400
}
```

### Refresh Token

```http
POST /api/v1/auth/refresh
```

**Request Body:**
```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 86400
}
```

### Register New User

```http
POST /api/v1/auth/register
```

**Request Body:**
- `email`: (Required) User email address
- `password`: (Required) Password (min 8 characters)
- `tenant_id`: (Optional) Tenant ID to join. **If provided, `invite_token` is required** (see [Joining an Existing Tenant](#joining-an-existing-tenant-invite-required)).
- `invite_token`: (Optional but **required** when `tenant_id` is provided) Tenant-scoped JWT issued by that tenant's administrator via [`POST /api/v1/auth/invite`](#issue-an-invite-token).

The register endpoint supports two onboarding paths:

#### Path 1 — Bootstrap (no `tenant_id`)

Used for the household self-onboarding UX. The server creates a new Tenant +
"Default Household" Organization for the user. The very first registration
in the entire database is promoted to **SYSTEM_ADMIN**; subsequent
bootstraps become **ADMIN** of their own new household.

The first-user check is race-protected via a Postgres advisory lock
(`pg_advisory_xact_lock`) acquired before `COUNT(users)` and held across
the insert, so two concurrent bootstraps cannot both promote.

```json
{
  "email": "user@example.com",
  "password": "securepassword123"
}
```

#### Path 2 — Joining an Existing Tenant (invite required)

When `tenant_id` is provided, the server verifies (in order):

1. The tenant exists (404 otherwise — no leak).
2. The `invite_token` is a valid `SECRET_KEY`-signed JWT scoped to that
   tenant (403 otherwise). The token may also bind to a specific `email`
   and encode a role (`USER` / `ADMIN` / `MANAGER`; **`SYSTEM_ADMIN` is
   never grantable via invite** — bootstrap is the only SYSTEM_ADMIN
   grantor; defense in depth: the issuer refuses to encode it and the
   verifier downgrades any hand-crafted `SYSTEM_ADMIN` claim to `USER`).

See [TENANCY_AND_USER_MANAGEMENT.md](TENANCY_AND_USER_MANAGEMENT.md) for
the full invite flow + curl examples.

```json
{
  "email": "newmember@family.com",
  "password": "securepassword123",
  "tenant_id": "123e4567-e89b-12d3-a456-426614174001",
  "invite_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response:** `200 OK`
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "email": "user@example.com",
  "role": "SYSTEM_ADMIN",
  "tenant_id": "123e4567-e89b-12d3-a456-426614174001"
}
```

### Issue an Invite Token

```http
POST /api/v1/auth/invite
```

**Role gating:** `ADMIN`, `MANAGER`, or `SYSTEM_ADMIN` only. A non-
`SYSTEM_ADMIN` caller can only mint invites for their own tenant.

**Query Parameters:**
- `tenant_id` (Optional) Target tenant. Defaults to the caller's tenant.
  `SYSTEM_ADMIN` can target any tenant; other roles get 403 if they
  name a different tenant.
- `email` (Optional) Bind the token to a specific invitee email. If set,
  the register endpoint will reject any request using this token with a
  different email.
- `role` (Optional, default `USER`) Role to grant. Cannot be
  `SYSTEM_ADMIN` — that role is bootstrap-only.
- `expires_days` (Optional, default `7`) Token TTL in days.

**Response:** `200 OK`
```json
{
  "invite_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "tenant_id": "123e4567-e89b-12d3-a456-426614174001",
  "role": "USER",
  "expires_in_days": 7
}
```

### Validate Token

```http
GET /api/v1/auth/validate
```

**Response:** `200 OK`
```json
{
  "valid": true,
  "user_id": "123e4567-e89b-12d3-a456-426614174000"
}
```

### Get Current User

```http
GET /api/v1/users/me
```

**Response:** `200 OK`
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "email": "user@example.com",
  "role": "user",
  "tenant_id": "123e4567-e89b-12d3-a456-426614174001"
}
```

## REST API

### Tenants

#### Create Tenant

```http
POST /api/v1/tenants
```

**Request Body:**
```json
{
  "name": "Acme Corporation",
  "settings": {
    "data_retention_days": 2555,
    "unit_system": "metric"
  }
}
```

**Response:** `201 Created`

#### Get Tenant

```http
GET /api/v1/tenants/{tenant_id}
```

**Response:** `200 OK`

#### Update Tenant

```http
PUT /api/v1/tenants/{tenant_id}
```

**Request Body:** (same as create)

#### Delete Tenant

```http
DELETE /api/v1/tenants/{tenant_id}
```

**Note:** Soft delete - sets `deleted_at` timestamp

### Users

#### Create User

```http
POST /api/v1/users
```

**Request Body:**
```json
{
  "email": "john.doe@example.com",
  "password": "SecurePass123!",
  "role": "user",
  "tenant_id": "123e4567-e89b-12d3-a456-426614174001"
}
```

**Response:** `201 Created`

#### Get Current User

```http
GET /api/v1/users/me
```

**Response:** `200 OK`

#### Update User

```http
PUT /api/v1/users/{user_id}
```

#### Delete User
```http
DELETE /api/v1/users/{user_id}
```

### User Administration

#### List Tenant Users
```http
GET /api/v1/users
```
Returns all users belonging to the current tenant. Admin/Manager roles only.

#### Create User
```http
POST /api/v1/users
```
**Request Body:**
```json
{
  "email": "newuser@example.com",
  "password": "temporarypassword123",
  "role": "USER",
  "tenant_id": "optional-uuid"
}
```
If `tenant_id` is omitted, the user is created in the administrator's current tenant.

### Documents

#### Upload Document

```http
POST /api/v1/documents
```

**Form Data:**
- `file`: PDF, JPEG, PNG, DOCX, or TXT file
- `patient_id`: Optional patient reference

**Response:** `202 Accepted`

#### Get Document

```http
GET /api/v1/documents/{document_id}
```

#### Download Document

```http
GET /api/v1/documents/{document_id}/download
```

**Response:** File download

#### Preview Document

```http
GET /api/v1/documents/{document_id}/preview?page=0
```

Returns a single-page JPEG/PNG preview of a document (DICOM frames or
PDF pages are converted on the fly). Multi-page docs expose total page
count via the `X-Total-Pages` response header; `page` is zero-indexed.

**Auth (one of):**
- A valid **presigned token** in the `?token=...` query parameter
  (short-lived JWT bound to this `document_id`; minted by an
  authenticated caller). This is the path used by `<img src="...">`
  tags in the frontend, which cannot send an `Authorization` header.
- A valid **`Authorization: Bearer <jwt>`** for the document's tenant.
  Used by JSON-fetch clients (the AI assistant, fetch-based viewers).
  `SYSTEM_ADMIN` Bearer bypasses the tenant check.

A request with **neither** credential returns `401`. A Bearer JWT for a
different tenant returns `404` (no information leak that the row exists
elsewhere).

#### Trigger Extraction

```http
POST /api/v1/documents/{document_id}/extract
```

**Response:** `202 Accepted`

#### Get Extraction Status

```http
GET /api/v1/documents/{document_id}/extract/status
```

**Response:**
```json
{
  "status": "completed",
  "progress": 100,
  "extracted_at": "2024-01-15T11:05:00Z"
}
```

### Patient & Observation endpoints

> Patient identity and biomarker readings are managed via dedicated domain
> endpoints that return ORM-shape JSON (snake_case + app fields like
> `biomarker_id`, `normalized_value`). These are the frontend's primary API
> surface. For **canonical FHIR R4 interop** (external systems, export/import,
> SMART-on-FHIR), see the `/api/v1/fhir/R4/*` facade at
> [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md).

#### Create Patient

```http
POST /api/v1/patients
```

**Request Body:** (ORM-shape patient dict)

#### List Patients

```http
GET /api/v1/patients
```

**Query Parameters:**
- `limit`: Number of results per page
- `offset`: Pagination offset
- `user_id`: Filter by linked user

#### Get Patient

```http
GET /api/v1/patients/{id}
```

#### Create Observation

```http
POST /api/v1/observations
```

**Request Body:** (ORM-shape observation dict)

#### List Observations

```http
GET /api/v1/observations
```

**Query Parameters:**
- `patient_id`: Filter by patient ID
- `code`: Filter by LOINC code
- `start_date` / `end_date`: Filter by date range
- `limit` / `offset`: Pagination

#### Get / Delete Observation

```http
GET /api/v1/observations/{id}
DELETE /api/v1/observations/{id}
```

### Clinical Events

#### List Event Types
```http
GET /api/v1/clinical-events/types
```
Returns all available event categories (Pregnancy, Pain, Dental, etc.).

#### List Events
```http
GET /api/v1/clinical-events
```
**Query Parameters:**
- `patient_id`: Filter by patient
- `examination_id`: Filter by linked examination
- `status`: Filter by `active`, `resolved`, etc.

#### Create Event
```http
POST /api/v1/clinical-events
```
**Request Body:**
```json
{
  "patient_id": "uuid",
  "type_id": "uuid",
  "title": "Third Pregnancy",
  "description": "Routine monitoring",
  "onset_date": "2026-01-15T10:00:00Z",
  "event_metadata": {
    "lmp": "2026-01-01"
  },
  "examinations": [
    { "examination_id": "uuid", "reason": "Initial scan" }
  ]
}
```

#### Link Examination
```http
POST /api/v1/clinical-events/{event_id}/link-examination
```
Associate an existing visit with an event.

#### List Correlated Biomarkers
```http
GET /api/v1/clinical-events/types/{type_id}/biomarkers
```
Returns all biomarkers conceptually linked to a specific event type (e.g., Vision -> Visual Acuity).

### Wearable Data


#### Upload Wearable Data

```http
POST /api/v1/wearable/data
```

**Request Body:**
```json
{
  "device_id": "garmin-vivoactive-4",
  "data": [
    {
      "timestamp": "2024-01-15T08:00:00Z",
      "heart_rate": 72,
      "steps": 1500,
      "calories": 250
    }
  ]
}
```

**Response:** `201 Created`

#### Get Wearable Data

```http
GET /api/v1/wearable/data
```

**Query Parameters:**
- `device_id`: Filter by device
- `start_date`: Start timestamp
- `end_date`: End timestamp
- `metrics`: Comma-separated metrics

#### Get Daily Summary

```http
GET /api/v1/wearable/data/summary
```

**Query Parameters:**
- `date`: Date
- `device_id`: Filter by device

### Notifications & Push

> All notification endpoints require authentication and are
> **tenant-scoped** via `current_user.tenant_id`. Patient-scoped routes
> additionally call `check_patient_access` so a `USER`-role caller can
> only reach patients assigned to them. Cross-tenant calls return `404`
> (no leak that the row exists elsewhere).

#### Get VAPID Public Key

```http
GET /api/v1/notifications/vapid-public-key
```

Returns the public key required for PWA Web Push registration.

#### Register Subscription

```http
POST /api/v1/notifications/subscribe
```

**Request Body:**
```json
{
  "subscription": {
    "endpoint": "...",
    "keys": { "p256dh": "...", "auth": "..." }
  },
  "device_id": "optional-uuid",
  "user_agent": "..."
}
```

#### List Notifications

```http
GET /api/v1/notifications
```

**Query Parameters:**
- `patient_id`: (Required) Filter by patient. The caller must have
  access to this patient (`USER`-role patient-assignment check;
  `ADMIN`/`MANAGER` see any patient in their tenant).
- `unread_only`: (Boolean)
- `limit`: Default 20

**Tenant-scoped**: results are additionally constrained to
`current_user.tenant_id`.

#### Mark as Read

```http
PATCH /api/v1/notifications/{id}/read
```

**Tenant-scoped**: a cross-tenant call returns `404` (no leak).

#### Mark as Delivered

```http
PATCH /api/v1/notifications/{id}/delivered
```

**Requires authentication** (Bearer JWT). **Tenant-scoped**: a cross-
tenant call returns `404`. This endpoint previously had no auth at all
and was used by the frontend service worker; the SW now needs to send
the session JWT.

#### Create Trigger

```http
POST /api/v1/notifications/triggers
```

**Request Body:**
```json
{
  "patient_id": "uuid",
  "title": "Reminder",
  "body": "Message",
  "notification_type": "medication_reminder",
  "trigger_type": "recurring",
  "config": {
    "at": "09:00",
    "days": ["mon", "wed"]
  }
}
```

**Patient-access scoped**: `USER`-role callers can only create triggers
on patients assigned to them; `ADMIN`/`MANAGER` see any patient in
their tenant.

#### List Triggers

```http
GET /api/v1/notifications/triggers?patient_id=<uuid>
```

**Tenant-scoped**: results constrained to `current_user.tenant_id`.
**Patient-access scoped** for `USER` role.

#### Delete Trigger

```http
DELETE /api/v1/notifications/triggers/{trigger_id}
```

**Tenant-scoped**: a cross-tenant delete is a no-op (the endpoint
returns success either way to avoid leaking existence).

#### Test Trigger

```http
POST /api/v1/notifications/triggers/{trigger_id}/test
```

Fires the trigger immediately (skipping its schedule). **Tenant-scoped**:
a cross-tenant call returns `404`.

### Alerts

> All `/alerts/*` endpoints require authentication and are
> **tenant-scoped** via `current_user.tenant_id`. Cross-tenant read/
> update/delete/trigger calls return `404` (no leak). Patient-scoped
> routes additionally call `check_patient_access` so a `USER` cannot
> read another user's patient; `ADMIN`/`MANAGER` see the tenant-wide
> view.

#### Create Alert

```http
POST /api/v1/alerts
```

**Request Body:**
```json
{
  "type": "high_glucose",
  "patient_id": "123e4567-e89b-12d3-a456-426614174002",
  "threshold": 7.0,
  "enabled": true
}
```

#### List Alerts

```http
GET /api/v1/alerts
```

#### Get Alert History

```http
GET /api/v1/alerts/history
```

#### Get Dashboard Data

```http
GET /api/v1/analytics/dashboard
```

**Query Parameters:**
- `patient_id`: Filter by patient
- `period`: Time period

#### Get Biomarker Trends

```http
GET /api/v1/analytics/trends
```

**Query Parameters:**
- `biomarker_codes`: Comma-separated slugs, LOINC codes, or display names. When provided, the filter is expanded against the definition catalog (names, aliases, codes) so both mapped and unmapped observations are included.
- `period`: Time period (e.g., 'last-30-days', 'last-12-months', 'all-time')
- `aggregation`: Optional resolution bucket for telemetry data (e.g., '15 minutes', '1 day', '1 week')
- `patient_id`: Optional override for admin context

#### Remap Observations to Definition

```http
POST /api/v1/biomarkers/{biomarker_id}/remap
```

Relinks unmapped observations (those with `biomarker_id = NULL`) to a biomarker definition. Matches observations whose stored `code.text` equals `source_name` (case-insensitive). Used by the frontend's "Create biomarker" / "Map to existing" popup on unmapped biomarkers.

**Request Body:**
```json
{
  "source_name": "WBC",
  "patient_id": "uuid-of-patient (optional)"
}
```

**Response:**
```json
{
  "status": "success",
  "biomarker_id": "3e9e3f7e-42f1-439f-a73c-64fd6234aed5",
  "observations_remapped": 3
}
```

#### Get Analytics Summary

```http
GET /api/v1/analytics/summary
```

**Query Parameters:**
- `patient_id`: Filter by patient
- `period`: Time period

#### Get Reference Ranges

```http
GET /api/v1/analytics/reference-ranges
```

### Integrations & Webhooks

The integrations surface lives at `/api/v1/integrations/*` (see
[INTEGRATIONS_FRAMEWORK.md](INTEGRATIONS_FRAMEWORK.md) for the discovery
+ enable + sync flow). Two **tokenless** inbound routes are exposed for
external systems that don't carry the platform JWT:

#### Webhook (inbound push from the provider)

```http
POST /api/v1/integrations/{domain}/webhook/{integration_id}
```

HMAC-SHA256 verification when the integration's `user_config.webhook_secret`
is set. Supported signature headers: `X-Webhook-Signature`,
`X-Webhook-Signature-256`, `X-Hub-Signature-256` (GitHub; `sha256=<hex>`
prefix tolerated). Without a configured secret the integration UUID is
the only credential (legacy mode — operators are encouraged to set a
secret).

#### Generic two-way API proxy

```http
{GET|POST|PUT|DELETE} /api/v1/integrations/{domain}/api/{integration_id}/{path}
```

Forwards to the provider's `handle_api_request` for headless two-way
integration clients. **Auth:**
- **HMAC** (recommended) — when the integration's `user_config.api_secret`
  is set, the request MUST carry an `X-Api-Signature` header with an
  HMAC-SHA256 of the canonical request:

  ```
  METHOD\n<path>\n[<timestamp>\n]<raw_body>
  ```

  The optional `X-Api-Timestamp` header (epoch seconds) is folded into
  the signed payload and validated against a ±5-minute skew window to
  prevent replay. Missing or invalid signature → `401`.
- **Legacy UUID-only** — when no `api_secret` is configured, the
  integration UUID acts as the credential. A `logger.warning` is
  emitted on every call so operators notice the gap. This mode is
  preserved for backward compatibility but is not recommended for new
  deployments.

See [INTEGRATIONS_SDK.md](INTEGRATIONS_SDK.md) for the `webhook_secret` /
`api_secret` configuration flow.

## Error Handling

### Standard Error Response

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid input data",
    "details": [
      {
        "field": "email",
        "message": "Invalid email format"
      }
    ]
  }
}
```

### HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 201 | Created |
| 202 | Accepted (async operation) |
| 204 | No Content (delete success) |
| 400 | Bad Request |
| 401 | Unauthorized (missing/invalid token) |
| 403 | Forbidden (role/tenant/patient-access denied) |
| 404 | Not Found (also returned for cross-tenant calls — no leak) |
| 410 | Gone (FHIR R4 facade: tombstone of a soft-deleted resource) |
| 422 | Validation error |
| 429 | Too Many Requests (rate limited — future) |
| 500 | Internal Server Error |

## Current API Status

### ✅ Working Endpoints

#### Authentication
- `POST /api/v1/auth/login` - Login and get tokens
- `POST /api/v1/auth/refresh` - Refresh access token
- `GET /api/v1/auth/validate` - Validate current token
- `POST /api/v1/auth/register` - Register new user (bootstrap or invite)
- `POST /api/v1/auth/invite` - Issue a tenant invite token (ADMIN+)
- `GET /api/v1/users/me` - Get current user

#### Users
- `GET /api/v1/users/{id}` - Get user by ID
- `PUT /api/v1/users/{id}` - Update user
- `DELETE /api/v1/users/{id}` - Delete user

#### Tenants
- `GET /api/v1/tenants/{id}` - Get tenant
- `POST /api/v1/tenants` - Create tenant
- `PUT /api/v1/tenants/{id}` - Update tenant
- `DELETE /api/v1/tenants/{id}` - Delete tenant

#### Documents
- `POST /api/v1/documents` - Upload document
- `GET /api/v1/documents/{id}` - Get document
- `GET /api/v1/documents/{id}/download` - Download document
- `GET /api/v1/documents/{id}/preview` - Get preview image (presigned token or Bearer JWT; B5)
- `POST /api/v1/documents/{id}/extract` - Trigger extraction
- `GET /api/v1/documents/{id}/extract/status` - Get extraction status

#### Patients & Observations (domain endpoints — ORM-shape)
- `GET /api/v1/patients` - List patients
- `POST /api/v1/patients` - Create patient
- `GET /api/v1/patients/{id}` - Get patient
- `PUT /api/v1/patients/{id}` - Update patient
- `DELETE /api/v1/patients/{id}` - Delete patient
- `GET /api/v1/observations` - List observations (patient/code/date filters)
- `POST /api/v1/observations` - Create observation
- `GET /api/v1/observations/{id}` - Get observation
- `DELETE /api/v1/observations/{id}` - Delete observation
- For canonical FHIR R4 interop, see `/api/v1/fhir/R4/*` ([FHIR_R4_FACADE.md](FHIR_R4_FACADE.md))

#### Wearable Data
- `POST /api/v1/wearable/data` - Upload wearable data
- `GET /api/v1/wearable/data` - Get wearable data
- `GET /api/v1/wearable/data/summary` - Get daily summary
- `GET /api/v1/wearable/anomalies` - Detect anomalies

#### Alerts (tenant-scoped; B4)
- `GET /api/v1/alerts` - List alerts
- `POST /api/v1/alerts` - Create alert
- `GET /api/v1/alerts/{id}` - Get alert
- `PUT /api/v1/alerts/{id}` - Update alert
- `DELETE /api/v1/alerts/{id}` - Delete alert
- `POST /api/v1/alerts/{id}/trigger` - Trigger alert
- `GET /api/v1/alerts/history` - Get alert history

#### Analytics
- `GET /api/v1/analytics/dashboard` - Get dashboard data
- `GET /api/v1/analytics/trends` - Get biomarker trends
- `GET /api/v1/analytics/summary` - Get analytics summary
- `GET /api/v1/analytics/reference-ranges` - Get reference ranges

#### Notifications (tenant-scoped; B2/B3)
- `GET /api/v1/notifications/vapid-public-key` - Get VAPID key
- `POST /api/v1/notifications/subscribe` - Register for push
- `GET /api/v1/notifications` - List patient notifications
- `PATCH /api/v1/notifications/{id}/read` - Mark read
- `PATCH /api/v1/notifications/{id}/delivered` - Mark delivered (was unauthenticated; now requires JWT)
- `POST /api/v1/notifications/triggers` - Create manual trigger
- `GET /api/v1/notifications/triggers` - List triggers for a patient
- `DELETE /api/v1/notifications/triggers/{id}` - Delete a trigger
- `POST /api/v1/notifications/triggers/{id}/test` - Fire a trigger immediately

#### Task Monitoring (tenant-scoped except SYSTEM_ADMIN; B1)
- `GET /api/v1/task-monitor/documents/processing` - List stuck processing documents
- `GET /api/v1/task-monitor/examinations/processing` - List stuck processing examinations
- `POST /api/v1/task-monitor/documents/retry/{id}` - Retry OCR for a document
- `POST /api/v1/task-monitor/examinations/retry/{id}` - Retry extraction for an examination
- `GET /api/v1/task-monitor/stats` - Aggregate task stats (tenant-scoped; global for SYSTEM_ADMIN)

#### Integrations & Webhooks
- `GET /api/v1/integrations` - List available integrations
- `GET /api/v1/integrations/{domain}` - Integration metadata / config flow
- `POST /api/v1/integrations/{domain}/enable` - Enable an integration for the current user
- `DELETE /api/v1/integrations/{domain}/disable` - Disable an integration
- `POST /api/v1/integrations/{domain}/sync/{integration_id}` - Trigger a manual sync
- `POST /api/v1/integrations/{domain}/webhook/{integration_id}` - Inbound webhook (HMAC via `webhook_secret`)
- `ANY /api/v1/integrations/{domain}/api/{integration_id}/{path}` - Generic two-way API proxy (HMAC via `api_secret` + `X-Api-Signature`; B8)

#### FHIR R4 Facade (interop surface)
- `GET /api/v1/fhir/R4/metadata` - CapabilityStatement
- `GET/POST /api/v1/fhir/R4/{Resource}` - Search / Create (15 resource types)
- `GET/PUT/DELETE /api/v1/fhir/R4/{Resource}/{id}` - Read / Update (version-bumps) / Delete (soft-delete → 410 Gone)
- See [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md)

#### Export & Import (Backup)

See [EXPORT_IMPORT.md](EXPORT_IMPORT.md) for the full format/scopes/restore spec.

- `POST /api/v1/export` - Create an export job (body: `BackupRequest` with `scope` ∈ {patient, group, system}, `export_type` ∈ {fhir_only, full_backup, catalog_only}, optional `patient_ids`). Enqueues a Celery task; returns `ExportJobResponse`.
- `GET /api/v1/export/jobs` - List export jobs for the current tenant.
- `GET /api/v1/export/jobs/{job_id}` - Get export job status.
- `GET /api/v1/export/jobs/{job_id}/download` - Download the generated file (FHIR Bundle `.fhir.json`, full-backup `.zip`, or catalog `.catalog.json`).
- `POST /api/v1/import/backup` - Upload a ZIP or bare `bundle.json`/`catalog.json` (multipart `file`). Enqueues a Celery task; returns `ImportJobResponse`.
- `GET /api/v1/import/jobs/{job_id}` - Get backup-import job status + `restore_result` (created/updated counts, `manifest_verified`, `fhir_validated`).
- `POST /api/v1/import/fhir` - Synchronous FHIR Bundle import (smaller bundles).
- `POST /api/v1/import/csv` - CSV import (legacy).
- `POST /api/v1/import/ocr` - OCR extraction (legacy).

**Role gating**: `patient` scope — any role (`USER` limited to a single own patient); `group` — `MANAGER`+; `system` — `ADMIN`+. `SYSTEM_ADMIN` bypasses all checks.

### ⚠️ Legacy Note

Some internal service functions might still use fallback logic if specific external dependencies (like local OCR engines or AI providers) are not fully configured in the `.env` file. However, the core database CRUD and multi-tenancy logic are fully implemented.

## API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc