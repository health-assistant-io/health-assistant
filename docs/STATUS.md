# Health Assistant — Project Status & Roadmap

What's implemented, what's in progress, and what's planned for the self-hosted, open-source health records platform.

## Current Status

**Backend**: Running on http://localhost:8000  
**Frontend**: Running on http://localhost:3000  
**API Docs**: http://localhost:8000/docs

## Implementation Progress

### ✅ Completed

#### Backend
- FastAPI server with async support (Python 3.12+)
- SQLAlchemy 2.0 ORM with PostgreSQL & Alembic migrations
- JWT authentication with refresh tokens and presigned download tokens
- FHIR resource models (Patient, Observation, DiagnosticReport, Medication)
- Full Clinical Visit system (Examinations & Doctors)
- AI OCR & NLP Pipeline (OpenAI Vision/LLM + spaCy)
- Background task processing via Celery & Redis
- Modular Notification Framework with Web Push (VAPID) support — replaced with the **unified notification system** (multi-source, multi-recipient, multi-channel, role-aware, real-time via WebSocket). See [NOTIFICATION_SYSTEM.md](NOTIFICATION_SYSTEM.md).
- Unit converter service
- Anomaly detector service (Reference range based)
- Medication interactor service
- Centralized semantic versioning manager
- **Unified taxonomy & knowledge graph** — multi-kind `concepts` (one term can carry several domain tags via `concept_kind_tags`) + typed polymorphic `concept_edges`, replacing the scattered category tables. Searchable concept CRUD in the Catalogs workspace at `/catalogs?type=concept` (TaxonomyManager retired), REST API at `/concepts` + `/concept-edges`. See [TAXONOMY.md](TAXONOMY.md).
- **Unified Catalog Registry + cross-domain knowledge graph** — a thin registry-driven `/catalogs` meta-layer gives every clinical catalog (anatomy, taxonomy, biomarkers, medications, allergies, **vaccines**) one CRUD/RBAC/search/FHIR/edge contract (`app/catalogs/`). `concept_edges` is the **single** link system: biomarker→anatomy `AFFECTS`, biomarker↔event-type `MONITORS`, biomarker→panel `MEMBER_OF`, specialty→organ `EXAMINES`, etc. Cross-catalog traversal at `GET /catalogs/{type}/{id}/relations` (recursive CTE); unified search at `GET /catalogs/search` + the global `/search` (both cover all registered catalogs). **Vaccinations (Phase 5)** are implemented end-to-end: `VaccineCatalog` + `PatientImmunization` tables, `/vaccines/*` REST CRUD, FHIR R4 `Immunization` facade resource, CVX-coded seeds. Phases 0–5 of `dev/plans/unified-catalog-architecture-2026-07-08.md` landed; diseases-as-concepts (Phase 6), the admin workspace (Phase 7), and LLM tools (Phase 8) landed. The legacy `biomarker_relationships` + `biomarker_event_correlations` tables are dropped (migrated to edges).
- **Export & Import (backup) system** — FHIR R4B Bundle + BagIt-style ZIP exports at patient/group/system scope; validated imports with SHA256 manifest verification and cross-tenant id remapping (see [EXPORT_IMPORT.md](EXPORT_IMPORT.md)). Admin-only UI at `/settings/export-import` (export form, drag-and-drop restore, live job polling, download).
- **Agentic AI Copilot + human-in-the-loop (HITL) proposals** — the chat assistant proposes clinical write actions (create a clinical event, add a biomarker to an examination, add a medication, define a new biomarker/medication in the catalog); the user reviews/edits and explicitly confirms before anything is saved (the AI never writes directly). After resolution, the agent gets an **auto-resume continuation turn** with structured outcomes fed back. **Parallel proposals** (multiple independent actions per turn) and a **Continue button** for partial resumes are supported. See [AI_SYSTEM.md §4.1](AI_SYSTEM.md).
- **0.3.0 security & critical-fix pass** (see [CHANGELOG.md](../CHANGELOG.md)) — **all 29 Critical/High items resolved**:

  > **Note (2026-06-22):** A re-audit performed after this pass catalogued **110 additional findings** (25 Critical / 32 High / 30 Medium / 23 Low) covering the FHIR R4 facade conformance, bidirectional FHIR interop, internal data pipeline, DB schema drift, and backend architecture. The **P0 stabilization batch (12 commits, 130+ regression tests)** below landed next, closing every Critical/High item in the audit's P0 tier. Remaining P1–P3 items are tracked in the local stabilization plan.

  - *First pass:* AI provider `api_key` encrypted at rest + masked in responses; scope checks on `/ai-config/providers/*` and `/models/*`; telemetry endpoints tenant-scoped; global exception handler no longer leaks `str(exc)` (now uses a `correlation_id`); `fetch-external-models` SSRF guard; `list_observations` actually applies its filters; `/fhir/Observation/history` no longer raises `TypeError`; `sync_active_integrations` correctly routes telemetry to TimescaleDB; `AIModel.__table_args__` typo fixed + migration `f1a2b3c4d5e6`; dead `processors/fhir_mapper.py` deleted; `from app.models import *` works again; telemetry service stubs replaced with real implementations.
  - *Second pass (P0 completion):* OHLC double-aggregation fixed (`AVG(AVG(col))` → single-level) + telemetry bucket whitelist; `relative_score` boundary logic fixed (strictly-interior = Normal, boundary defers to range check); Magic Fill date now uses `datetime.now()`; FHIR single-resource reads tenant-scoped at the service level (`get_observation`/`get_diagnostic_report`/`get_medication`/`delete_observation`); **prompt-injection guard** (`app/utils/prompt_guard.py`, 8 OWASP LLM01 patterns + `DEFENSE_PREAMBLE`); `print()` leaks in AI service replaced with `logger.debug`; **SVG sanitizer rewritten** (all event-handler quoting forms + `javascript:`/`vbscript:`/`data:text/html` URLs + `<script>`/`<foreignObject>` elements); CORS fallback hostname fixed (underscore → hyphen); **WebSocket hardened** (subprotocol auth + fixed 10Hz busy-loop + error logging + 30s keepalive ping); **AuditLog provenance** (`app/services/audit_service.py`) wired into FHIR create/delete endpoints; insecure `POSTGRES_PASSWORD` default removed + production validator; SQL threshold validated before inlining; **webhook HMAC-SHA256** verification (opt-in via `webhook_secret`); auth on integration listing + documentation endpoints.
  - ***P0 stabilization batch (2026-06-22):* 12 audit items closed:**
    - **Tenant-isolation breaches closed** — `task_monitor` (B1), `notifications` incl. previously-unauth `/delivered` (B2, B3), `alerts` (B4), document `/preview` PHI exfil hole (B5), `/auth/register` tenant impersonation (B7 — invite-token flow + `pg_advisory_xact_lock` bootstrap race fix + new `POST /auth/invite`), `integration_api_proxy` (B8 — optional HMAC via `api_secret` + `X-Api-Signature`).
    - **Pipeline data-loss / race fixes** — `_persist_results` re-extraction wrapped in `begin_nested()` SAVEPOINT (C2); `_check_trigger_cumulative` per-exam `pg_try_advisory_xact_lock` (C3); `sync_active_integrations` per-integration Redis lock `sync_lock:{id}` `SET NX EX 600` (C4); `migrate_biomarker_data` telemetry→FHIR now resolves patient per-row via `device_id → UserIntegration → user_id → Patient.user_id` and aborts cleanly when attribution is impossible (C1).
    - **Worker hygiene** — `cleanup_stuck_extractions` threshold 15 → 20 min (5-min margin beyond Celery hard `task_time_limit=900s`) (A5); startup cleanup now filters `updated_at < threshold` so rolling restarts don't kill in-flight exams (A6).
    - **Schema & facade conformance** — `SoftDeleteMixin` added to all 9 FHIR-exposed models (`Patient`, `Observation`, `DiagnosticReport`, `Medication`, `AllergyIntolerance`, `OrganizationModel`, `ExaminationModel`, `ClinicalEvent`, `DocumentModel`); the facade's `_soft_delete_predicate` is now non-None for all of them and reads return `410 Gone` instead of `404 Not Found` (D6 + F3). `OrganizationModel` gained `TimestampMixin` (`created_at`/`updated_at`) (D15). Migration `c4a8e7f2b1d9` realigns the index naming convention. The legacy `idx_*_deleted_at` partial indexes that were invisible to SQLAlchemy autogenerate are dropped; the `ix_*_deleted_at` indexes the models declare are created.
    - **Regression coverage** — 11 new test files (`tests/test_task_monitor_isolation.py`, `test_notifications_isolation.py`, `test_alerts_isolation.py`, `test_documents_preview_auth.py`, `test_auth_register_isolation.py`, `test_integration_api_proxy_hmac.py`, `test_stuck_extraction_cleanup.py`, `test_medical_processing_savepoint.py`, `test_check_trigger_cumulative_race.py`, `test_sync_active_integrations_lock.py`, `test_migrate_biomarker_attribution.py`, `test_softdelete_mixin_alignment.py`). Full suite: **830 tests pass**.
  - ***Full-project audit (2026-07-15):* all Critical/High security items + the structural cleanup batch landed** — baseline `dev/audits/AUDIT-2026-07-15.md`, details in [CHANGELOG.md](../CHANGELOG.md). **Section A (security):** closed the critical second-order SQLi in the biomarker telemetry path (A1), Redis rate limiting on auth + refresh-token rotation/revocation (A2/A5), upload file-type allowlist + inline-XSS hardening + size cap (A3/A4), `detail=str(e)` leak removal (A6), medium/low hardening (A7–A13). **Data architecture:** per-tenant unique indexes on 4 catalog/identity tables (B1), `Observation.document_id` → UUID FK `ON DELETE SET NULL` (B2), maintained `patient_id` FK on Observation/DiagnosticReport `ON DELETE CASCADE` (B3 — also fixes the silent facade `?patient=` no-op), obvious CHECK constraints (B10). **Backend structure:** domain-exception hierarchy `app/core/errors.py` + handler (C1), shared `app/core/converters.py` (C8), Pydantic config fields (C7), dead-code deletes + frontend logout IndexedDB PHI-leak fix (D1). 11 new regression test files (100+ cases); full suite **1822 pass**.
