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

> Unified multi-source/multi-recipient/multi-channel notification system.
> All endpoints require authentication and are **tenant-scoped** via
> `current_user.tenant_id`. Cross-tenant calls return `404`. Patient-scoped
> routes additionally call `check_patient_access`. See
> [NOTIFICATION_SYSTEM.md](NOTIFICATION_SYSTEM.md) for the full
> architecture (fan-out model, target resolution, rules engine, real-time
> WS).

#### Get VAPID Public Key

```http
GET /api/v1/notifications/vapid-public-key
```

Returns the public key for Web Push registration. Public (no auth).

#### Register Push Subscription

```http
POST /api/v1/notifications/subscribe
```

**Request body** (`SubscribeRequest` Pydantic schema — the whole envelope
is parsed; do **not** pass the subscription as a bare dict with metadata
in query params):

```json
{
  "subscription": {
    "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/...",
    "keys": { "p256dh": "...", "auth": "..." }
  },
  "device_id": "optional-uuid",
  "user_agent": "Mozilla/5.0 ..."
}
```

#### Personal Inbox

```http
GET /api/v1/notifications/inbox
```

**Query Parameters:**
- `status`: `unread` | `read` | `dismissed`
- `category`: notification category
- `source`: notification source
- `patient_id`: filter to a patient (access-checked)
- `limit` (default 50, max 100), `offset`

Returns `{items: NotificationRecipientRead[], total}` — per-user inbox
state joined to the notification event.

#### Unread Count

```http
GET /api/v1/notifications/unread-count
```

Returns `{count: int}` — for the bell badge.

#### Mark Read / Dismiss / Read-All

```http
PATCH /api/v1/notifications/{recipient_id}/read
PATCH /api/v1/notifications/{recipient_id}/dismiss
POST  /api/v1/notifications/read-all
```

Operate on the user's own inbox rows (tenant-scoped; cross-tenant = `404`).

#### Admin Feed / Stats / Delivery Detail

> Role-gated to `ADMIN` / `MANAGER` / `SYSTEM_ADMIN`. `SYSTEM_ADMIN` sees
> cross-tenant data and may pass `tenant_id` to target another tenant.

```http
GET /api/v1/notifications/admin
GET /api/v1/notifications/admin/stats
GET /api/v1/notifications/admin/{notification_id}/delivery
```

- `/admin` — tenant-wide (or cross-tenant for `SYSTEM_ADMIN`) feed of every notification event. Filters: `type`, `source`, `category`, `tenant_id`.
- `/admin/stats` — aggregated counts: `by_source`, `by_category`, `delivery` (per-channel × status matrix), `recipients` (total inbox rows), `unique_recipients` (distinct user count), `total`.
- `/admin/{id}/delivery` — per-recipient breakdown for one notification. Returns the notification, sender (email resolved), and per-recipient inbox status + per-channel delivery state (channel, status, error, timestamps). Used by the admin center's click-to-detail modal.

#### System Broadcast

```http
POST /api/v1/admin/notifications/broadcast
```

**Query Parameters:**
- `title` (required)
- `body`
- `severity`: `info` | `warning` | `critical` (default `info`)
- `scope`: `tenant` (default) | `system` — `system` requires `SYSTEM_ADMIN`
- `tenant_id`: target another tenant (`SYSTEM_ADMIN` only)

Emits a `SYSTEM`/`SYSTEM_BROADCAST` notification to every user in scope (`TENANT` target for tenant scope, `SYSTEM` target = every `SYSTEM_ADMIN` for system scope).

#### Scheduled Triggers (reminders)

```http
POST   /api/v1/notifications/triggers
GET    /api/v1/notifications/triggers
DELETE /api/v1/notifications/triggers/{trigger_id}
POST   /api/v1/notifications/triggers/{trigger_id}/test
```

`TriggerType`: `TIME` (one-shot at a datetime) or `RECURRING` (wall-clock
schedule with optional days-of-week). `TriggerType.EVENT` and the legacy
`biomarker_update` event hook were removed — biomarker thresholds are now
event-driven via the rules engine below.

`GET /triggers` `patient_id` is **optional** — without it, lists
tenant-wide (used by the global Notification Center "Reminders" tab);
with it, access-checked and patient-scoped.

#### Biomarker Rules (event-driven alerts)

Replaces the removed `/alerts/*` endpoints. Rules are evaluated on every
observation ingestion (`fhir_service.create_observation` →
`notification_rule_service.evaluate_and_fire`).

