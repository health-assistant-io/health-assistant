# API Reference

The bridge exposes **push** endpoints (`/status`, `/map`, `/sync`) and
**read/management** endpoints (observations, biomarkers, examinations, and
their documents) under one two-way API proxy:

```
{base_url}/api/v1/integrations/health_assistant_bridge/api/{integration_id}/{path}
```

`{integration_id}` is the bridge instance UUID (part of the URL, bound to one
patient). `{path}` selects the endpoint. All request/response bodies are JSON.

When an `api_secret` is configured, `/map`, `/sync`, and every read/management
path require an HMAC signature — see [Authentication](authentication.md).
`/status` is **never** signed (it is the connectivity + SDK-discovery probe).

---

## GET /status

Connectivity probe + cursor + SDK-version discovery. Safe to call frequently.

**Headers:** none required (never signed).

**Response (200):**

```json
{
  "status": "active",
  "integration_id": "00000000-0000-0000-0000-000000000000",
  "last_synced_at": "2026-07-24T12:00:00Z",
  "cursor": "2026-07-24T12:00:00Z",
  "latest_sdks": { "python": "1.2.0", "ts": "1.2.0" }
}
```

| Field | Type | Notes |
|---|---|---|
| `status` | string | `"active"` when the instance is healthy. |
| `integration_id` | string (uuid) | The instance UUID. |
| `last_synced_at` | string\|null | ISO 8601 of the last successful `/sync`. `null` before the first sync. |
| `cursor` | string\|null | The sync cursor the client last pushed. Use this to bound incremental scraping. |
| `latest_sdks` | object\|null | The Python/TS SDK versions the server advertises (from `manifest.json`). Compare against your client's `SDK_VERSION` and warn the user on mismatch. |

---

## POST /map

Ask the Health Assistant AI to resolve raw metric names to standardized biomarker definitions. **Does not persist anything** — the client must show the proposed mappings to the user, confirm them, and cache them locally before the next `/sync`. See [AI Ontology Mapping](mapping.md).

**Request:**

```json
{
  "unmapped_metrics": [
    { "name": "Natrium (Na)", "code": null },
    { "name": "HCT", "code": null }
  ]
}
```

**Response (200):**

```json
{
  "mappings": [
    {
      "original_name": "Natrium (Na)",
      "action": "map_to_existing",
      "existing_biomarker_id": "uuid-of-sodium-record",
      "new_biomarker_name": null,
      "new_biomarker_code": null,
      "new_biomarker_coding_system": "loinc"
    },
    {
      "original_name": "HCT",
      "action": "create_new",
      "existing_biomarker_id": null,
      "new_biomarker_name": "Hematocrit",
      "new_biomarker_code": "20570-8",
      "new_biomarker_coding_system": "loinc"
    }
  ]
}
```

| `action` | Meaning | What the client does |
|---|---|---|
| `map_to_existing` | The LLM found a matching `BiomarkerDefinition` in the patient's catalog. | Use `existing_biomarker_id` on subsequent `/sync` records for this metric. |
| `create_new` | No existing match — the LLM proposes a new definition with a LOINC/SNOMED code. | Show the proposal to the user; on confirm, record `new_biomarker_*` and (optionally) have the backend create it via a `/sync` record carrying those fields. |

**Errors:** `400` when the AI service is unavailable or the payload is malformed; `422` on a Pydantic validation failure.

---

## POST /sync

Push observations and/or examinations into Health Assistant. The endpoint is **idempotent** — but only when you supply the right dedup keys (see below).

**Request — flat records (wearables / telemetry):**

```json
{
  "client_version": "1.2.0",
  "source_system": "smartwatch_extension",
  "cursor": "2026-07-24T12:00:00Z",
  "records": [
    {
      "type": "quantitative",
      "code": "8867-4",
      "coding_system": "loinc",
      "name": "Heart Rate",
      "value": 75.0,
      "unit": "bpm",
      "timestamp": "2026-07-24T11:00:00Z",
      "performer": "Apple Watch"
    }
  ]
}
```

**Request — grouped examinations (lab reports):**

