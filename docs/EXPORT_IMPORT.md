# Export & Import (Backup)

Health Assistant can export and re-import data as **backup files**, at three scopes and in three formats. Exports are FHIR R4B-conformant and integrity-checked; imports are validated with [`fhir.resources`](https://pypi.org/project/fhir.resources/) and upserted by natural key with automatic id remapping across tenants.

> **Files**: `backend/app/services/{export_service,import_service,fhir_converter}.py`, `backend/app/api/v1/endpoints/{export,import_data}.py`, `backend/app/workers/tasks.py` (`export_backup`, `import_backup`), `backend/app/models/export_import_job.py`, `backend/app/schemas/backup.py`.

---

## 1. Scopes

Scopes mirror the FHIR [Bulk Data Access](https://hl7.org/fhir/uv/bulkdata/) levels. Each scope is stored on the export job as a **SMART-compatible scope string** (`smart_scope`) — these are vocabulary-only claims enforced by the platform's existing RBAC + tenant isolation; no separate OAuth2 authorization server is required.

| Scope | SMART claim | Who can request | Coverage |
|---|---|---|---|
| `patient` | `patient/*.rs` | `USER` (single own patient), `MANAGER`, `ADMIN`, `SYSTEM_ADMIN` | One patient (`patient_ids` required, single id for `USER`) |
| `group` | `system/*.rs` | `MANAGER`, `ADMIN`, `SYSTEM_ADMIN` | Multiple patients (`patient_ids` required) |
| `system` | `system/*.cruds` | `ADMIN`, `SYSTEM_ADMIN` | Whole tenant (`patient_ids` ignored) |

`SYSTEM_ADMIN` bypasses all role checks (existing behaviour).

---

## 2. Export types

| Type | File | Contents |
|---|---|---|
| `fhir_only` | `<job_id>.fhir.json` | A single **FHIR R4B `Bundle`** (`type: transaction`). Portable to any FHIR server. Resources: `Patient`, `Observation`, `MedicationStatement`, `AllergyIntolerance`, `DiagnosticReport`, `Organization`, `Practitioner`, `DocumentReference` (metadata only — no inline binaries). No telemetry, no integrations, no AI config. |
| `full_backup` | `<job_id>.zip` | A **BagIt-style ZIP** containing the FHIR Bundle plus non-FHIR sidecars, raw document files, and a SHA256 manifest. Re-importing this ZIP restores the tenant/patient. |
| `catalog_only` | `<job_id>.catalog.json` | Biomarker/unit definitions, medication/allergy catalogs + clinical-event-type `metadata_schema` definitions. Cross-tenant portable (use to seed a new deployment). |

### ZIP layout (`full_backup`)

```
<job_id>.zip
├── manifest.json              # BackupManifest: schema_version, exported_at, tenant_id, fhir_version, scope, export_type, smart_scope, counts{}, files[]{path,sha256,size}, options, notes
├── manifest-sha256.txt        # <sha256>  <path>  (BagIt-style, one line per file)
├── bag-info.txt               # tenant id, job id, export timestamp, schema/FHIR version, smart scope
├── fhir/
│   └── bundle.json            # the FHIR R4B transaction Bundle (clinical core)
├── nonfhir/
│   ├── examinations.json
│   ├── clinical_events.json
│   ├── clinical_event_types.json
│   ├── biomarker_definitions.json
│   ├── medication_catalog.json
│   ├── allergy_catalog.json
│   ├── documents.json         # document metadata + _archive_path mapping
│   ├── telemetry.json         # group/system scope only (see §5)
│   ├── integrations.json      # user_integrations incl. encrypted user_config + OAuth tokens
│   ├── notification_triggers.json
│   └── ai_config.json         # system scope + include_ai_config only (restore not supported in v1)
└── documents/                 # raw uploaded files (not base64), referenced by documents.json
    └── <doc_id>.<ext>
```

---

## 3. Endpoints

All under `/api/v1`, JWT-auth required.

### Export

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/export` | Create an export job (body: `BackupRequest`). Enqueues `export_backup` Celery task. Returns `ExportJobResponse`. |
| `GET` | `/export/jobs` | List export jobs for the current tenant. |
| `GET` | `/export/jobs/{job_id}` | Get job status (`PENDING` → `PROCESSING` → `COMPLETED`/`FAILED`/`PARTIAL`). |
| `GET` | `/export/jobs/{job_id}/download` | Download the generated file (JWT-gated, tenant-scoped). |

**`POST /export` body:**
```json
{
  "scope": "patient",
  "export_type": "full_backup",
  "patient_ids": ["<uuid>"],
  "include_documents": true,
  "include_telemetry": true,
  "include_integrations": true,
  "include_ai_config": false
}
```

### Import

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/import/backup` | Upload a ZIP **or** a bare `bundle.json`/`catalog.json`. Enqueues `import_backup` Celery task. Returns `ImportJobResponse`. |
| `GET` | `/import/jobs/{job_id}` | Get backup-import job status + `restore_result` (created/updated counts, manifest verification). |
| `POST` | `/import/fhir` | Synchronous FHIR Bundle import (smaller bundles, no job tracking). |
| `POST` | `/import/csv` | CSV import (legacy). |
| `POST` | `/import/ocr` | OCR extraction (legacy). |

**`POST /import/backup`** — multipart `file` field. The server detects the format:
- ZIP → verifies manifest SHA256, restores FHIR bundle + sidecars + documents.
- Bare JSON with `resourceType: "Bundle"` → restores as a FHIR transaction.
- Bare JSON with `units`/`biomarkers` keys → upserts the ontology catalog.

---

## 4. FHIR conversion & validation

- **Library**: `fhir.resources==8.2.0` (R4B sub-package, Pydantic-v2-native). Provides *structural* validation (types, cardinality, JSON shape) without a terminology server.
- **`services/fhir_converter.py`** converts ORM `to_dict()` output (snake_case + app extensions) to canonical FHIR R4B JSON and back:
  - App-only fields (`tenant_id`, `user_id`, `biomarker_id`, `normalized_value`, `dashboard_layout`, `org_type`, …) are stripped from the FHIR output.
  - `Patient.mrn` → `identifier[{system: "urn:healthassistant:mrn"}]`.
  - Enum casing normalized (`Gender.FEMALE` → `"female"`; `MedicationStatus.ACTIVE` → `"active"`).
  - `Medication` ORM → FHIR `MedicationStatement`; `DoctorModel` → FHIR `Practitioner`.
  - Cardinality fixes for R4B: `Organization.type`, `DiagnosticReport.conclusionCode`/`presentedForm`/`category` wrapped as lists; `Observation.comment` → `note`; `MedicationStatement.timing.repeat.timeOfDay` padded to `HH:MM:SS`. (`Observation.interpretation` is now stored natively as the canonical FHIR `0..*` CodeableConcept list — JSONB column — so export is a pass-through; `Observation.component` is also stored + exported natively.)
- Every exported resource carries `meta.versionId`, `meta.lastUpdated`, `meta.source`, and a provenance `meta.tag` (`{system: "https://healthassistant.local/fhir/export", code: "ha-export"}`).
- On import, each entry is validated with `fhir.resources.R4B.get_fhir_model_class(rt).model_validate(...)`; invalid entries are recorded as errors and skipped. All 15 registered FHIR resource types are importable (`Patient`, `Observation`, `MedicationStatement`, `MedicationRequest`, `AllergyIntolerance`, `DiagnosticReport`, `Organization`, `Practitioner`, `Condition`, `Encounter`, `Device`, `Communication`, `Provenance`; `DocumentReference` is intentionally skipped on import with a warning since binary content lives in the `documents/` sidecar).

---

## 5. Data integrity

- **SHA256 manifest**: `manifest.json` lists every file with its `sha256` + `size`; `manifest-sha256.txt` is the BagIt-style `"<sha256>  <path>"` duplicate. On import, each file's hash is recomputed and compared → `manifest_verified` flag on the result.
- **Provenance tag**: every FHIR resource carries the `ha-export` `meta.tag`; importers can check it to confirm origin.
- **Round-trip**: export serializes via `fhir.resources`; import validates with the same R4B classes, so the schema is shared. Pydantic field equality + hash match is the integrity guarantee.

---

## 6. Restore behaviour & caveats

- **Transaction verb routing**: the import path honors each entry's `request.method` (FHIR transaction Bundles). `PUT Type/<id>` updates an existing same-tenant row or creates one **with the supplied id** (not a random UUID); `POST` + `ifNoneExist` performs a conditional create (skipped when a match exists — Patient `identifier` → `mrn` lookup today; unsupported forms log a warning and create rather than silently treating "couldn't match" as "no match"); `DELETE` soft-deletes (idempotent on missing id). Entries with no `request` block default to `POST` (create-new) — the historical behaviour. This makes round-tripping third-party EHR / SMART transaction Bundles preserve referential integrity.
- **Upsert by id with cross-tenant remapping**: for each FHIR resource, if the imported `id` already exists **in the same tenant** → update in place; if it exists in a **different tenant** → generate a new UUID, record the remap, **and surface a warning** in the ImportJob result (e.g. `"Patient/<id> already exists in tenant <other>; created new with id <new>"`) so collisions are visible. All reference-bearing fields (`subject`/`patient`/`performer`/`partOf`/`context`/`encounter`/`author`/`device`/`specimen`/`sender`/`recipient`) are rewritten through the remap dict; bare `urn:uuid:` references are routed to the correct resource type via a `FIELD_HINT_TO_TYPE` map + bundle look-ahead (ambiguous hints like `sender`/`recipient` are resolved by finding the referenced resource's type in the bundle).
- **Provenance per entry**: every created/updated/deleted entry records a `Provenance` resource (best-effort — never aborts the import) with the `CREATE`/`UPDATE`/`DELETE` activity code, the importing user as the agent, and an `ImportJob/<id>` reference in `entity_inputs` so bulk-import Provenance is distinguishable from facade-write Provenance.
- **`BundleRestoreResult`**: `ImportService.restore_fhir_bundle` returns a dataclass with `created`/`updated`/`deleted`/`skipped`/`errors`/`warnings`/`id_remap` fields (attribute access). *Breaking change* (was a 5-tuple prior to v0.3.0).
- **Telemetry**: the `telemetry_data` hypertable has no `patient_id` column, so for **patient** scope telemetry is **excluded** (a note is written to the manifest). For `group`/`system` scope, all tenant telemetry is included. On restore, telemetry rows are re-inserted with the destination tenant id.
- **Documents**: raw files are archived under `documents/<doc_id>.<ext>`; on restore they're written back to `UPLOAD_DIR/<tenant_id>/<new-uuid>.<ext>` and `DocumentModel.file_path` is rebased. OCR `extracted_text`/`entities` are preserved (no re-OCR needed).
- **Integrations**: `user_integrations.user_config` is exported **with Fernet-encrypted secrets intact** (the `{"_encrypted": ...}` tokens) plus plaintext OAuth `access_token`/`refresh_token`. **Restoring on a deployment with a different `INTEGRATION_SECRET_KEY` will leave secrets undecryptable** — the rows import, but the integration will fail to authenticate. A warning is added if `INTEGRATION_SECRET_KEY` is unset on the target. Never use the `mask_fields` helper in the export path.
- **AI config** (`ai_config.json`): exported only when `scope=system` and `include_ai_config=true`. **Restore is not supported in v1** (export-only) — a warning is recorded. `api_key` values are exported as-is (they're already plaintext in the DB).
- **Clinical event types / categories**: upserted by `slug` (only inserted if missing; existing definitions are not overwritten).
- **Biomarker catalog**: delegated to `CatalogImportService.import_catalog` (upsert by `slug`/`symbol`).

---

## 7. Job lifecycle (Celery)

Export/import are **async Celery tasks** (long jobs don't block the request):
1. `POST /export` (or `/import/backup`) creates an `ExportJobModel`/`ImportJobModel` row (`PENDING`) and calls `export_backup.delay(job_id)` / `import_backup.delay(job_id, archive_path, user_id)`.
2. The task binds a fresh session to the **worker-scoped shared engine** via `get_async_session()` (audit A7 — engine is a singleton with `NullPool`, never disposed per task), runs `ExportService.run_export` / `ImportService.run_import`, and updates the job row (`PROCESSING` → `COMPLETED`/`PARTIAL`/`FAILED`).
3. The import task deletes the temp upload file in `finally`.
4. Poll `GET /export/jobs/{id}` (or `/import/jobs/{id}`) for progress; download via `GET /export/jobs/{id}/download`.

Generated files are written to `UPLOAD_DIR/exports/<tenant_id>/`. There is no automatic retention/cleanup yet (admin should prune old exports).

---

## 8. Adding it to a new deployment

`fhir.resources` is in `backend/requirements.txt`. The migration `2f60048dd5ec_add_export_import_jobs_tables` creates the `export_jobs` and `import_jobs` tables. Run `alembic upgrade head` after pulling.

---

## 9. Frontend UI

A dedicated page is available at **`/settings/export-import`** (admin-only — `ADMIN` and `SYSTEM_ADMIN` roles). It's wired into the Sidebar under **Settings → Export & Import**.

**Files**: `frontend/src/pages/Settings/ExportImport.tsx`, `frontend/src/services/backupService.ts` (named async fns wrapping the REST endpoints), `frontend/src/services/backupService.test.ts` (vitest), `frontend/src/types/backup.ts` (TS mirrors of the backend Pydantic schemas). Route registered in `frontend/src/App.tsx` (wrapped in the `(user?.role === 'ADMIN' || user?.role === 'SYSTEM_ADMIN')` block). Sidebar entry in `frontend/src/components/layout/Sidebar.tsx` with `roles: ['ADMIN', 'SYSTEM_ADMIN']`. i18n keys under the `backup` namespace in `frontend/src/locales/{en,el}/common.json`.

### Page layout
- **Tabbed interface** (inline-tab pattern from `AIConfig`): **Export** and **Import**.
- **Export tab**: scope `<select>` (`patient`/`group`/`system` — options disabled if the user lacks the role), format `<select>` (`fhir_only`/`full_backup`/`catalog_only` with inline descriptions), patient multi-select (radio for `patient` scope, checkboxes for `group`; `USER` role sees their linked patient as a read-only note), option toggles for `include_documents`/`include_telemetry`/`include_integrations`/`include_ai_config` (shown only for `full_backup`, AI config only for `SYSTEM_ADMIN` + `system` scope), and a **Start export** button that enqueues the Celery task.
- **Import tab**: a dashed-border dropzone accepting `.zip`/`.json`, an info panel explaining the format/tenant/secret-key caveats, and a confirmation modal (`useUIStore.showConfirmation` with `confirmVariant: 'danger'`) before the restore is actually fired — protecting against accidental overwrites.
- **Job tables**: both tabs show a job history table (created, scope/type, status badge with inline progress bar, size, resource counts, download button). The import table additionally shows `manifest_verified` / `fhir_validated` ticks and error/warning counts.
- **Polling**: when a job is created, the page starts a 3s `setInterval` poller (the existing project pattern from `ExaminationDetail.tsx`) keyed on the job id, updates the row in place, stops when status reaches `COMPLETED`/`FAILED`/`PARTIAL`, fires a `react-toastify` toast, and self-clears after a 5-minute stall timeout. Intervals are tracked in a `useRef` map and cleaned up on unmount.
- **Toasts**: `toast.success` / `toast.error(err.response?.data?.detail || ...)` per the project convention.
- **Download**: uses the `triggerDocumentDownload` blob pattern from `documentService.ts` — `api.get(..., { responseType: 'blob' })` → `window.URL.createObjectURL` → synthetic `<a>` click.

### Verification
- `npx tsc --noEmit` — passes.
- `npm run lint` — 0 errors / 0 warnings in the new files.
- `npm run build` — passes (tsc + vite build + SW).
- `npx vitest run` — 22 tests pass (6 new in `backupService.test.ts` + 16 existing).

No frontend store was added — the page is stateless and tracks everything in local `useState`, matching the admin-page convention (`CatalogManagement`, `TenantManagement`).