```http
GET    /api/v1/notification-rules
POST   /api/v1/notification-rules
PUT    /api/v1/notification-rules/{id}
DELETE /api/v1/notification-rules/{id}
POST   /api/v1/notification-rules/{id}/test
```

#### Real-time WebSocket

```http
GET (WS) /api/v1/ws/notifications
```

Per-user live stream over Redis pub/sub. Auth via the
`["bearer", <jwt>]` Sec-WebSocket-Protocol subprotocol (the token stays
out of URL logs). Server-side keepalive ping every 30s. The frontend's
`useNotificationStream` hook auto-reconnects with 5s backoff and falls
back to a 30s unread-count poll if the socket can't open.

### Analytics

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

#### Notification Action (button click handler)

```http
POST /api/v1/integrations/{domain}/notification-action/{integration_id}/{action_id}
```

Server-side handler for clicked action buttons on integration-authored
notifications (the buttons of `type="post"` in `payload.actions[]`).
Routes to the provider's `handle_notification_action(integration,
action_id, payload)` and returns an `ActionResult`-shape dict
(`{"message": ..., "results": [DisplayBlock...]}`) that the frontend
renders in a follow-up modal — same renderer as custom-action results.

**Auth**: Bearer JWT, tenant-scoped (the caller must own the integration).
The provider must opt in via `supports_notifications() → True` (see
[INTEGRATIONS_SDK.md §3.9](INTEGRATIONS_SDK.md#39-notifications-event-driven-rich-actionable)).

**Request body**: arbitrary JSON (passed through as the `payload` argument
to the provider). Most action buttons send an empty object `{}`.

#### Notification Types (per-integration preferences)

```http
GET /api/v1/integrations/notification-types
PUT /api/v1/integrations/{domain}/notification-types/{type_id}
```

**`GET /integrations/notification-types`** — aggregates every enabled
integration's declared notification kinds into a single response:

```json
{
  "integrations": [
    {
      "domain": "dev_dummy",
      "instance_name": "My test dummy",
      "integration_id": "<uuid>",
      "types": [
        {
          "id": "elevated_heart_rate",
          "label": "Elevated heart-rate alerts",
          "description": "Fires when a synced heart-rate reading exceeds 100 bpm.",
          "category": "alert",
          "severity": "warning",
          "default_enabled": true,
          "channels": ["IN_APP", "PUSH"],
          "enabled": true
        }
      ]
    }
  ]
}
```

`enabled` resolves as: USER setting wins, otherwise the provider's
`default_enabled`, otherwise `true`. Used by both the per-integration
"Notifications" tab and the central `/settings/notifications` rollup.

**`PUT /integrations/{domain}/notification-types/{type_id}`** — set the
caller's per-type preference. Body: `{"enabled": <bool>}`. Setting
`enabled=true` removes the override (revert to default); `false` writes
`user.settings["notifications.integration.{domain}.{type_id}"] = false`.
404 if the caller doesn't own an integration of the given domain.

Prefs are keyed by `(domain, type_id)`, NOT by integration instance —
multiple instances of the same domain share prefs. See
[INTEGRATIONS_SDK.md §3.9 → Per-type user preferences](INTEGRATIONS_SDK.md#per-type-user-preferences-opt-out).

### Catalogs & Search

Health Assistant groups its reference vocabularies (anatomy, taxonomy/concepts,
biomarkers, medications, allergies, vaccines) under a uniform **Catalog
Registry** (`app/catalogs/`). Every catalog conforms to one
CRUD/access/search/FHIR/edge contract, exposed through a thin registry-driven
meta-layer that **complements** (does not replace) the domain endpoints
(`/biomarkers`, `/medications`, `/anatomy`, `/concepts`, `/allergies`).

Access is **ownership-based via scope tiers** (Phase A), not pure RBAC: every
item carries a `scope` of `system` | `tenant` | `user`. Any authenticated user
may **create** — the scope is derived from the role (SYSTEM_ADMIN→system,
ADMIN/MANAGER→tenant, USER→user). Update/delete: `system`→SYSTEM_ADMIN,
`tenant`→ADMIN/MANAGER, `user`→creator OR ADMIN. `CatalogPermissionDenied` →
403. Every write appends a `CatalogAuditLog` row (Phase B).

#### List registered catalog types
`GET /api/v1/catalogs` → `{types: [{type, ui, has_concept_link, edge_endpoint_type, search_columns}, …]}`
(drives the `/catalogs?type=` workspace left rail).

#### List / get / create / update / delete one catalog type
```
GET    /api/v1/catalogs/{type}?search=&scope=&class=&kind=&include=relations&limit=&offset=  → {"items":[…], "total":n}
GET    /api/v1/catalogs/{type}/{id}
POST   /api/v1/catalogs/{type}                          (any role; scope derived from role)
PUT    /api/v1/catalogs/{type}/{id}                     (scope + ownership gated)
DELETE /api/v1/catalogs/{type}/{id}                     (scope + ownership gated)
```
`{type}` ∈ `biomarker | medication | allergy | anatomy | vaccine | concept`.
Reads are tenant-scoped (`or_(tenant_id == caller, tenant_id IS NULL)`).
- `?scope=system|tenant|user` narrows to a tier.
- `?class=<slug>` (comma-list OK) filters any catalog whose items carry a
  `class_concept_id` FK (anatomy, biomarker, medication, allergy, vaccine) by
  its taxonomy class — the adapter resolves the slug → concept id. Each item is
  annotated with `class_concept_slug` + `class_concept_name` (e.g. Thyroid →
  `organ` / `Organ`).
- `?kind=<ConceptKind>` filters the `concept` catalog by `primary_kind`
  (e.g. `anatomy_class`, `disease`).
- `?include=relations` annotates each item with `relation_count` +
  `relation_breakdown` (per relation type) via a single batched count query.

#### Scope promotion / demotion
`POST /api/v1/catalogs/{type}/{id}/promote` body `{"scope":"tenant"|"system"|"user"}`
— transitions an item's scope. user↔tenant requires ADMIN/MANAGER; any
transition involving `system` requires SYSTEM_ADMIN. Promote-to-system clears
`tenant_id`; demote-to-tenant sets it to the actor's tenant.

#### Audit history
`GET /api/v1/catalogs/{type}/{id}/history` → `{items: [audit_entry, …]}` —
newest-first append-only trail (create/update/delete/promote/demote). Each
entry records the operation, who (`user_email`), when, and any scope transition.
Tenant-scoped (the item must be visible to the caller).

#### Cross-catalog graph traversal
`GET /api/v1/catalogs/{type}/{id}/relations?depth=1-3&relation=&include_proposed=`
→ `{start, nodes, edges}` — the polymorphic `concept_edges` subgraph reachable
within `depth` hops. Powers "which organ does this biomarker affect? what treats
that disease?" via a depth-bounded, cycle-safe recursive CTE
(`app/services/catalog_graph_service.py`).

#### Unified catalog search
`GET /api/v1/catalogs/search?q=&types=&limit=` → `{results: [{type, id, label}, …]}`
— typo-tolerant (`pg_trgm`) search across **all** registered catalogs at once,
tenant-scoped. `types` is an optional comma-separated subset.

#### Global search
`GET /api/v1/search?q=` → `{results: [{id, type, title, subtitle}, …]}` —
patient/examination/document/clinical-event blocks (inline ILIKE) **plus** the
catalog portion delegated to the registry-driven dispatcher, so anatomy,
concepts, and allergies appear automatically (not just medications + biomarkers).

### Vaccines & Immunizations (Phase 5)

Vaccinations are implemented end-to-end, mirroring the medications pattern:
`VaccineCatalog` is the CVX-coded reference definition (the product);
`PatientImmunization` is the dose-administered patient-instance record.

```
# Catalog CRUD (RBAC: USER read-only; ADMIN/MANAGER tenant; SYSTEM_ADMIN global)
GET    /api/v1/vaccines/catalog?search=            → List[VaccineCatalogResponse]
GET    /api/v1/vaccines/catalog/{catalog_id}        → VaccineCatalogResponse
POST   /api/v1/vaccines/catalog                     → VaccineCatalogResponse  (ADMIN+)
PUT    /api/v1/vaccines/catalog/{catalog_id}        → VaccineCatalogResponse  (global→SYSTEM_ADMIN)
DELETE /api/v1/vaccines/catalog/{catalog_id}                                    (global→SYSTEM_ADMIN)