```json
{
  "client_version": "1.2.0",
  "source_system": "health_portal_extension",
  "cursor": "2026-07-24T12:00:00Z",
  "examinations": [
    {
      "id": "report-12345",
      "date": "2026-07-24T00:00:00Z",
      "lab_name": "City General Hospital Laboratory",
      "category": "Biochemical Tests",
      "diagnoses": ["Hypertension"],
      "records": [
        {
          "type": "quantitative",
          "biomarker_id": "uuid-of-sodium-record",
          "code": "2951-2",
          "coding_system": "loinc",
          "name": "Sodium",
          "value": 145.0,
          "unit": "mmol/L",
          "timestamp": "2026-07-24T00:00:00Z",
          "reference_range": { "low": 137.0, "high": 147.0 },
          "interpretation": "INSIDE_LIMIT",
          "performer": "City General Hospital Laboratory"
        }
      ]
    }
  ]
}
```

**Response (200):**

```json
{
  "success": true,
  "metrics_synced": 2,
  "message": "Data synchronized successfully"
}
```

| Field | Type | Notes |
|---|---|---|
| `success` | bool | `true` on a clean ingest. |
| `metrics_synced` | int | Count of observations + telemetry records actually written. |
| `message` | string | Human-readable summary. |

**Errors:** `400` on a malformed payload, a bad/missing HMAC signature (when `api_secret` is set), or a validation failure. The error body carries `success: false` + a human-readable `error` string.

### The Universal Data Contract

A `record` is one biomarker reading:

| Field | Type | Required | Notes |
|---|---|---|---|
| `type` | `"quantitative"` \| `"categorical"` | ✅ | `value` vs `value_string`. |
| `biomarker_id` | string (uuid)\|null | ❌ | The resolved biomarker definition id from `/map`. Strongly recommended — without it the backend resolves by code/name heuristics. |
| `code` | string\|null | ❌ | The LOINC/SNOMED/custom code. |
| `coding_system` | string | ❌ | `loinc`, `snomed`, or `custom` (default). |
| `name` | string | ✅ | Display name from the portal. |
| `value` | float\|null | ❌ | Required for `type=quantitative`. |
| `value_string` | string\|null | ❌ | Required for `type=categorical`. |
| `unit` | string\|null | ❌ | Unit (UCUM). |
| `timestamp` | string\|null | ❌ | ISO 8601 of the reading. Defaults to now. |
| `reference_range` | `{low, high}`\|null | ❌ | Reference range (FHIR `referenceRange`). |
| `interpretation` | string\|null | ❌ | e.g. `INSIDE_LIMIT`, `HIGH`. |
| `performer` | string\|null | ❌ | Display name of the source (lab, device). |

An `examination` groups records under a lab report / visit:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string\|null | **strongly recommended** | The upstream's stable report id — **the dedup key**. Re-syncing the same `id` is a no-op. |
| `date` | string\|null | ❌ | The report/visit date. |
| `lab_name` | string\|null | ❌ | Maps to an Organization; created if missing. |
| `notes` / `patient_notes` | string\|null | ❌ | Clinician / patient notes. |
| `category` | string\|null | ❌ | Free text → resolved to a category concept. |
| `diagnoses` | string[] | ❌ | Problem list for this exam. |
| `impressions` | string\|null | ❌ | Radiology/clinical impressions. |
| `records` | `ClientRecord[]`\|null | ❌ | The nested biomarkers. |

### Cursor handling

The `cursor` in the request body is **opaque to the server** — it's stored as-is in `_sync_state.last_timestamp` and echoed back by `/status`. The client owns its meaning (typically the last scraped timestamp). Send the freshest cursor on each `/sync` so the next `/status` reports it. Stuck? Use the **Reset Sync Cursor** custom action (see [Troubleshooting](troubleshooting.md)).

### Deduplication & Idempotency

- **Grouped examinations**: dedup keys on `(tenant, patient, integration_id, examination.id)`. **Always pass the upstream's stable `id`** (the portal's `reportId` / `encounterId`) — without it every sync creates a duplicate exam. A re-pull of the same upstream file is a no-op: the exam and its nested records are skipped.
- **Flat records**: rely on backend heuristics (matching timestamps + values) — weaker. For structured lab data, prefer grouped examinations with explicit ids.

## Read & management paths

These let a headless client (mobile app, CLI) **read** the bound patient's data
and **create** examinations / upload documents through the same single
connection identity. All are HMAC-gated when `api_secret` is set and are
**scoped to the patient the instance is bound to** — the actor resolved from
the integration carries the owner's role, so patient isolation is an explicit
per-path filter, not an automatic consequence of the credential.

### Response envelope (reads)

```json
{ "data": [ ... ], "cursor": "...|null", "cached_at": "2026-08-09T12:00:00Z" }
```