- **FHIR R4 facade (Stage 3)** — **15 FHIR R4 conformance items resolved** (only advanced C6 conformance deferred):
  - New `/api/v1/fhir/R4/` router exposes a conformant FHIR R4 REST API — the **interop surface** for external systems. The frontend uses domain endpoints (`/patients/*`, `/observations/*`, ...); the legacy ORM-shape `/fhir/*` router has been deprecated. See [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md).
  - `GET /fhir/R4/metadata` CapabilityStatement (dynamic, from `RESOURCE_REGISTRY`).
  - `GET /fhir/R4/{Resource}` returns FHIR Bundles (`type=searchset`) with pagination links; honors `_id`/`_lastUpdated`/`_count`/`_sort`/`_format` + resource-specific params (`patient`/`code`/`date`/`status`/etc).
  - `POST /fhir/R4/{Resource}` returns 201 + Location + ETag; input parsed via `fhir_to_orm`; output validated by `fhir.resources`.
  - `DELETE` soft-deletes via `SoftDeleteMixin.deleted_at`; subsequent reads return `410 Gone` (tombstone OperationOutcome).
  - **19 resources** registered: Patient, Observation, Condition, EpisodeOfCare, Encounter, AllergyIntolerance, MedicationStatement, MedicationRequest, Medication (catalog), Immunization, DiagnosticReport, DocumentReference, Device, Communication, Organization, Practitioner, Provenance, CodeSystem, ValueSet.
  - **Hybrid storage** (no dual-write): existing tables became FHIR-canonical via `to_fhir_dict()` projections (Condition ← ClinicalEvent, Encounter ← ExaminationModel, DocumentReference ← DocumentModel); 3 new tables for concepts with no app analog (`fhir_provenance`, `fhir_devices`, `fhir_communications`).
  - **Provenance-on-write** best-effort hook on every facade create/update/delete.
  - **Medication intent discriminator**: one `fhir_medications` table serves both MedicationStatement (`intent=statement`) and MedicationRequest (`intent=order|plan|proposal`).
  - **AllergyIntolerance write-time FHIR gate**: `allergy_service` now calls `assert_valid_fhir()` (parity with `fhir_service`).
  - 4 migrations, 131 new tests, zero regressions.
  - **API surface consolidation**: deprecated the misleadingly-named `/fhir/*` ORM-shape router; patient/observation CRUD moved to proper domain endpoints (`/patients/*`, `/observations/*`); medication create consolidated under `/medications/*` (new `GET /medications/{id}` for citation lookups). The `/fhir/R4/*` facade is now clearly the interop-only surface. The frontend's `types/fhir.ts` was split into `types/patient.ts` + `types/observation.ts` (the old name was misleading — these types mirror ORM-shape, not FHIR R4). Dead code removed: GraphQL client (defined but unused), 6 unused `/fhir/*` routes.