# Patient immunization instances (patient-access scoped)
GET    /api/v1/vaccines/patient/{patient_id}        → List[PatientImmunizationResponse]
POST   /api/v1/vaccines/patient/{patient_id}        → PatientImmunizationResponse
GET    /api/v1/vaccines/{immunization_id}           → PatientImmunizationResponse
PUT    /api/v1/vaccines/{immunization_id}           → PatientImmunizationResponse
DELETE /api/v1/vaccines/{immunization_id}
```

FHIR: `GET /api/v1/fhir/R4/Immunization?patient={id}&date=&status=` returns a
searchset Bundle (the `PatientImmunization` row projects to a canonical R4
`Immunization` via `to_fhir_dict()`). `VaccineCatalog` projects to `Medication`.
Vaccines also appear in the unified catalog endpoints (`/catalogs/vaccine`,
`/catalogs/search?q=&types=vaccine`) and the cross-catalog graph.

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
> **Removed.** The legacy `/alerts/*` endpoints and `AlertModel` were
> deleted in the unified notification system refactor. Biomarker
> threshold alerts are now event-driven via the `notification_rules`
> engine (see Notifications below).

#### Analytics
- `GET /api/v1/analytics/dashboard` - Get dashboard data
- `GET /api/v1/analytics/trends` - Get biomarker trends
- `GET /api/v1/analytics/summary` - Get analytics summary
- `GET /api/v1/analytics/reference-ranges` - Get reference ranges

#### Notifications (unified; tenant-scoped)
> See [NOTIFICATION_SYSTEM.md](NOTIFICATION_SYSTEM.md) for the full
> architecture (fan-out model, target resolution, rules engine).

- `GET /api/v1/notifications/vapid-public-key` - Get VAPID key (public, no auth)
- `POST /api/v1/notifications/subscribe` - Register a Web Push subscription (body = `SubscribeRequest`)
- `GET /api/v1/notifications/inbox` - Personal inbox (filters: status/category/source/patient_id/limit/offset)
- `GET /api/v1/notifications/unread-count` - Bell badge count
- `PATCH /api/v1/notifications/{recipient_id}/read` - Mark inbox row read
- `PATCH /api/v1/notifications/{recipient_id}/dismiss` - Dismiss inbox row
- `POST /api/v1/notifications/read-all` - Mark all unread as read
- `GET /api/v1/notifications/admin` - Tenant-wide feed (ADMIN/MANAGER/SYSTEM_ADMIN; cross-tenant for SYSTEM_ADMIN)
- `GET /api/v1/notifications/admin/stats` - Aggregate delivery stats (by source/category/channel-status, unique recipients)
- `GET /api/v1/notifications/admin/{notification_id}/delivery` - Per-recipient delivery breakdown for one notification
- `POST /api/v1/admin/notifications/broadcast` - System broadcast (tenant or system scope)
- `POST /api/v1/notifications/triggers` - Create scheduled/recurring trigger
- `GET /api/v1/notifications/triggers` - List triggers (tenant-wide when `patient_id` omitted)
- `DELETE /api/v1/notifications/triggers/{id}` - Delete a trigger
- `POST /api/v1/notifications/triggers/{id}/test` - Fire a trigger immediately
- `GET /api/v1/notification-rules` - List biomarker rules
- `POST /api/v1/notification-rules` - Create a biomarker rule
- `PUT /api/v1/notification-rules/{id}` - Update a rule
- `DELETE /api/v1/notification-rules/{id}` - Delete a rule
- `POST /api/v1/notification-rules/{id}/test` - Force-fire a rule
- `GET (WS) /api/v1/ws/notifications` - Per-user real-time stream (Bearer subprotocol auth)

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
- `POST /api/v1/integrations/{domain}/webhook/{integration_id}` - Inbound webhook (HMAC via `webhook_secret`); triggers the same notification dispatch as `run_sync`
- `ANY /api/v1/integrations/{domain}/api/{integration_id}/{path}` - Generic two-way API proxy (HMAC via `api_secret` + `X-Api-Signature`; B8)
- `POST /api/v1/integrations/{domain}/notification-action/{integration_id}/{action_id}` - Server-side handler for clicked action buttons on integration-authored notifications (see [INTEGRATIONS_SDK.md §3.9](INTEGRATIONS_SDK.md))
- `GET /api/v1/integrations/notification-types` - Aggregate every enabled integration's declared notification types + the caller's per-type prefs
- `PUT /api/v1/integrations/{domain}/notification-types/{type_id}` - Set the caller's per-type preference (body: `{enabled: bool}`)

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