| Endpoint | Purpose |
|---|---|
| `GET /observations/latest?limit=` | **Latest value per biomarker** (dashboard cards) — FHIR observations and telemetry (heart rate / steps / SpO₂) merged, one row per biomarker, newest first. |
| `GET /observations?biomarker=&since=&until=&limit=` | Time series for one biomarker (charts). `biomarker` is the LOINC/SNOMED code **or slug**; `since`/`until` are ISO 8601; `limit` ≤ 500. Telemetry-flagged biomarkers (heart rate `8867-4`, steps `55423-8`, SpO₂ `59408-5`, …) are served from the telemetry store; all other biomarkers from FHIR observations. |
| `GET /biomarkers?limit=` | The patient's biomarker catalog (id, name, slug, code, coding system, unit, is_telemetry, reference range, value_type). Includes tenant-scoped + global (`tenant_id IS NULL`) definitions, ordered by name. |
| `GET /examinations?limit=` | The patient's examinations (newest first; `limit` ≤ 200). |
| `GET /examinations/{id}` | One examination's detail (notes, status, diagnoses, impressions). |
| `POST /examinations` | Create an examination (offline-friendly). Idempotent on the client `id`. |
| `GET /examinations/{id}/documents` | Documents attached to an examination. |
| `POST /examinations/{id}/documents` | Upload a document for an examination (base64 JSON). Idempotent on the client id. |
| `GET /documents?examination_id=&limit=` | Patient-wide document list (newest first; `limit` ≤ 500, default 100). Optional `?examination_id=` narrows to one exam. |
| `GET /documents/{id}` | One document's metadata (filename, status, progress, `content_type`, `file_size`, `examination_id`). |
| `GET /documents/{id}/content` | The document's binary content (response body is the file's bytes; `Content-Type` from the filename). |
| `GET /documents/{id}/preview?page=` | A JPEG page render for PDF/DICOM, or the stored bytes for images. `?page=` selects a page (default 0; clamped). `X-Total-Pages` + `X-Current-Page` headers carry the pagination metadata. |