- **Unified notification system** (5 phases) — replaced the legacy single-table reminder system with a multi-source, multi-recipient, multi-channel, role-aware platform with real-time delivery. See [NOTIFICATION_SYSTEM.md](NOTIFICATION_SYSTEM.md):
  - **Fan-out model** (3 tables: `Notification` event + `NotificationRecipient` inbox state + `NotificationDelivery` channel log). Single `emit()` API every source calls.
  - **Multi-source**: SCHEDULED (medication/exam reminders), RULE (event-driven biomarker thresholds), AGENT (HITL proposals), INTEGRATION (sync outcomes), CLINICAL (clinical-event lifecycle), SYSTEM (admin broadcasts).
  - **Real-time delivery**: per-user WebSocket `/ws/notifications` over Redis pub/sub (Bearer subprotocol auth) + fallback poll. Replaces the legacy 30s bell polling.
  - **Web Push (VAPID)**: per-user subscriptions, dead-endpoint self-pruning on 410/404, per-recipient delivery log, click-to-detail modal in the admin center.
  - **Rules engine**: `NotificationRule` replaces the removed `AlertModel`/`/alerts/*` endpoints. Evaluated on every observation ingestion (`fhir_service.create_observation` → `evaluate_and_fire`).
  - **Admin center**: broadcast composer (tenant / system), aggregate stats, click-through per-recipient delivery detail.
  - Removed: `AlertModel`, `/alerts/*` endpoints, `TriggerType.EVENT`, the dead `biomarker_update` event hook, the `/notifications/{id}/delivered` SW-callback endpoint (delivery now tracked server-side).
  - 1292 backend tests passing; lint clean.

