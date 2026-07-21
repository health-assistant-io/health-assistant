# Health Assistant — REST API Reference

The Health Assistant REST API exposes the platform's clinical and operational
surface over JSON. It's the primary API for the web frontend, the future mobile
companion app, and any external integrator that prefers a domain-shaped REST
contract over canonical FHIR. Every endpoint is **tenant-scoped** (callers only
see rows whose `tenant_id` matches their own, except `SYSTEM_ADMIN`) and most
patient-scoped routes additionally verify ownership for the `USER` role.

**Base URL:** `http://localhost:8000/api/v1`
**Interactive docs (when running):** `http://localhost:8000/docs` (Swagger) and `http://localhost:8000/redoc`
**FHIR R4 interop surface:** `/api/v1/fhir/R4/*` — see [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md)

> This reference documents every router module mounted under
> `backend/app/api/v1/endpoints/`. Each section mirrors the FastAPI tag/grouping.
> 298 HTTP/WS handlers across 36 modules. For the always-current OpenAPI
> rendering, visit `http://localhost:8000/docs` while the server is running.

## Contents

- [Authentication & authorization](#authentication--authorization) — JWT, tenant/patient scoping, rate limits
- [Quick start — common flows](#quick-start--common-flows) — login → list → upload → extract → subscribe
- [Identity & access](#identity--access) — `users`, `tenants`, `settings`, `admin`, `admin/tenants`, `admin/integrations`
- [Patient clinical record](#patient-clinical-record) — `patients`, `patients/{id}/layouts`, `observations`, `examinations`, `documents`, `clinical-events`
- [Treatments & alerts](#treatments--alerts) — `medications`, `allergies`, `vaccines`, `notifications`, `notification-rules`
- [Catalogs & reference data](#catalogs--reference-data) — `catalogs`, `biomarkers`, `concepts`+`concept-edges`, `anatomy`, `search`+`instances`
- [Analytics](#analytics) — `analytics`, `telemetry`
- [AI](#ai) — `ai-config`, `ai-assistance`
- [Integrations](#integrations) — discovery, OAuth, instances, inbound webhooks / API proxy
- [Operations](#operations) — `task-monitor`, `ws`, `export`, `import`, `doctors`, `organizations`
- [FHIR R4 facade](#fhir-r4-facade) — canonical interop surface (15 resource types)
- [Error handling](#error-handling) — domain exceptions, status code table
- [See also](#see-also)

---

## Authentication & authorization

### JWT

Include a JWT in the `Authorization` header:

```
Authorization: Bearer <your-jwt-token>
```

JWTs are HS256, carry `user_id`, `tenant_id`, `role`, and `sub`, and are validated
by `get_current_user` (no DB lookup — trust is in the token). `get_current_user_ws`
is the WebSocket variant and reads the token from the `["bearer", <jwt>]`
`Sec-WebSocket-Protocol` subprotocol.

### Tenant & patient scoping

Every authenticated request carries a `tenant_id` (from the JWT). All
list/read/write endpoints are **tenant-scoped** — a caller can only see rows whose
`tenant_id` matches their own. Cross-tenant calls return `404` (not `403`) so the
existence of a row in another tenant is not leaked. The deliberate exception is
the **`SYSTEM_ADMIN`** role, which bypasses the tenant filter (operator visibility
— see [TENANCY_AND_USER_MANAGEMENT.md](TENANCY_AND_USER_MANAGEMENT.md)).

For patient-scoped endpoints (anything that takes a `patient_id`), a `USER`-role
caller must additionally be the patient's linked user
(`Patient.user_id == current_user.user_id`); `ADMIN` and `MANAGER` see all
patients in their tenant. The canonical check is `check_patient_access` in
`app/services/access.py`.

### Rate limiting

The auth endpoints are rate-limited per client IP via Redis fixed-window counters
(`app/core/rate_limit.py`). Degrades open if Redis is unreachable.

| Endpoint | Cap |
|---|---|
| `POST /auth/login` | 20 / minute |
| `POST /auth/register` | 5 / minute |
| `POST /auth/refresh` | 30 / minute |
| `POST /auth/invite` | 10 / minute |

### `auth` — login, register, invite, tokens

| Method | Path | Body | Response | Notes |
|---|---|---|---|---|
| `POST` | `/auth/login` | `OAuth2PasswordRequestForm` (`username`, `password`) | `TokenResponse` | Issues access + refresh JWTs. Rejects service-account emails. |
| `POST` | `/auth/register` | `UserRegister` | `UserResponse` | Two paths: **bootstrap** (no `tenant_id`) creates a new tenant + "Default Household" org; the first user ever is promoted to `SYSTEM_ADMIN` (race-protected via `pg_advisory_xact_lock`), subsequent bootstraps become `ADMIN`. **Join** requires `tenant_id` + a valid `invite_token` JWT. |
| `POST` | `/auth/invite` | (none; query: `tenant_id?`, `email?`, `role=user\|manager\|admin`, `expires_days=7`) | `{invite_token, tenant_id, role, expires_in_days}` | `ADMIN` / `MANAGER` / `SYSTEM_ADMIN` only. Non-`SYSTEM_ADMIN` can only mint for own tenant. `SYSTEM_ADMIN` cannot be granted via invite. |
| `POST` | `/auth/service-account` | (none; query: `instance_name` *, `tenant_id?`, `expires_days=90`) | `{access_token, token_type, tenant_id, client_id, expires_in_days}` | `ADMIN`/`MANAGER`/`SYSTEM_ADMIN`. Mints a long-lived service-account JWT (creates a `UserModel` with `is_service_account=True`). `ADMIN`/`MANAGER` limited to own tenant. |
| `GET` | `/auth/validate` | (none) | `{valid: true, user_id}` | Lightweight check that the JWT is still valid. |
| `POST` | `/auth/refresh` | `{refresh_token}` | `TokenResponse` | **Rotates** the refresh token (audit A5): the presented `jti` is revoked and a brand-new refresh token is returned. Access tokens can no longer be replayed here (the `type=refresh` claim is enforced). |
| `POST` | `/auth/logout` | `{refresh_token}` | `{revoked: true}` | Revokes one refresh token's `jti`. |
| `POST` | `/auth/logout-all` | (none) | `{revoked: <count>}` | Revokes every refresh token for the calling user. |

#### Register examples

```json
// Path 1 — bootstrap a new tenant
{
  "email": "user@example.com",
  "password": "securepassword123"
}

// Path 2 — join an existing tenant (invite required)
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
  "expires_in": 86400
}
```

See [TENANCY_AND_USER_MANAGEMENT.md](TENANCY_AND_USER_MANAGEMENT.md) for the full
invite + bootstrap flow and curl examples.

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
#     "token_type": "Bearer", "expires_in": 86400 }

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

### `users`

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/users/me` | any | — | `UserResponse` | Returns the current user. Handles switched `SYSTEM_ADMIN` via `original_tenant_id`. |
| `GET` | `/users/{user_id}` | self / admin / manager / SYSTEM_ADMIN | — | `UserResponse` | Single-user fetch. |
| `GET` | `/users` | `ADMIN` / `MANAGER` | — | `List[UserResponse]` | List users in the caller's tenant. |
| `POST` | `/users` | `ADMIN` / `SYSTEM_ADMIN` | `UserCreate` | `UserResponse` (`201`) | Create user in a tenant. `tenant_id` defaults to caller's tenant. |
| `PUT` | `/users/{user_id}` | self / admin / manager | (query: `email?`, `role?`, `settings?`) | `UserResponse` | Role change requires admin. |
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
| `POST` | `/admin/notifications/broadcast` | `ADMIN` / `SYSTEM_ADMIN` | query: `title` *, `body?`, `severity=info\|warning\|critical`, `scope=tenant\|system`, `tenant_id?` | `{status, notification_id}` | Emits `SYSTEM`/`SYSTEM_BROADCAST` to every user in scope (`TENANT` target for tenant, `SYSTEM` target = every `SYSTEM_ADMIN` for system). |
| `POST` | `/admin/catalogs/import/url` | `SYSTEM_ADMIN` | query: `url` * | `{message}` | Fetches a clinical-ontology catalog JSON and runs the import in the background. |
| `POST` | `/admin/catalogs/import/file` | `SYSTEM_ADMIN` | multipart: `file` | `{message}` | Validates + imports an uploaded catalog JSON. |
| `GET` | `/admin/seeds/export.zip` | `SYSTEM_ADMIN` | — | `application/zip` (attachment) | Streams the running instance's global taxonomy / anatomy / catalog data as flat seed-format JSON (read-only; never touches server's `data/seeds/`). |

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
| `POST` | `/admin/tenants/{tenant_id}/invite` | `CreateInvitePayload` | `InviteResponse` | Mint a tenant-scoped invite token. |
| `GET` | `/admin/tenants/{tenant_id}/audit` | query: `action?`, `limit=50`, `offset=0` | `AuditListResponse` | Paginated audit-log viewer. |

### `admin/integrations` — global integration enablement

All routes `SYSTEM_ADMIN`-only.

| Method | Path | Response | Notes |
|---|---|---|---|
| `GET` | `/admin/integrations` | `List[{domain, name, version, is_enabled}]` | Merges on-disk manifests with DB state. |
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
| `GET` | `/patients` | any | query: `tenant_id?`, `user_id?`, `limit=10`, `offset=0` | `List[patient]` | `USER` is auto-pinned to own `user_id`. `SYSTEM_ADMIN` can override `tenant_id`. |
| `POST` | `/patients` | any | patient dict (ORM shape) | `patient` | `USER` auto-pinned to own `user_id`. |
| `GET` | `/patients/{patient_id}` | `check_patient_access` | — | `patient` | Tenant + ownership enforced. |
| `PUT` | `/patients/{patient_id}` | `check_patient_access` | patient dict | `patient` | Update patient info. |
| `DELETE` | `/patients/{patient_id}` | `check_patient_access` | — | `{message}` | Delete patient + all associated clinical data. Non-admin / non-`SYSTEM_ADMIN` must own the patient. |
| `PUT` | `/patients/{patient_id}/layout` | `check_patient_access` | layout dict | `patient` | **Legacy single-slot** `Patient.dashboard_layout`. For multi-layout persistence use `/patients/{id}/layouts/*`. |

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
| `POST` | `/observations` | `check_patient_access` | observation dict (FHIR-shape `subject.reference` or top-level `patient_id`) | `observation` | Writes an `AuditLog` entry (provenance). Fires `NotificationManager.trigger_event("biomarker_update")` + the rules engine. |
| `GET` | `/observations/{observation_id}` | tenant-scoped | — | `observation` | 404 cross-tenant. |
| `DELETE` | `/observations/{observation_id}` | `USER` patient-access check via `subject.reference` | — | `{message}` | Audited. |

### `examinations` — clinical visits

The clinical-visit container. Holds documents, observations, medications, doctors,
category concept, organization, and event links. Patient-access is enforced via
`check_examination_access` (chains through `check_patient_access`).

The `POST /examinations` write path delegates to `examination_service.create_examination`
— the canonical write chokepoint also used by integrations that opt into
`supports_examinations` (see [INTEGRATIONS_SDK.md §3.10](INTEGRATIONS_SDK.md#310-clinical-events--examinations-opt-in-write-hooks)).
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
| `GET` | `/documents/{document_id}/download` | presigned `?token=` | — | `FileResponse` | Forces `attachment` for active-content types; `nosniff` always set (audit A3). |
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

**Event instances** (the journey):

The `POST /clinical-events` write path delegates to `clinical_event_service.create_event`
— the canonical write chokepoint also used by integrations that opt into
`supports_clinical_events` (see [INTEGRATIONS_SDK.md §3.10](INTEGRATIONS_SDK.md#310-clinical-events--examinations-opt-in-write-hooks)).
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
| `DELETE` | `/medications/catalog/{catalog_id}` | scope+ownership | — | `{message}` |
| `GET` | `/medications/catalog/{catalog_id}/usage` | any | — | usage report |
| `POST` | `/medications/catalog/{catalog_id}/reprocess` | any | — | `MedicationCatalogResponse` (re-run AI enrichment) |
| `GET` | `/medications/patient/{patient_id}` | `check_patient_access` | — | `List[MedicationRecordResponse]` |
| `POST` | `/medications/patient/{patient_id}` | `check_patient_access` | `MedicationRecordCreate` | `MedicationRecordResponse` |
| `GET` | `/medications/{medication_id}` | `check_medication_access` | — | `MedicationRecordResponse` |
| `PUT` | `/medications/{medication_id}` | `check_medication_access` | `MedicationRecordUpdate` | `MedicationRecordResponse` |
| `DELETE` | `/medications/{medication_id}` | `check_medication_access` | — | `{message}` |

### `allergies` — catalog + patient instances

Same scope model as medications.

| Method | Path | Auth | Body / Query | Response |
|---|---|---|---|---|
| `GET` | `/allergies/catalog` | any | `search?` | `List[AllergyCatalogResponse]` |
| `GET` | `/allergies/catalog/{catalog_id}` | any | — | `AllergyCatalogResponse` |
| `POST` | `/allergies/catalog` | scope-derived | `AllergyCatalogCreate` | `AllergyCatalogResponse` |
| `PUT` | `/allergies/catalog/{catalog_id}` | scope+ownership | `AllergyCatalogUpdate` | `AllergyCatalogResponse` |
| `DELETE` | `/allergies/catalog/{catalog_id}` | scope+ownership | — | `{message}` |
| `GET` | `/allergies/active` | any | — | `List[AllergyIntoleranceResponse]` | For `USER` returns own patients; otherwise whole-tenant active allergies. |
| `GET` | `/allergies/patient/{patient_id}` | `check_patient_access` | — | `List[AllergyIntoleranceResponse]` |
| `POST` | `/allergies/patient/{patient_id}` | `check_patient_access` | `AllergyIntoleranceCreate` | `AllergyIntoleranceResponse` |
| `PUT` | `/allergies/{allergy_id}` | `check_allergy_access` | `AllergyIntoleranceUpdate` | `AllergyIntoleranceResponse` |
| `DELETE` | `/allergies/{allergy_id}` | `check_allergy_access` | — | `{message}` |

### `vaccines` — catalog + patient immunizations

Same scope model. Mirrors the medications pattern: `VaccineCatalog` is the
CVX-coded reference (the product); `PatientImmunization` is the dose-administered
patient instance.

```
# Catalog CRUD
GET    /api/v1/vaccines/catalog?search=            → List[VaccineCatalogResponse]
GET    /api/v1/vaccines/catalog/{catalog_id}       → VaccineCatalogResponse
POST   /api/v1/vaccines/catalog                    → VaccineCatalogResponse  (scope derived from role)
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

### `notification-rules` — biomarker threshold alerts

Rules are evaluated on every observation ingestion (`fhir_service.create_observation`
→ `notification_rule_service.evaluate_and_fire`).

| Method | Path | Auth | Body / Query | Response |
|---|---|---|---|---|
| `GET` | `/notification-rules` | any | `patient_id?`, `biomarker_id?`, `enabled?`, `limit=100` (max 200), `offset=0` | `NotificationRuleListResponse` |
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
USER→user). Update/delete: `system`→SYSTEM_ADMIN, `tenant`→ADMIN/MANAGER,
`user`→creator OR ADMIN. `CatalogPermissionDenied` → 403. Every write appends a
`CatalogAuditLog` row (Phase B).

### `catalogs` — unified meta-layer

| Method | Path | Body / Query | Response | Notes |
|---|---|---|---|---|
| `GET` | `/catalogs` | — | `{types: [...]}` | Registered catalog types + UI metadata + `edge_endpoint_type` + `search_columns`. |
| `GET` | `/catalogs/relation-types` | — | `{items: [...]}` | Reference metadata for every `ConceptRelationType` (label/group/description/icon). |
| `GET` | `/catalogs/search` | `q` * (min 2), `types?`, `kind?`, `limit?` | `{results: [...]}` | Unified cross-catalog hybrid search (trigram + `websearch_to_tsquery` FTS + `ILIKE` fallback, fused via **Reciprocal Rank Fusion** k=60). `types` is a comma-separated subset; `kind` narrows the concept catalog by `ConceptKind`. |
| `GET` | `/catalogs/graph` | `types?`, `kind?`, `include_isolated?`, `limit?` | `{nodes, edges, truncated}` | Whole cross-catalog ontology graph (rootless). |
| `GET` | `/catalogs/{type}` | `search?`, `scope?`, `kind?`, `class?` (alias `class_`), `include?=relations`, `limit?`, `offset?` | `{items:[...], total}` | List items of one type. `?include=relations` annotates each item with `relation_count` + `relation_breakdown`. |
| `GET` | `/catalogs/{type}/{item_id}` | — | item dict |  |
| `POST` | `/catalogs/{type}` | item dict | item dict (`201`) | Read-only types (`concept`) → `405`. |
| `PUT` | `/catalogs/{type}/{item_id}` | item dict | item dict |  |
| `DELETE` | `/catalogs/{type}/{item_id}` | — | `{status, message}` |  |
| `POST` | `/catalogs/{type}/{item_id}/promote` | `{scope: "system"\|"tenant"\|"user"}` | item dict | Scope transition. user↔tenant needs `ADMIN`/`MANAGER`; any transition involving `system` needs `SYSTEM_ADMIN`. |
| `GET` | `/catalogs/{type}/{item_id}/history` | `limit?` | `{items: [...]}` | Audit trail (newest-first). Item must be visible to caller. |
| `GET` | `/catalogs/{type}/{item_id}/relations` | `depth=1..3`, `relation?` (CSV), `include_proposed?` | `{start, nodes, edges}` | Cross-catalog polymorphic graph traversal. |

`{type}` ∈ `biomarker | medication | allergy | anatomy | vaccine | concept`.

> **Read-only concept catalog adapter:** `POST`/`PUT`/`DELETE` on
> `/catalogs/concept` return **405** — the `ConceptCatalogAdapter` is read-only.
> All concept writes go through the `/concepts` domain endpoints.

### `biomarkers` — definitions + units + stratified reference ranges

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/biomarkers/` | any | — | `List[BiomarkerResponse]` | Global + tenant, tenant-scoped via `_tenant_scope`. |
| `GET` | `/biomarkers/{biomarker_id}` | any | — | `BiomarkerResponse` |  |
| `GET` | `/biomarkers/slug/{slug}` | any | — | biomarker dict |  |
| `POST` | `/biomarkers/` | scope-derived | `BiomarkerCreate` | `BiomarkerResponse` | `slug` must match `^[A-Za-z0-9_-]{1,80}$` (audit A1). |
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
> scope invariant). The `ConceptCatalogAdapter` under `/catalogs/concept` is
> read-only (405 on writes).

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
| `GET` | `/analytics/anomalies` | any | `biomarker_codes?`, `patient_id?` | `{anomalies}` | Statistical outlier + reference-range violation detection. |
| `GET` | `/analytics/reference-ranges` | any | — | `{slug → {min, max, unit}}` | Reads each visible biomarker's default reference range from the catalog (not a hardcoded table). Stratified ranges live under `/biomarkers/{id}/reference-ranges`. |
| `GET` | `/analytics/available-categories` | any | `patient_id?` | `{categories}` | Categories that have completed-document data for the caller/patient. |
| `GET` | `/analytics/category/{category_name}` | any | `patient_id?` | category data | Per-category rollup. |

### `telemetry` — high-frequency wearable/device data

Mobile-device sync (Health Connect / HealthKit) and per-instance integration
telemetry live here. Tenant-scoped via `current_user.tenant_id`. The TimescaleDB
telemetry split + OHLC aggregation are documented in
[TELEMETRY_AND_AGGREGATION.md](TELEMETRY_AND_AGGREGATION.md).

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/telemetry/data` | any | `TelemetrySyncPayload` `{device_id, points: [...]}` | `{uploaded, device_id, message}` |  |
| `GET` | `/telemetry/data` | any | `device_id` *, `start_date` *, `end_date` *, `metrics?` (CSV) | `{device_id, data}` |  |
| `GET` | `/telemetry/data/summary` | any | `date` *, `device_id?` | summary dict |  |
| `GET` | `/telemetry/anomalies` | any | `device_id` *, `metric` *, `period_days=30` (1..365) | `{device_id, metric, anomalies}` |  |

---

## AI

### `ai-config` — provider/model/task-assignment CRUD

> See [AI_SYSTEM.md](AI_SYSTEM.md) for the factory, model resolution, and the
> 3-table config model. Scope checks (`verify_provider_access` /
> `verify_model_access` / `check_scope_access`) are enforced on every entry point.

| Method | Path | Auth | Body / Query | Response |
|---|---|---|---|---|
| `POST` | `/ai-config/providers` | `check_scope_access` | `AIProviderCreate` | `AIProviderResponse` (`201`) |
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
| `GET` | `/ai-config/task-assignments` | any | `tenant_id?`, `user_id?`, `scope?`, `task_type?`, `is_active?=true` | `List[AITaskAssignmentResponse]` |
| `GET` | `/ai-config/task-assignments/{assignment_id}` | `check_scope_access` | — | `AITaskAssignmentResponse` |
| `PUT` | `/ai-config/task-assignments/{assignment_id}` | `check_scope_access` | `AITaskAssignmentUpdate` | `AITaskAssignmentResponse` |
| `DELETE` | `/ai-config/task-assignments/{assignment_id}` | `check_scope_access` | — | `204` |
| `GET` | `/ai-config/task-assignments/active/{task_type}` | any | `tenant_id?`, `user_id?` | `AITaskAssignmentResponse` |
| `GET` | `/ai-config/summary` | any | `tenant_id?`, `user_id?`, `scope?` | `AIConfigSummary` |
| `PUT` | `/ai-config/settings` | `check_scope_access` | `AIConfigUpdate` (query: `tenant_id?`, `user_id?`, `scope?=tenant`) | `204` |
| `GET` | `/ai-config/default-for-task/{task_type}` | any | `tenant_id?`, `user_id?` | `{provider, model?}` |

### `ai-assistance` — chatbot, Magic Fill, HITL, transcription

> The chatbot is agentic with a hard human-in-the-loop wall: the AI proposes
> actions via task cards, the user reviews them in a modal and explicitly
> approves/cancels/rejects. The AI never writes clinical data directly. See
> [AI_SYSTEM.md](AI_SYSTEM.md).

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/ai-assistance/assist` | any | `AIAssistanceRequest` | `AIAssistanceResponse` | Non-streaming assistance (form filling, icons, SVG, chat). |
| `POST` | `/ai-assistance/stream` | any | `AIAssistanceRequest` | SSE `text/event-stream` | Streaming chat. Rejects `task_type != "chat"` with 400. Stable SSE error codes: `connection`, `auth`, `rate_limit`, `timeout`, `generic`, `guard`. |
| `GET` | `/ai-assistance/sessions` | any | query: `patient_id?` | `List[ChatSessionSchema]` | Tenant-scoped. |
| `GET` | `/ai-assistance/sessions/{session_id}/messages` | any (ownership verified in service) | — | `List[ChatMessageSchema]` |  |
| `DELETE` | `/ai-assistance/sessions/{session_id}` | any (ownership verified in service) | — | `{success: true}` |  |
| `GET` | `/ai-assistance/tools` | any | query: `patient_id` *, `examination_id?` | `List[AIAssistanceToolSchema]` | Built-in tools + integration-aggregated tools (failures logged, not raised). |
| `POST` | `/ai-assistance/sessions/{session_id}/tasks/{proposal_id}/resolve` | any | `HitlResolutionRequest` (CONFIRMED/DISMISSED) | `{success, task}` | HITL resolution. Idempotent: 409 if already resolved. Does NOT perform the underlying write (frontend commits via canonical REST). |
| `POST` | `/ai-assistance/sessions/{session_id}/resume` | any | `HitlResumeRequest` | SSE `text/event-stream` | Agent continuation after one or more HITL task cards are resolved. Guards: session ownership, target message must exist + have tasks, all tasks must be terminal. |
| `POST` | `/ai-assistance/transcribe` | any | multipart: `file` | `{text, success}` | Speech-to-text. MIME allowlist (webm/ogg/mp4/mpeg/x-m4a/wav/x-wav/flac) + size guard (`AI_STT_MAX_AUDIO_BYTES`, default 20 MiB). Audio is **ephemeral** — never persisted. Resolves a `transcription` task assignment (model advertising `audio_input`). 415 / 400 / 413 / 502 error codes. |

---

## Integrations

The integrations surface lives at `/api/v1/integrations/*` (see
[INTEGRATIONS_FRAMEWORK.md](INTEGRATIONS_FRAMEWORK.md) for the discovery +
enable + sync flow, and [INTEGRATIONS_SDK.md](INTEGRATIONS_SDK.md) for building
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
| `POST` | `/integrations/instance/{integration_id}/sync` | any | `patient_id` * | `{message, metrics_synced, pulled, dropped_invalid, status, last_synced_at}` | 409 if a sync is already running; 401 on auth failure; 429 on upstream rate limit; 500 on other failures. Runs every opt-in hook the provider supports (events / exams / catalog-proposals / HITL-proposals / documents). |

### HITL proposals (review + resolve)

Catalog write proposals queued for human review by providers that opt into `supports_hitl_proposals` (see [INTEGRATIONS_SDK.md §3.12](INTEGRATIONS_SDK.md#312-hitl-proposals-human-in-the-loop-catalog-review)). Approve delegates to `catalog_proposal_service.apply_proposal` — the same write path the auto-apply hook (§3.11) uses.

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/integrations/instance/{integration_id}/proposals` | owner | `status?=proposed\|confirmed\|dismissed\|failed`, `limit?=100`, `offset?=0` | `List[IntegrationProposalResponse]` | Newest-first; deterministic `(created_at, id)` tiebreaker. |
| `GET` | `/integrations/instance/{integration_id}/proposals/{proposal_id}` | owner | — | `IntegrationProposalResponse` | 404 if proposal doesn't exist or belongs to another integration. |
| `POST` | `/integrations/instance/{integration_id}/proposals/{proposal_id}/resolve` | ADMIN+ for `action=approve`; USER can `reject`/`cancel` | `{action: "approve"\|"reject"\|"cancel", payload?: Dict, note?: str}` | `IntegrationProposalResponse` (+ `applied_entity_id`, `error`) | 409 if proposal is already in a terminal state. On `approve`, the resolver runs `apply_proposal` server-side; `applied_entity_id` is the created/updated catalog row's UUID. `payload` overrides `proposed_payload` (user edits in the review modal). |

### Notifications (per-integration preferences + actionable push)

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/integrations/notification-types` | any | — | `{integrations: [{domain, instance_name, integration_id, types: [...]}]}` | Aggregates every enabled integration's declared notification types + the caller's per-type prefs. `enabled` resolves as USER setting wins → provider's `default_enabled` → `true`. |
| `PUT` | `/integrations/{domain}/notification-types/{type_id}` | any | `{enabled: bool}` | `{status, domain, type_id, enabled}` | 404 if caller has no integration of that domain. Prefs keyed by `(domain, type_id)`, not by instance. |
| `POST` | `/integrations/{domain}/notification-action/{integration_id}/{action_id}` | any (tenant-scoped; caller must own the integration) | JSON passthrough | provider ActionResult | Dispatches a clicked action button (`type="post"`) on an integration-authored notification to `provider.handle_notification_action`. 400 if provider lacks `supports_notifications`. |

### Inbound routes (tokenless)

| Method | Path | Auth | Body | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/integrations/{domain}/webhook/{integration_id}` | HMAC via `webhook_secret` (optional) | raw Request body | `{message, metrics_synced}` | Verifies HMAC-SHA256 over raw body if `webhook_secret` is set. Supported signature headers: `X-Webhook-Signature`, `X-Webhook-Signature-256`, `X-Hub-Signature-256` (GitHub; `sha256=<hex>` prefix tolerated). Without a configured secret the integration UUID is the only credential (legacy mode). Calls `provider.handle_webhook`, maps observations, splits telemetry vs FHIR, writes `IntegrationSyncLog`, dispatches post-sync notifications. |
| `ANY` | `/integrations/{domain}/api/{integration_id}/{path:path}` | HMAC via `api_secret` (optional) | raw Request body | provider-defined | Generic two-way API proxy. With `api_secret` set, request MUST carry `X-Api-Signature` (HMAC-SHA256 of `METHOD\n<path>\n[<timestamp>\n]<raw_body>`) + optional `X-Api-Timestamp` (±5-min skew window). Without `api_secret`, UUID-only legacy mode with a logged warning (audit B8). |

See [INTEGRATIONS_SDK.md §3.9](INTEGRATIONS_SDK.md#39-notifications-event-driven-rich-actionable)
for the notification-action contract.

---

## Operations

### `task-monitor`

Tenant-scoped except `SYSTEM_ADMIN` (audit B1). Cross-tenant retries return 404.

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
| `GET` | `/export/jobs` | any | `limit?=50` | `ExportJobListResponse` |  |
| `GET` | `/export/jobs/{job_id}` | any | — | `ExportJobResponse` |  |
| `GET` | `/export/jobs/{job_id}/download` | any | — | `application/octet-stream` | FHIR Bundle `.fhir.json`, full-backup `.zip`, or catalog `.catalog.json`. |

### `import`

| Method | Path | Auth | Body / Query | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/import/backup` | `get_current_user` | multipart: `file` *, `auto_map_biomarkers?`, `use_ai_normalization?` | `ImportJobResponse` | Enqueues Celery. Accepts ZIP or bare FHIR Bundle / catalog JSON. |
| `POST` | `/import/fhir` | `get_current_user` | multipart: `file` (`.json`), `patient_id?`, `validate?`, `auto_map_biomarkers?`, `use_ai_normalization?` | dict | Synchronous FHIR Bundle import (smaller bundles). |
| `POST` | `/import/csv` | `get_current_user` | multipart: `file` (`.csv`), `patient_id?`, `delimiter?`, `has_header?` | dict | Synchronous CSV import (legacy). |
| `POST` | `/import/ocr` | `get_current_user` | multipart: `file`, `patient_id?`, `model_name?`, `api_base?`, `extract_tables?` | dict | OCR import (legacy). |
| `GET` | `/import/jobs` | any | `limit?=50` | `ImportJobListResponse` | List import jobs for the caller's tenant, newest first. |
| `GET` | `/import/jobs/{job_id}` | any | — | `ImportJobResponse` | Status + `restore_result` (created/updated counts, `manifest_verified`, `fhir_validated`). |

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

**19 registered resource types:** Patient, Observation, Condition (← ClinicalEvent),
EpisodeOfCare (← ClinicalEvent journey view), Encounter (← ExaminationModel),
AllergyIntolerance, MedicationStatement, MedicationRequest (both ← Medication
via `intent` discriminator), Medication (← MedicationCatalog, read-only),
Immunization (patient dose records; REST CRUD at `/vaccines/*`),
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
their HTTP statuses; access-check helpers in `app/services/access.py` raise these.

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
| 405 | Method Not Allowed (read-only catalog adapter writes) |
| 409 | Conflict (`ConflictError` — e.g. catalog promote permission denied; sync already running; HITL task already resolved) |
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
- [INTEGRATIONS_FRAMEWORK.md](INTEGRATIONS_FRAMEWORK.md) + [INTEGRATIONS_SDK.md](INTEGRATIONS_SDK.md) — integrations discovery + SDK
- [EXPORT_IMPORT.md](EXPORT_IMPORT.md) — backup format / scopes / restore

When running locally, visit **http://localhost:8000/docs** (Swagger UI) or
**http://localhost:8000/redoc** for the always-up-to-date OpenAPI rendering of
every route, request body, response model, and query parameter.