**Observation item shape.** Every row in `/observations` and `/observations/latest`
carries the same fields regardless of source (FHIR or telemetry), so a client
never branches on source. Key fields: `effective_datetime`, `code` (FHIR
`{coding:[{system, code}]}`), `raw_value`/`normalized_value`/`normalized_unit`,
`reference_range` (**always the flat `{low, high}` object** — FHIR-list shaped
ranges are normalized server-side), `interpretation`, `relative_score`,
`biomarker_id`, `biomarker_slug`, `biomarker_value_type`
(`quantity`\|`state`), and for STATE biomarkers `value_string` /
`value_codeable_concept`. Telemetry rows synthesize the same shape from the
biomarker definition (slug → code/unit/range, `relative_score` computed from the
definition's reference range).

**Errors** follow the bridge convention: `ValueError` → HTTP 400 with a
human-readable `error` string (e.g. an `id` belonging to a different patient
returns *"Examination not found for this patient."*); unknown paths → HTTP 400.

### POST /examinations

```json
{
  "id": "client-generated-uuid-or-stable-id",
  "date": "2026-08-08T00:00:00Z",
  "lab_name": "City General Hospital Laboratory",
  "category": "Biochemical Tests",
  "notes": "Fasting panel",
  "patient_notes": "Minor fatigue",
  "diagnoses": ["Hypertension"],
  "impressions": null
}
```

**Response (200):** `{ "id": "<server uuid>", "external_id": "<your id>" }`.

**Idempotency:** the request flows through `examination_service.create_examination`
with `source_integration_id` + `external_id` = your `id`, so re-sending the same
`id` after a network blip returns the same examination — no duplicate.

### POST /examinations/{id}/documents

Upload a document as base64 JSON. The `id`/`client_request_id` makes it
idempotent via the existing document dedup key
`(tenant, patient, source_integration_id, external_id)` — a re-upload of the
same id returns the same row (no re-write, no OCR re-dispatch).

```json
{
  "id": "client-generated-id",
  "filename": "panel.pdf",
  "content_type": "application/pdf",
  "data": "<base64-encoded bytes>",
  "include_in_extraction": false
}
```

**Response (200):** `{ "id", "external_id", "filename", "status", "progress" }`.

**Limits:** the decoded payload must not exceed **25 MiB** (over → HTTP 400).
The filename extension must be on the upload allowlist (`pdf`, `png`, `jpg`,
`tiff`, `docx`, … — `svg`/`html`/`xml`/`js` are blocked). Set
`include_in_extraction: false` for headless uploads that should not trigger the
OCR/NLP pipeline.

### Document list + content paths

`GET /documents` (and `GET /documents/{id}`) return items with this shape:

```json
{
  "id": "<uuid>",
  "filename": "panel.pdf",
  "status": "uploaded",
  "progress": 0,
  "external_id": "client-id-or-null",
  "created_at": "2026-08-12T...",
  "content_type": "application/pdf",
  "file_size": 1024,
  "examination_id": "<uuid>|null"
}
```

`content_type` is MIME-guessed from the filename (null when unknown); `file_size`
is bytes on disk (null when the file is missing). Both are best-effort metadata
— the canonical content lives at `GET /documents/{id}/content`.

`GET /documents/{id}/content` returns the stored file's bytes as the response
body, with `Content-Type` from the filename. The bridge's HMAC credential
authenticates the call; `_bound_document` enforces that the doc belongs to the
bound patient (cross-patient → HTTP 400 *"Document not found for this patient."*).
Soft-deleted docs (`deleted_at IS NOT NULL`) are excluded everywhere.

`GET /documents/{id}/preview?page=N` returns:
- For images (`.png`/`.jpg`/`.jpeg`/`.webp`/`.gif`/`.bmp`): the stored bytes
  with their guessed MIME (no conversion).
- For PDF / DICOM: a JPEG page render via the same `convert_to_images`
  pipeline the PWA's document preview uses. `?page=N` selects the page
  (default 0; clamped to `[0, len(images))`). `X-Total-Pages` +
  `X-Current-Page` response headers expose the count.

**Kotlin SDK:** `BridgeClient.requestBytes(method, path)` materialises the body
of any signed request as `ByteArray` (throws `BridgeException` on non-2xx).
`getDocumentContent(id)` and `getDocumentPreview(id, page?)` are the typed
conveniences for the two binary paths.

### Clinical-record reads (Phase 3)

| Endpoint | Purpose |
|---|---|
| `GET /medications?limit=` | The bound patient's medication instances (newest by `start_date` desc). |
| `GET /allergies?active=&limit=` | The bound patient's allergy-intolerance instances. `active=true` (default) → only `clinical_status = ACTIVE`; `active=false` → full history. |
| `GET /vaccines?limit=` | The bound patient's immunizations (newest by `administered_at` desc). |
| `GET /clinical-events?status=&limit=` | Flat list of the bound patient's events (no nested relations). `?status=ACTIVE` filters to currently-active. |
| `GET /clinical-events/{id}` | Full nested detail (`to_dict()` shape — `type_details`, `examinations[]`, `observations[]`, `anatomy_links[]`, `occurrences[]`). |
| `GET /doctors?limit=` | The bound owner's tenant-wide doctor address book (doctors are not patient-scoped). |

Each list item mirrors the matching PWA response schema fields (id, status,
code/JSONB payload, dates, audit). All patient-scoped paths filter by the
integration's bound patient + tenant + `deleted_at IS NULL`. Cross-patient →
HTTP 400 *"not found for this patient."*

**Kotlin SDK:** typed readers `getMedications()`, `getAllergies(active)`,
`getVaccines()`, `getClinicalEvents(status)`, `getClinicalEventRaw(id)` (the
detail shape is rich — decode as needed), `getDoctors()`. All return
`ReadEnvelope<T>`.

### Mutations + extraction status (Phase 4)

| Endpoint | Purpose |
|---|---|
| `DELETE /examinations/{id}` | Hard-delete an examination + its documents (file unlink) + defensively clean orphan observations/medications. Patient-scoped. |
| `DELETE /documents/{id}` | Hard-delete a document (file unlink + row delete + observation cleanup). Patient-scoped. |
| `POST /documents/{id}/extract` | Trigger the OCR/NLP extraction pipeline (best-effort Celery dispatch — broker-down is swallowed). Returns `{job_id, message}`. |
| `GET /documents/{id}/extract/status` | Live `{id, status, progress, error_message}` for a document's extraction. |
| `GET /examinations/{id}/status` | Exam-level extraction state plus the per-document status array. |
| `GET /examinations/{id}/logs` | TaskLog entries for the exam + its documents (INFO/WARN/ERROR, stages, data payloads). |

**Kotlin SDK:** use `BridgeClient.requestText(method, path)` (or `request` for
the raw `HttpResponse`) for these — they return either a JSON dict (DELETE,
POST extract, GET status) or a JSON array (GET logs) that the app decodes
inline. Typed wrappers may be added in a future phase.

**Notes:**
- DELETEs are **hard** (matching the PWA endpoints). The bound patient filter
  rejects cross-patient attempts *before* any side effect.
- `POST /documents/{id}/extract` is idempotent — re-triggering an
  already-extracted doc re-runs OCR.
- The `error_message` on extraction status surfaces broker failures when the
  Celery dispatch couldn't even queue the task.

### Unified delta — `GET /changes` (Phase 5)

```
GET /changes?since=<ISO>&types=<csv>&limit=<int>
```

One round-trip replaces N per-type reads; powers the app's pull-to-refresh +
the 15-min wake-up poll.

- **`since`** (ISO 8601): the cursor from the previous poll's response. Default:
  now − 7 days (bounded first-pull window).
- **`types`** (CSV): subset of `medications,allergies,vaccines,clinical_events,documents,examinations`.
  Default: all six. Unknown names → HTTP 400.
- **`limit`** (int): per-type cap, max 2000, default 500.

**Response:**
```json
{
  "data": {
    "medications": [{"id": "...", "updated_at": "...", "status": "ACTIVE", "code_text": "...", "start_date": "..."}],
    "allergies":    [{"id": "...", "updated_at": "...", "clinical_status": "ACTIVE", "code_text": "..."}],
    "vaccines":     [{"id": "...", "updated_at": "...", "status": "completed", "administered_at": "..."}],
    "clinical_events": [{"id": "...", "updated_at": "...", "status": "ACTIVE", "title": "...", "onset_date": "..."}],
    "documents":    [{"id": "...", "updated_at": "...", "filename": "...", "status": "...", "examination_id": "..."}],
    "examinations": [{"id": "...", "updated_at": "...", "examination_date": "...", "extraction_status": "..."}]
  },
  "cursor": "<max updated_at across this batch>|null",
  "cached_at": "...",
  "since": "<the since the server used>"
}
```

**Cursor semantics:** `cursor` advances to `max(updated_at)` across the batch
when at least one row matched. The client uses the returned cursor as the next
poll's `since`. A `null` cursor means nothing changed — the client should
re-use the same `since` next time.

**Limitation:** deletions (soft or hard) are **not** represented in `/changes`.
A soft-deleted row updates `deleted_at` but the query filters on
`deleted_at IS NULL`; a hard-deleted row is gone. The client must periodically
do a full re-sync to discover deletions.

**Kotlin SDK:** `BridgeClient.getChangesRaw(since, types, limit)` returns the
raw JSON string (the response shape diverges from `ReadEnvelope<List<T>>` —
decode as a `JsonObject` with per-type arrays).

### Clinical-record mutations (Phase 6)

POST/PUT/DELETE for the resources Phase 3 made readable. Every
patient-scoped path re-verifies the tenant + patient via a `_bound_*`
loader *before* any side effect — a cross-patient attempt fails with
HTTP 400 before the service is called. Creates accept a client-supplied
`id` (or `client_request_id`) forwarded as `external_id`; re-push is a
no-op (the partial unique index `(source_integration_id, external_id)`
catches the duplicate).

```
POST    /medications                         create (idempotent on external_id)
PUT     /medications/{id}                    update fields
DELETE  /medications/{id}                    soft-delete (filtered out of reads)

POST    /allergies                           create
PUT     /allergies/{id}                      update
DELETE  /allergies/{id}                      soft-delete

POST    /vaccines                            create
PUT     /vaccines/{id}                       update
DELETE  /vaccines/{id}                       soft-delete

POST    /clinical-events                     create (returns to_dict() — rich + nested)
PUT     /clinical-events/{id}                update (returns to_dict())
DELETE  /clinical-events/{id}                soft-delete (sets deleted_at)
POST    /clinical-events/{id}/occurrences    log a recurrence (returns updated event)

POST    /doctors                             create (tenant-scoped, NOT patient-scoped)
PUT     /doctors/{id}                        update
DELETE  /doctors/{id}                        hard-delete
```

**Payload shape:** the create/update body is the resource's own JSON dict
(the same shape the corresponding Phase 3 read returns). Schema validation
happens inside the service layer; bad input → HTTP 400. Enum values: see
the Phase 3 read shapes — `AllergyCategory`/`AllergyCriticality`/
`AllergyClinicalStatus` are uppercase (`MEDICATION`, `HIGH`, `ACTIVE`);
`ImmunizationStatus` is lowercase (`completed`, `entered-in-error`,
`not-done`); `Medication.status` is uppercase (`ACTIVE`/`INTENDED`/…).

**Delete ack:** `{"id": "...", "deleted": true, "message": "..."}`.

**Kotlin SDK:** one `createX`/`updateX(id, payload)`/`deleteX(id)` per
resource (payload is a `JsonObject` built via `buildJsonObject`); clinical
events have a `Raw` variant because their `to_dict()` is rich + nested.

### Notification inbox + preferences + triggers (Phase 7)

Notifications are addressed to the **integration owner** (the user who
onboarded the connection), NOT to the bound patient. A user with multiple
patients (child + elderly parent) sees one inbox. Every handler keys on
`integration.user_id` + `integration.tenant_id`; `_bound_patient_id` is
NOT called (the one exception is `GET /notifications/triggers`, which
filters biomarker-threshold rules by the bound patient).

```
GET    /notifications/inbox                  owner inbox
       ?status=&category=&source=&patient_id=&limit=&offset=
GET    /notifications/unread-count           {"unread_count": N}
PATCH  /notifications/{recipient_id}/read    {"status": "success"}
PATCH  /notifications/{recipient_id}/dismiss {"status": "success"}
POST   /notifications/read-all               {"status": "success", "marked_read": N}

GET    /notifications/preferences            full preferences hub listing
PUT    /notifications/preferences/{kind_id}  {"enabled": true|false}
       body: {"enabled": bool}

GET    /notifications/triggers               biomarker-threshold rules (bound patient)
POST   /notifications/triggers               create (rule_type required)
DELETE /notifications/triggers/{id}
```

`status`/`category`/`source` filter values follow the enum string values
(`status` is the lowercase `RecipientStatus`: `unread`/`read`/`dismissed`;
`category` is the lowercase `NotificationCategory`: `reminder`/`alert`/
`system`/…; `source` is the uppercase `NotificationSource`: `SYSTEM`/
`INTEGRATION`/…). `kind_id` is the canonical preferences id (`channel:PUSH`,
`source:INTEGRATION`, `integration:{iid}:{tid}`).

Medication / appointment reminders are NOT here — those live on the device
(the mobile app's WorkManager), so they fire even when the server is
unreachable. The bridge surfaces only server-side concepts (threshold rules
that need the data the server holds).

**Kotlin SDK:** `getNotificationInbox`, `getUnreadNotificationCount`,
`markNotificationRead/dismissed`, `markAllNotificationsRead`,
`getNotificationPreferences`, `setNotificationPreference(kindId, enabled)`,
`getNotificationTriggers`, `createNotificationTrigger(payload)`,
`deleteNotificationTrigger(id)`.

### Native push device registration (Phase 8)

One row per (user, device) registered for native push. Sibling to the
PWA's Web Push / VAPID subscriptions. The mobile app registers a device
on first run after onboarding; the dispatcher fans out to every active
device on each emitted notification. UnifiedPush default (self-hostable),
FCM optional per-device.

```
POST    /notifications/register-device       register / re-register (upsert)
DELETE  /notifications/register-device/{device_id}  soft-deactivate (sign-out)
GET    /devices                              "Where am I signed in" list (masked)
```

**Register payload:** `device_id` (client-stable per-install id),
`platform` (`unifiedpush` | `fcm`), `endpoint_url` (UnifiedPush
distributor URL or FCM token), optional `encryption_pubkey`,
`app_version`, `user_agent`. Re-registering the same `(user, device)`
upserts — useful when the user picks a new UnifiedPush distributor.

**List response:** endpoint URLs are masked (`https://ntfy.example…`);
the bridge never echoes a reusable credential back. The dispatch task
reads the raw column server-side.

**Channel preference is honored upstream:** `notification_service.emit`
drops the PUSH `NotificationDelivery` row when the user has muted PUSH
for the notification's kind, so the dispatch task naturally skips.

**Kotlin SDK:** `registerDevice(DeviceRegistration(...))`,
`unregisterDevice(deviceId)`, `listDevices()`.

## See also

- [Overview](overview.md)
- [AI Ontology Mapping](mapping.md) — the `/map` workflow in depth.
- [Authentication](authentication.md) — the HMAC gate on `/map` and `/sync`.
- [Troubleshooting](troubleshooting.md).