#### Frontend
- React 18 app with Vite & TypeScript
- Immersive frontend with Tailwind CSS
- Draggable & Persistent Dashboard (react-grid-layout)
- Secure full-screen viewers for Images, PDFs, and Text/Markdown
- Centralized data extractor (`useBiomarkers` hook)
- Zustand state management
- Notification Center & PWA Push support — unified inbox, real-time WebSocket delivery, biomarker rules engine, admin center with broadcast + per-recipient delivery detail. See [NOTIFICATION_SYSTEM.md](NOTIFICATION_SYSTEM.md).
- Document gallery and clinical timeline
- **Export & Import UI** (`/settings/export-import`, admin-only) — create exports, restore from ZIP/JSON, live job polling, download
- **0.3.0 PWA fixes**: manifest shortcut `/examinations/new` → `/examinations/upload`; PWA runtime caches now use same-origin predicates (no longer hardcoded to `localhost:8000`).

### ⚠️ In Progress
- Advanced anomaly detection algorithms (Statistical)
- Multi-language OCR refinement
- Chart component enhancements
- Advanced form validation

### 📅 Roadmap / Future Tasks
1. **Advanced Analytics**: Multi-axis charts for trend visualization.
2. **Data Portability**: Full patient history export (PDF/JSON). — *Partially delivered: FHIR Bundle + ZIP backup export/import landed (see [EXPORT_IMPORT.md](EXPORT_IMPORT.md)); PDF report export still pending.*
3. **Testing**: Add E2E tests using Playwright or Cypress.
4. **Mobile Sync**: Headless mobile sync architecture for wearable data.
5. **Biomarker Insights**: Deeper clinical insights and correlations (See [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)).
6. **FHIR R4 advanced conformance**: `POST /_search`, `_format=xml`, transaction/batch Bundle processing. Core R4 conformance (CapabilityStatement, Bundle search, missing resources, Provenance) landed in the Stage 3 facade (see [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md)).
