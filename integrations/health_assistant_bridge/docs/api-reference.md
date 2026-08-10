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

## See also

- [Overview](overview.md)
- [AI Ontology Mapping](mapping.md) — the `/map` workflow in depth.
- [Authentication](authentication.md) — the HMAC gate on `/map` and `/sync`.
- [Troubleshooting](troubleshooting.md).