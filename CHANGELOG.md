# Changelog

All notable changes to Health Assistant are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Workflow:** every user-facing change adds a line under `## [Unreleased]` at
> commit time (see [docs/RELEASE_PROCESS.md](docs/RELEASE_PROCESS.md)). At release
> time, `## [Unreleased]` is renamed to `## [vX.Y.Z] - YYYY-MM-DD` and a fresh
> `## [Unreleased]` is opened above it.

## [Unreleased]

### Added
- **Reusable test workflow (``.github/workflows/test.yml``).** The backend pytest suite and frontend lint+build test are now defined once in a reusable workflow (``on: workflow_call``) and called from the GitHub ``docker-publish.yml`` pipeline. The workflow includes the TimescaleDB + Redis service containers, the ``PYTHONPATH`` fix for the top-level ``integrations`` package, and bumps the frontend Node version to 20 (matching the frontend Dockerfiles). The Gitea ``deploy.yml`` keeps its test jobs inline (``test-backend`` + ``test-frontend`` as separate top-level jobs) because Gitea's ``act_runner`` collapses reusable-workflow jobs into a single "Set up job" node with no step-level visibility — inlining preserves separate job nodes and full step visibility on Gitea.

### Fixed
- **CI/CD ``test-backend`` job failed** with ``ModuleNotFoundError: No module named 'integrations'``. The Gitea workflow ran ``pytest`` from ``backend/`` with ``pythonpath = .`` (per ``pytest.ini``), which only puts ``backend/`` on ``sys.path`` — but ``app.core.integration_registry`` imports the top-level ``integrations`` package (sibling of ``backend/``). The pytest step now exports ``PYTHONPATH`` to mirror the production Docker setup (``PYTHONPATH=/app/backend:/app`` in ``docker/Dockerfile``), so both ``app/`` and ``integrations/`` are importable during tests.

### Changed
- **GitHub ``docker-publish.yml`` now gates image pushes on tests.** Previously, the GitHub workflow built and pushed Docker images to ghcr.io with no test verification at all. Both ``build-and-push-backend`` and ``build-and-push-frontend`` now declare ``needs: [test]`` and only run after the reusable test workflow passes.
- **Gitea ``deploy.yml`` test jobs inlined** (``test-backend`` + ``test-frontend`` as top-level jobs with ``needs: [test-backend, test-frontend]`` on ``build-and-push``), and ``actions/checkout`` bumped from v3 to v4 across all jobs. Gitea renders top-level jobs as separate nodes, so backend/frontend test results are visible at a glance.

### Changed
- **People & Access list (`/admin/tenant/users`) is now clickable.** Selecting a person's name/avatar navigates to their detail page instead of using a separate "eye" action button. The link-record (chain) and change-role (shield) row actions were removed from the list — both operations now live on the detail page (`/admin/tenant/users/{id}`): access level is edited inline via an Access Level card, and clinical profiles (patients/doctors) are linked/unlinked from the Linked Clinical Profiles section. Added missing `common.close`, `common.no_patients`, and `common.no_doctors` i18n keys (EN/EL) that were referenced but absent.

## [0.3.0-rc.5] - 2026-06-25

### Fixed
- **Frontend Docker image build crashed** (`ReferenceError: crypto is not defined` from `serialize-javascript` via `@rollup/plugin-terser`). The frontend Dockerfiles (`frontend/Dockerfile`, `docker/Dockerfile.frontend`) used `node:18-alpine`, where `globalThis.crypto` is still behind the `--experimental-global-webcrypto` flag (unflagged only in Node 20+). Bumped base image to `node:20-alpine`. Node 18 also reached EOL in April 2025.

## [0.3.0-rc.4] - 2026-06-25

### Changed
- **Versioning & changelog workflow is now local-first by default.** `docs/RELEASE_PROCESS.md` and `docs/DEVELOPMENT.md` updated: `version_manager.py --git` (stage + commit + tag locally) is the default stop point; `--push` (publishes to every remote + triggers CI/CD) is opt-in and only used when explicitly requested. A prominent push-policy callout was added to `RELEASE_PROCESS.md`. All examples and quick checklists reordered to show the local-only path first.
- **Changelog rule made explicit** in `docs/DEVELOPMENT.md` and `docs/RELEASE_PROCESS.md`: every user-visible change must add a bullet under `## [Unreleased]` at commit time, proactively.

### Fixed
- **Frontend build broken** — `tsc` failed with `Cannot find namespace 'NodeJS'` on 5 files (`CitationButton.tsx`, `BiomarkerDetail.tsx`, `DocumentList.tsx`, `ExaminationDetail.tsx`, `ExaminationList.tsx`) that used `NodeJS.Timeout` for `setInterval`/`setTimeout` return types. `tsconfig.json` `types` only includes `vite/client` + `vitest/globals` (no `@types/node`), so the namespace was unresolvable. Switched to the browser-idiomatic `ReturnType<typeof setTimeout/setInterval>`, which needs no `@types/node` and is runtime-identical.
- **Removed all private references from public docs.** `docs/AI_SYSTEM.md`, `docs/API.md`, `docs/STATUS.md`, and `docs/RELEASE_PROCESS.md` no longer reference internal/private tooling; links replaced with public doc cross-references (`INTEGRATIONS_SDK.md`, `AI_SYSTEM.md §4.1`).
- **`docs/RELEASE_PROCESS.md` cutting-a-release / promote-RC / catch-up sections** no longer default to `--git --push`; they default to `--git` (local) with `--push` shown as an explicit opt-in step.
- **Stale version string** in project metadata corrected (`0.3.0-alpha` → `0.3.0-rc.3`).

## [0.3.0-rc.3] - 2026-06-25

### Security
- **All 58 Dependabot alerts resolved** (1 critical, 26 high, 28 moderate, 3 low) across backend (pip) and frontend (npm). `npm audit` now reports 0 vulnerabilities.
- **Backend: replaced unmaintained `python-jose` with `PyJWT`** (CVE-2024-33664/33663 — algorithm confusion, DoS). `ecdsa`, `rsa`, and `passlib` (transitive deps of python-jose, unused in code) removed entirely. `security.py` updated: `JWTError` → `jwt.PyJWTError`.
- **Backend: bumped vulnerable packages** — `cryptography` 47→49 (vulnerable OpenSSL wheels), `starlette` 1.3.0→1.3.1 (`request.form()` DoS), `langsmith` 0.8.14→0.9.1 (TracingMiddleware file read), `langchain` 1.3.7→1.3.11 (path traversal/sandbox escape), `pydantic-settings` 2.14.1→2.14.2 (symlink secrets_dir bypass), `fastapi-mail` 1.6.4→1.6.5 (cryptography 49 compat).
- **Frontend: bumped `axios` → 1.18.1** — fixes 22 CVEs including SSRF, prototype pollution, credential leak, header injection, and CRLF injection.
- **Frontend: bumped `vitest` 1.1→4.1.9** (critical CVE — arbitrary file read/execute when UI server is listening). Required major bump of `vite` 5.4→6.4.3, `@vitejs/plugin-react` → 4.7.0, `vite-plugin-pwa` → 1.3.0.
- **Frontend: bumped `i18next-http-backend` → 3.0.6** (path traversal/URL injection), `react-router-dom` → 6.30.4 (open redirect), `postcss` → 8.5.15 (XSS via `</style>`), `@typescript-eslint/*` 6→7.18.0 (transitive `minimatch` ReDoS).
- **Frontend: pinned `react-quill-new` ~3.7.0 + npm `overrides` for `quill` 2.0.2** (Quill XSS via HTML export).
- **Frontend: `npm audit fix` patched transitive deps** — `flatted`, `lodash`, `picomatch`, `brace-expansion`, `follow-redirects`, `form-data`, `serialize-javascript`, `fast-uri`, `js-yaml`, `@babel/*`, `esbuild`.
- **Added `.github/dependabot.yml`** — weekly automated dependency checks with auto-PR for direct deps across `pip` (backend), `npm` (frontend + integrations TS SDK), `docker`, and `github-actions` ecosystems.

## [0.3.0-rc.2] - 2026-06-25

**Release candidate — tenant administration, deployment hardening, UI overhaul.**

### DB foundation: migration squash + schema hardening

**Single deterministic baseline replaces the 62-migration chain.** Existing
databases must be dropped and recreated (`alembic upgrade head` from empty).
There is no in-place upgrade path from the prior chain — the historical
migrations are archived under `backend/alembic/versions_archived/` for
traceability but no longer executed.

The squashed `0001_initial_schema.py` is fully idempotent (re-runnable),
guards all TimescaleDB DDL behind an extension-availability check, creates
all 20 PG enum types via `DO $$ ... EXCEPTION WHEN duplicate_object` blocks,
and seeds examination categories with deterministic uuid5-based IDs.

#### Migrations (21 audit items closed)
- **D7**: `Medication.intent` column is now a proper PG enum (was `VARCHAR(50)`).
- **D8**: `TenantMixin` now declares `ForeignKey("tenants.id", ondelete="CASCADE")` — 29 tables got the FK. Deleting a tenant purges all owned data instead of orphaning it. `TelemetryDataModel` overrides `tenant_id` without FK (TimescaleDB hypertable limitation).
- **D9**: Patient deletion cascades to ALL clinical tables. Previously `examinations`, `documents`, `fhir_devices`, `chat_sessions` used `SET NULL` (orphaned rows); now `CASCADE` (full record removal).
- **D10 + E2**: `documents.entities` converted from `JSON` to `JSONB` + GIN index for `@>` / path queries.
- **D11**: Dead `SENT` value in `notificationstatus` enum dropped (was never in the Python enum).
- **D12**: `role` enum no longer uses the `COMMIT` workaround for `ALTER TYPE ADD VALUE`.
- **D13**: Examination category seed UUIDs are deterministic (`uuid5` + `ON CONFLICT DO NOTHING`).
- **D14**: All TimescaleDB DDL (hypertable, continuous aggregates, retention/compression policies) is guarded — plain-PG dev/test databases now migrate successfully without the extension.
- **D16**: Dropped dead `notifications.fhir_resource_type` column (never honored).
- **D17**: `export_jobs.completed_at` + `import_jobs.completed_at` converted from `TEXT` to `TIMESTAMPTZ`.
- **D18**: `BodyPartModel.slug` is now `UNIQUE` (matches every other slug column in the codebase).
- **D19**: `CHECK` constraint on `fhir_patients.mrn` prevents empty-string MRNs.
- **D22**: Consistent index naming across the schema.
- **D23**: Idempotent enum creation (all via `DO` blocks or `create_type=False`).
- **K12**: Stale migration docstring archived.

#### Performance indexes
- **E1**: Expression index on `Observation.subject->>'reference'` + `DiagnosticReport.subject->>'reference'`. This is the most-used query pattern in the codebase (12+ call sites). `EXPLAIN` confirms the planner now uses an Index Scan instead of a full-table scan within tenant.
- **E3**: Indexes on `biomarker_group_members.biomarker_id` + `.group_id` (FK reverse-lookups).
- **E4**: Indexes on `biomarker_relationships.source_biomarker_id` + `.target_biomarker_id`.
- **E5**: Composite `(tenant_id, timestamp)` index on `telemetry_data` for tenant-wide analytics.
- **E6**: Indexes on FHIR sort columns — `fhir_patients.birth_date`, `fhir_medications.start_date`, `fhir_communications.sent`.

#### Migration upgrade instructions
```bash
# Existing dev databases must be reset (no in-place upgrade path):
PGPASSWORD=admin123 psql -h localhost -p 5433 -U admin -d postgres \
  -c "DROP DATABASE IF EXISTS health_assistant;"
PGPASSWORD=admin123 psql -h localhost -p 5433 -U admin -d postgres \
  -c "CREATE DATABASE health_assistant OWNER admin;"
cd backend && alembic upgrade head
```

### P0 stabilization pass (2026-06-22)

**7 critical/high fixes + a redesigned dev workflow + production-grade Docker hardening** following the comprehensive re-audit.

### Security
- **Tenant-isolation breaches closed** — `task_monitor` (B1), `notifications` incl. previously-unauth `/delivered` (B2, B3), `alerts` (B4), document `/preview` PHI exfil hole (B5), `/auth/register` tenant impersonation (B7 — invite-token flow + `pg_advisory_xact_lock` bootstrap race fix + new `POST /auth/invite`), `integration_api_proxy` (B8 — optional HMAC via `api_secret` + `X-Api-Signature`).
- **Pipeline data-loss / race fixes** — `_persist_results` re-extraction wrapped in `begin_nested()` SAVEPOINT (C2); `_check_trigger_cumulative` per-exam `pg_try_advisory_xact_lock` (C3); `sync_active_integrations` per-integration Redis lock `sync_lock:{id}` `SET NX EX 600` (C4); `migrate_biomarker_data` telemetry→FHIR now resolves patient per-row via `device_id → UserIntegration → user_id → Patient.user_id` and aborts cleanly when attribution is impossible (C1).
- **Worker hygiene** — `cleanup_stuck_extractions` threshold 15 → 20 min (5-min margin beyond Celery hard `task_time_limit=900s`) (A5); startup cleanup now filters `updated_at < threshold` so rolling restarts don't kill in-flight exams (A6).
- **Schema & facade conformance** — `SoftDeleteMixin` added to all 9 FHIR-exposed models (`Patient`, `Observation`, `DiagnosticReport`, `Medication`, `AllergyIntolerance`, `OrganizationModel`, `ExaminationModel`, `ClinicalEvent`, `DocumentModel`); the facade's `_soft_delete_predicate` is now non-None for all of them and reads return `410 Gone` instead of `404 Not Found` (D6 + F3). `OrganizationModel` gained `TimestampMixin` (`created_at`/`updated_at`) (D15). Migration `c4a8e7f2b1d9` realigns the index naming convention. The legacy `idx_*_deleted_at` partial indexes that were invisible to SQLAlchemy autogenerate are dropped; the `ix_*_deleted_at` indexes the models declare are created.
- **Regression coverage** — 11 new test files (`tests/test_task_monitor_isolation.py`, `test_notifications_isolation.py`, `test_alerts_isolation.py`, `test_documents_preview_auth.py`, `test_auth_register_isolation.py`, `test_integration_api_proxy_hmac.py`, `test_stuck_extraction_cleanup.py`, `test_medical_processing_savepoint.py`, `test_check_trigger_cumulative_race.py`, `test_sync_active_integrations_lock.py`, `test_migrate_biomarker_attribution.py`, `test_softdelete_mixin_alignment.py`). Full suite: **830 tests pass**.
- **AI provider `api_key` is now encrypted at rest** (Fernet, via `INTEGRATION_SECRET_KEY`) and **masked in every API response** (`***<last4>`, plus a new `has_api_key` boolean). Plaintext keys persisted before this release must be migrated with `PYTHONPATH=. python scripts/encrypt_existing_api_keys.py` (supports `--dry-run`, idempotent). The factory layer reads plaintext only via the new `AIProviderModel.get_api_key_plaintext()` accessor at LLM-instantiation time.
- **Scope checks on `/ai-config/providers/{id}`, `/providers/{id}/with-models`, `/providers/{id}/models`, `/models/{id}`, `/providers/{id}/fetch-external-models`** — previously any authenticated user could read any provider (including its key) by UUID. New `verify_provider_access` / `verify_model_access` helpers enforce USER/TENANT/SYSTEM scope on all 7 entry points.
- **`fetch-external-models` SSRF guard**: in production (`DEBUG=False`) the `api_base` must be `http(s)://` and not point at a loopback / private / link-local address.
- **Telemetry endpoints are now tenant-scoped**. `/telemetry/data`, `/telemetry/data/summary`, and `/telemetry/anomalies` previously accepted only `device_id`; a user who guessed another tenant's `device_id` could read its data. All three endpoints now require and filter on the caller's `tenant_id`.
- **Global exception handler no longer leaks `str(exc)` in production**. 500 responses now include a `correlation_id`; the full detail is logged server-side with the same id. `DEBUG=True` preserves the verbose detail for developer convenience.
- **Tenant-scoped FHIR single-resource reads** — `get_observation`, `get_diagnostic_report`, `get_medication`, and `delete_observation` now accept an optional `tenant_id` parameter and filter on it (defense in depth at the service level). All `/fhir/*/{id}` endpoints pass `current_user.tenant_id`.
- **Prompt-injection guard** (`app/utils/prompt_guard.py`) — heuristic detector for 8 OWASP LLM01 patterns (instruction-override, role-switch, role-marker-injection, prompt-extraction, jailbreak-mode, delimiter-escape, rule-injection). `scan_prompt_injection()` → `{safe, risk, matches, snippets}` with risk escalation (1 match = medium, 2+ = high). Non-blocking — logs WARNING, HITL wall remains the structural defence. `DEFENSE_PREAMBLE` prepended to both chat system prompts. The `assist()` dispatcher runs every input through the guard before the LLM.
- **SVG sanitizer rewritten** (`app/utils/svg.py`) — compiled regexes for all event-handler quoting forms (double-quoted, single-quoted, unquoted), `javascript:`/`vbscript:`/`data:text/html` URL protocols in `href`/`xlink:href`, and dangerous elements (`<script>`, `<foreignObject>` — both paired and self-closing). Optimization passes preserved.
- **WebSocket hardened** (`app/api/v1/endpoints/websockets.py`) — prefers `Sec-WebSocket-Protocol` subprotocol auth (`["bearer", token]`) over URL query string; fixes unawaited `asyncio.sleep(0.1)` busy-loop (10 Hz → 1 Hz via `get_message(timeout=1.0)`); logs errors before `close(1011)`; sends 30s keepalive ping.
- **AuditLog provenance** (`app/services/audit_service.py`) — `log_audit_action()` helper (best-effort, own session, never raises) wired into `POST /fhir/Observation`, `DELETE /fhir/Observation`, `POST /fhir/DiagnosticReport`, `POST /fhir/Medication`. Captures who/what/when + old/new value diff.
- **Insecure DB password default removed** — `POSTGRES_PASSWORD` no longer defaults to `admin123` in `config.py`; production `@model_validator` refuses known-weak credentials; `.env.example` uses `CHANGE_ME` placeholder.
- **Webhook HMAC-SHA256 verification** — integrations can set `user_config["webhook_secret"]`; when present the route verifies a constant-time HMAC-SHA256 signature over the raw body. Supports `X-Webhook-Signature`, `X-Webhook-Signature-256`, and GitHub-style `X-Hub-Signature-256`. Backward-compatible (no secret = legacy UUID-as-secret).
- **Auth on integration listing** — `GET /integrations/available` and `GET /integrations/{domain}/documentation` now depend on `get_current_user` (any role).
- **AI debug `print()` leaks removed** — two `print()` calls in `_define_biomarker`/`_define_medication` that dumped user input + LLM output to stdout replaced with `logger.debug`.
- **CORS fallback hostname fixed** — `app.health_assistant.com` (underscore — invalid RFC 1123) → `app.health-assistant.com`.
- **Catalog search SQL hardened** — `_set_similarity_threshold` validates float in [0, 1] before inlining into `SET pg_trgm.similarity_threshold` (PostgreSQL `SET` doesn't accept bind params).

### Fixed
- **`deliver_notification` `NameError` on every PUSH delivery** — `tasks.py` imported `datetime` as a module but called `datetime.now(timezone.utc)` with `timezone` unbound. Push notifications were silently 100% broken (status never advanced past `PENDING`). Fixed.
- **`admin.py` was entirely broken at import-call boundary** — imported nonexistent `async_session_maker`, wrong `TaskLogger`/`TaskProgressTracker` arity, called nonexistent methods. The frontend's `adminService.ts` uses both endpoints (`/admin/catalogs/import/url` + `/file`) so the feature was dead in prod. Rewritten against real signatures in `app.workers.task_logger`.
- **`list_examination_categories` raised `NameError`** — used `or_(...)` not imported at module scope. Re-flagged from 0.3.0-alpha; now actually fixed.
- **5 lowercase role-string comparisons in `documents_db.py`** (`update/edit/trigger_extraction/extraction_status/delete` endpoints) compared against `["admin", "manager"]` while enum values are uppercase. Admin/manager bypass never matched → admins couldn't perform their jobs on these endpoints. Now uses `Role.ADMIN.value` / `Role.MANAGER.value` / `Role.SYSTEM_ADMIN.value`.
- **Three PG enum drifts** that crashed on first use of the missing values: `medicationstatus` missing `INACTIVE`/`CANCELLED`; `allergycriticality` had wrong token (`unable_to_assess` underscore vs `unable-to-assess` hyphen rename); `aiscope` missing `ORGANIZATION`. Fixed by migration `7a9c2e1b4f3a` (idempotent, uses `DO $$ … IF NOT EXISTS … END $$`).
- **`fhir_communications` schema drift** — `CommunicationModel` uses `VersionedMixin` but the table lacked `version` + `is_current` columns and declared `tenant_id` `NOT NULL` (model inherits nullable from `TenantMixin`). ORM attribute access + facade PUT version-bump crashed. Fixed by migration `b3f1d52a9c7e`.
- **`notifications.communication_id`** column existed in DB but was missing from the `Notification` model. Added to model + surfaced in `to_dict()`; index created by migration `b3f1d52a9c7e`.
- **`list_observations` now applies its filters.** The function accepted `patient_id`/`code`/`start_date`/`end_date` but silently ignored them, returning every observation in the tenant. Cross-patient data exposure. Results are also ordered by `effective_datetime DESC`.
- **`/fhir/Observation/history` no longer raises `TypeError`.** It was calling `get_observation(patient_id, code, period)` — wrong arity. New `get_observation_history` service fn + reordered routes so `/Observation/history` is matched before `/Observation/{observation_id}`.
- **`sync_active_integrations` (background Celery task) now routes telemetry to TimescaleDB.** Every pulled observation previously landed in `fhir_observations` regardless of `BiomarkerDefinition.is_telemetry`, breaking the AI telemetry tools. New shared helper `app.services.integration_sync_service.apply_telemetry_split` — also wired into the manual-sync endpoint for DRY.
- **`/telemetry/anomalies` no longer raises `TypeError`.** Was calling the synchronous `AnomalyDetector.detect_biomarker_anomalies(device_id, metric, period)` with the wrong arity and an `await`. New wrapper `get_telemetry_anomalies` fetches history and feeds the detector correctly.
- **`AIModel.__table_args__` typo corrected** (was missing trailing `__`, silently dropped the `idx_ai_models_provider_active` composite index). Migration `f1a2b3c4d5e6` creates the index on existing databases.
- **Dead `processors/fhir_mapper.py` deleted** — broken import (`from app.models.fhir.observation import Observation` — module doesn't exist). Would have raised `ModuleNotFoundError` if imported.
- **`from app.models import *` works again** — removed the stale `WearableDataModel` alias (renamed to `TelemetryDataModel`); `TelemetryDataModel` is now exported in `__all__`.
- **`telemetry_service.get_telemetry_data` and `get_telemetry_summary` are no longer stubs.** Real tenant-scoped `SELECT` + aggregate queries against the `telemetry_data` hypertable.
- **OHLC double-aggregation fixed** (`analytics_service.py`) — the raw-table path set `avg_col = "AVG(col)"` and the SQL template wrapped it in `AVG(AVG(col))` — invalid SQL, silently caught by the except handler, producing empty charts for any non-cagg-stride bucket (all sub-hour/day + 1-week/month aggregations). Now uses single-level `AVG(col)`/`MAX(col)`/`MIN(col)`. Added `_ALLOWED_TELEMETRY_BUCKETS` whitelist to guard the `INTERVAL '{bucket}'` f-string interpolation.
- **`relative_score` boundary logic fixed** (`analytics_service.py`) — `_get_observation_status` used `< 0 → Low` / `> 1.0 → High` on a value clamped to [0, 1], so every score short-circuited to Normal. Now only returns Normal for strictly-interior scores (0 < s < 1); boundary values (0.0/1.0) defer to the explicit reference-range comparison.
- **Magic Fill live date** (`ai_assistance_service.py`) — hardcoded `"Today's date is 2026-03-22."` replaced with `datetime.now(timezone.utc)` injection for both `today_iso` and `current_year`.

### Added
- **AI Chatbot — HITL auto-resume continuation**: after the user resolves a human-in-the-loop task card (approve/reject), the agent automatically gets a continuation turn via `POST /sessions/{id}/resume`. The backend reads resolved outcomes from the `tasks` JSONB (never trusted from the client), builds a structured `[HITL RESOLUTION FEEDBACK]` message with per-task status/result/error, and streams a new response. The agent acknowledges what was saved, chains dependent proposals (e.g. define biomarker → add to exam via auto-resume), and respects dismissed items. Guardrails: fires at most once per message, race-gated, suppressed on session reloads.
- **AI Chatbot — HITL Continue button (partial resume)**: when the user answers some review cards but leaves others pending, a "Continue (N unanswered)" button appears. Clicking it triggers the resume with partial answers — the LLM is told which items were skipped and instructed not to auto-repropose them. When all cards are resolved, auto-resume fires immediately (no button needed).
- **AI Chatbot — parallel HITL proposals**: the system prompt now allows multiple independent `propose_*` calls in a single turn (e.g. "add medications X, Y, Z" → three parallel review cards). Dependent actions are split across turns via the auto-resume chaining mechanism.
- **`HitlTaskStatus` enum** (`backend/app/models/enums.py`): `(str, Enum)` with `PROPOSED | CONFIRMED | FAILED | DISMISSED` + a `terminal()` classmethod. Replaces scattered string-literal status comparisons across the backend. `HitlResolutionRequest.status` is now typed as the enum (Pydantic validates at the boundary). Backward-compatible: compares equal to plain strings, so existing JSONB data works unchanged.
- **`TERMINAL_HITL_STATUSES`** frontend constant (`registry.tsx`): ReadonlySet mirroring the backend `terminal()` helper — single source of truth for "which statuses mean the user finished acting."
- **AI Chatbot — human-in-the-loop (HITL) `add_medication` proposal**: the agentic chat can propose adding a medication; the user reviews/edits in a card + modal and explicitly confirms before anything is saved. Resolves the medication against the medication catalog automatically to ensure correct identifiers.
- **Powerful Catalog Search (pg_trgm)**: Replaced basic substring ILIKE matching with trigram similarity (`pg_trgm`) and GIN indexes across all catalogs (Medications, Biomarkers, Allergies, Clinical Event Types/Categories). This provides robust typo-tolerance for search and AI tools.
- **Unified Catalog Search Service**: A new `catalog_search_service.py` to power all catalog search functions across the backend.
- **Agent Capabilities Inspector**: A new modal inside the AI Chat Interface (accessible via the Wrench icon) that lists all available LangChain tools to the LLM. Features tool descriptions and dynamically parses the `args_schema` to display the exact input arguments, types, and descriptions required for each tool.
- **Data Source Inspector ID Visibility**: The raw UUID is now visible at the bottom of the data source inspector mini-cards (DataMiniPage) for precise referencing.
- **AI Chatbot — human-in-the-loop (HITL) `add_biomarker_to_examination` proposal**: the agentic chat can propose adding a lab-result biomarker to an examination; the user reviews/edits in a card + modal and explicitly confirms before anything is saved. The AI never writes — commit flows through the canonical `POST /fhir/Observation` endpoint, FHIR-validated at write time via `assert_valid_fhir`. The target exam resolves from the open exam in the chat context **or** an explicit `examination_id` the AI resolves via `get_recent_examinations` (hard-fails on unknown / cross-patient exam). Second HITL task type after `create_clinical_event`. See [AI_SYSTEM.md §4](docs/AI_SYSTEM.md).
- **Integrations SDK OAuth2 + SMART auth module** (`integrations/sdk/auth.py`): reusable Authorization Code + PKCE primitives, SMART discovery (`.well-known/smart-configuration`), **Dynamic Client Registration** (users enter only the server URL), encrypted `OAuthTokenStore`, Redis-backed `OAuthStateStore`, and a composed `SmartOAuth` (incl. `force_refresh` for refresh-on-401). Foundation for all cloud integrations (FHIR, Fitbit, Withings, …). 23 unit tests. See [INTEGRATIONS_SDK.md §3.8](docs/INTEGRATIONS_SDK.md).
- **Integrations SDK HTTP + FHIR helpers** (`integrations/sdk/http.py`, `integrations/sdk/fhir.py`): token-aware `http_request` (Bearer inject, retry/backoff, SDK exception mapping) + `paginate_bundle` (follows `link[rel=next]`); and `fhir_search`, `fhir_observation_to_create`, `parse_operation_outcome`. Reused by Stage 2 (client) and the future Stage 3 (facade). 19 unit tests.
- **FHIR Server integration** (`integrations/fhir_server/`): SMART Patient/Standalone Launch connect + **bounded FHIR search pull**. Each sync runs `Observation?patient=<remote>&_lastUpdated=gt<cursor>&_count=100&_sort=_lastUpdated` (+ optional `category`), maps each FHIR Observation to the local patient via `ObservationCreate`, and feeds the Biomarker Engine. Push is deferred to Stage 2b.
- **Platform OAuth round-trip**: `POST /{domain}/oauth/start` + `GET /{domain}/oauth/callback` (generic; secured by a one-shot Redis `state`). New `BaseConfigFlow.is_oauth` flag and `BaseHealthProvider.begin_oauth` / `complete_oauth` hooks (opt-in; existing integrations untouched).
- **Enable/disable integration script** (`backend/scripts/enable_integration.py`): headless toggle for a system integration domain (dev/CI equivalent of the admin UI).
- **FHIR Server auth modes**: per-instance `auth_mode` (`smart` | `none`). `smart` runs the full SMART round-trip (hospitals/sandbox); `none` is **tokenless** — for local/open FHIR servers (e.g. a vanilla HAPI FHIR, which has no SMART module and 404s on `/.well-known/smart-configuration`); the instance goes straight to `ACTIVE`. `fhir_search` is decoupled from `SmartOAuth` (takes an optional `access_token`).
- **Docker dev stacks**: `docker/docker-compose.dev-db.yml` (Postgres+TimescaleDB on 5433 + Redis on 6379, ports pinned to match `backend/.env`) and `docker/fhir-test-server/docker-compose.yml` (local HAPI FHIR R4 on `${HAPI_PORT:-8080}` for offline Stage 2 testing). `docker/init-test-db.sh` auto-creates `health_assistant_test`. See [docker/README.md](docker/README.md).
- **FHIR seed script** (`backend/scripts/seed_fhir_server.py`): POSTs a Patient + LOINC-coded lab/vital sample Observations (glucose, lipids, CBC, BP, HR, SpO₂, …) with reference ranges, spread over N months, to a FHIR server for testing the pull path.
- **Export & Import (Backup) System**: Comprehensive data export and import at patient/group/system scopes with three formats (FHIR R4B Bundle, full BagIt-style ZIP backup, catalog-only). Includes FHIR validation via `fhir.resources`, SHA256 manifest verification, cross-tenant id remapping, and a Celery-driven async job system. Admin-only UI at `/settings/export-import` with export configuration, drag-and-drop restore, live job polling, and detailed job modals. See [EXPORT_IMPORT.md](docs/EXPORT_IMPORT.md).
- **FHIR Converter** (`services/fhir_converter.py`): Bidirectional ORM ↔ FHIR R4B conversion for Patient, Observation, MedicationStatement, AllergyIntolerance, DiagnosticReport, Organization, Practitioner.
- **Modal component** (`components/ui/Modal.tsx`): Reusable accessible modal with Portal, ESC-to-close, overlay click, and scroll lock.

### Changed
- **Dev processes now run under [honcho](https://github.com/nickstenning/honcho)** via `Procfile.dev`. `scripts/run-dev.sh` keeps the bootstrap (venv/deps/migrations/admin/frontend-deps/Redis-preflight) but replaces manual PID tracking + cleanup trap with `exec honcho start`. Single Ctrl+C stops everything cleanly; if any process crashes, honcho stops the whole group so the error is impossible to miss (instead of jobs silently queuing in PENDING).
- **Flower added at http://localhost:5555** for dev visibility (parity with the docker-compose Flower service). `flower==2.0.1` was already in requirements.
- `--force-celery` flag deprecated (no-op with backward-compat message; honcho owns the worker lifecycle now). `--force-stop` now also kills `honcho start` and port 5555.
- **Production Docker hardening**:
  - **`beat` service added** to both `docker-compose.yml` and `docker-compose.prod.yml`. Previously silent broken state in Docker deploys — periodic tasks (`cleanup_stuck_extractions`, `check_notification_triggers`, `sync_active_integrations`) never fired, breaking medication reminders, recurring notifications, stuck-exam cleanup, and integration auto-sync.
  - **Migrations moved out of the backend container** into a one-shot `migrate` service (`restart: "no"`, runs `alembic upgrade head`, exits). Backend/worker/beat depend on it with `condition: service_completed_successfully`. Fixes the parallel-replica race + boot-coupling.
  - **Flower now requires HTTP basic auth** (`--basic-auth=${FLOWER_USER}:${FLOWER_PASSWORD}`) and binds to `127.0.0.1` by default. Previously exposed unauthenticated on all interfaces — anyone with network access could inspect task payloads + retry jobs.
  - **Healthchecks** added for backend (`curl /health`), worker (`celery inspect ping`), flower (`curl /`). Combined with `init: true` for proper PID 1 signal handling.
  - **Resource limits** on backend (1G/1cpu) and worker (2G/2cpu) via `deploy.resources.limits`. Worker `--concurrency=${CELERY_WORKER_CONCURRENCY:-2}` + `--max-tasks-per-child=${CELERY_MAX_TASKS_PER_CHILD:-100}` to release memory leaked by ML/OCR libraries.
  - **Log rotation** on every service (`max-size: 10m`, `max-file: 3` via YAML anchor). Without this, `json-file` logs grow unbounded.
  - **All ports bind to `127.0.0.1` by default** (`BACKEND_BIND`, `FLOWER_BIND`, `FRONTEND_BIND` env overrides). Forces reverse-proxy use in prod.
  - `SECRET_KEY` + `INTEGRATION_SECRET_KEY` now passed through to worker/beat/flower via a shared `*backend-env` YAML anchor (previously missing — broke integration sync decryption + JWT issuance inside worker tasks).
  - `curl` added to both Dockerfiles for healthchecks.
  - `.env.example` rewritten with grouped sections and inline generator commands.
  - Removed obsolete `version: '3.8'` from both compose files (modern Compose spec ignores it).
- **Biomarker Citations & Telemetry Tooling**: The system prompt now strictly enforces referencing biomarkers by their `id` (UUID) instead of `slug`. Backend tools (`get_biomarker_history` and `get_aggregated_biomarker_trends`) have been updated to accept `id` instead of `slug` to prevent collision bugs.
- **AI Chatbot Biomarker Tool**: `search_available_biomarkers` tool was upgraded from unindexed Regex (`~*`) to indexed trigram search, improving performance and accuracy.
- **`ChatbotTools` examination context**: `ChatbotTools` now accepts `examination_id`, threaded from the chat context in both `_stream_chat` and `_general_chat`, so the propose/inspect tools can target the active examination.
- **Write-time FHIR validation (FHIR architecture Stage 1.1)**: every `fhir_service.create_*` / `update_*` now calls `assert_valid_fhir()` before persisting, so invalid FHIR can never be stored — the root-cause fix for the shape-drift bug class. A dedicated handler maps `FhirSerializationError` → HTTP 400 (more specific than the global 500). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) § "Data Serialization & FHIR Interoperability".
- **Headless `AddBiomarkerForm`**: extracted the biomarker-entry form out of `AddBiomarkerModal` into a reusable headless component (catalog search + FHIR Observation build + prefill path), shared by the manual modal and the HITL `AddBiomarkerHandler`. `AddBiomarkerModal` is now a thin portal wrapper.
- **Doctor `address` schema is FHIR-correct**: `DoctorResponse` / `DoctorCreate` / `DoctorUpdate.address` is now `Optional[List[Address]]` (FHIR `Practitioner.address` is `0..*`). The frontend reads `address?.[0]` (mirroring `Organization`). Previously single-typed, which conflicted with FHIR-list-shaped storage.
- `Patient.to_dict()` returns the primary name as a single `HumanName` object (frontend contract); `Patient.to_fhir_dict()` emits `List[HumanName]` (FHIR). Storage shape (dict|list) is normalized on read via `_primary_human_name` / `_coerce_human_name_list`.
- `Observation.to_fhir_dict()` cleans `valueQuantity` (`_clean_quantity`): drops empty-string `unit`/`code`/`system` and keeps the `system`+`code` pair intact.
- `Medication.to_dict()` / `to_fhir_dict()` use `_enum_value` for `status` (tolerates enum-or-string pre-flush state).
- Replaced the stub `ImportService` (in-memory, non-persisting) with a real DB-backed service that validates FHIR resources and upserts by natural key.
- `Observation.to_dict()` now includes `subject`, `performer`, `comment`, and `value_codeable_concept` (previously missing FHIR fields).
- `DiagnosticReport` now has a `to_dict()` method (was missing).
- Export/import services use the resolved `UPLOAD_DIR` from `document_service_db.py` (with fallback chain) instead of `settings.UPLOAD_DIR` directly.

### Fixed (FHIR conformance — runtime)
- **SDK-built Observations no longer silently dropped by FHIR validation.** `ObservationBuilder.build()` stripped tzinfo "for asyncpg compat" (asyncpg handles tz-aware natively), so `isoformat()` produced e.g. `'2026-06-20T22:39:56.471381'` which failed the FHIR R4 regex. Every pulled observation from `dev_dummy` (and any future SDK provider) was being dropped by `assert_valid_fhir`. The builder now keeps tzinfo; new `fhir_isoformat()` helper on the ORM side defends against naive datetimes anywhere else. The dropped-count now also surfaces to the integration UI: the manual-sync response and `IntegrationSyncLog` carry `dropped_invalid` / `status="partial"` instead of reporting a silent "success" with zero metrics.

### Fixed (legacy)
- **HITL tasks lost on stream interruption**: tasks were only saved at the END of the `_chat_stream` generator. If the stream was interrupted (LLM error, client disconnect) after the `[HITL_TASK]` SSE chunk but before completion, the task was never persisted — breaking `/resolve` (404) and `/resume`. Tasks are now **proactively saved** the moment they're detected; `update_message_fields` patches the final content when the stream completes normally.
- **`"object async_generator can't be used in 'await' expression"`**: `resume_after_hitl` is an async generator (uses `yield`) — the `/resume` endpoint was incorrectly awaiting it. Fixed: `async for chunk in service.resume_after_hitl(...)` (no await).
- **`_general_chat` missing HITL system prompt**: the non-streaming chat path had no HUMAN-IN-THE-LOOP section in its system prompt, despite running the same HITL tool-call branch. Now mirrors `_chat_stream`.
- **`GET /api/v1/doctors` 500** (`ResponseValidationError: ... 'address' Input should be a valid dictionary ... input: [{...}]`): root-caused to a Pydantic schema / FHIR cardinality mismatch (the schema typed `address` single while stored JSONB + FHIR use a list). Fixed at the schema level (see "Changed") rather than masked by a silent coercing validator. The FHIR cardinality + fail-loud rules are now documented in [ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **FHIR export abort on `name`/`valueQuantity.code`**: legacy shape drift in stored JSONB. Export is fail-loud by design (`ExportError`); the serializers now normalize via `_coerce_human_name_list` / `_clean_quantity`.
- **`UniqueViolationError` on `Patient.mrn`**: empty-string `mrn` collided under the UNIQUE constraint (Postgres treats `''` as equal, unlike `NULL`). `create_patient`/`update_patient`/`import_service` now normalize `mrn` → `NULL` for empty/whitespace.
- **Frontend blank page** (`patient.name.given is undefined`): 3 non-defensive name-access sites (`PatientDetail`, `MedicationList`, `CalendarPage`) hardened with optional chaining.
- **Enum value mismatch**: `ExportScope` and `ExportType` enums now use `values_callable` so SQLAlchemy sends lowercase values (`patient`) matching the DB enum, not the uppercase names (`PATIENT`).
- **FHIR Observation.category dropped on pull**: FHIR `category` is `0..*` (a list) but `ObservationCreate.category` stores a single dict; `fhir_observation_to_create` now coerces via `_first_codeable_concept`. Previously every categorized observation failed validation and was silently skipped (0 pulled from a real server).

### Removed (dead code)
- **`POST /import` + `GET /import/status/{id}` placeholder routes.** Both were legacy stubs that returned redirect messages ("Use POST /import/backup, /import/fhir, …" / "Legacy in-memory jobs are no longer tracked"). Zero frontend callers (all imports go through `/import/backup` and `/import/jobs/{id}`); zero test coverage.
- **`UnitConverter` class** (`backend/app/services/unit_converter.py`, 81 lines) — defined but zero callers anywhere in `backend/app`, `backend/tests`, or `backend/scripts`. Deleted.
- **`services/__init__.py` re-exports** — `UnitConverter`, `AnomalyDetector`, `MedicationInteractor`, `NotificationService` were re-exported but no caller imported via the package; everyone uses direct path imports (`from app.services.anomaly_detector import AnomalyDetector`). File emptied (kept as package marker) with a comment explaining the convention.
- **Frontend deps `graphql` and `graphql-request`** removed from `package.json`. The `api/graphql.ts` client was deleted in 0.3.0-alpha (zero production callers); the dependency entries were left behind. Lockfile updated.

### Changed (API surface consolidation)
- **Deprecated the misleadingly-named `/fhir/*` ORM-shape router.** Patient/Observation CRUD moved to proper domain endpoints (`/patients/*`, `/observations/*`); medication create consolidated under `/medications/*` (new `GET /medications/{id}` for citation lookups). The `/fhir/R4/*` facade is now clearly the interop-only surface; the frontend uses domain endpoints. The frontend's `types/fhir.ts` was split into `types/patient.ts` + `types/observation.ts` (the old name was misleading — these types mirror ORM-shape, not FHIR R4). `services/fhirService.ts` was split into `patientService.ts` + `observationService.ts`.
- **Dead code removed**: GraphQL client (`api/graphql.ts`, defined but zero production callers); 6 unused `/fhir/*` routes (DiagnosticReport CRUD, duplicate Medication create/list, Observation history); the legacy single-layout `PUT /patients/{id}/layout` slot is still available but the live dashboard uses the per-user `/patients/{id}/layouts/*` routes.

### Docs
- `docs/DEVELOPMENT.md` — Flower URL, honcho flow, manual-start warning that running `uvicorn` standalone silently breaks background jobs.
- `docs/INSTALL.md` — security checklist updated with `FLOWER_USER`/`FLOWER_PASSWORD`, `BACKEND_BIND`, reverse-proxy requirement.
- `docs/STATUS.md` — added note that the comprehensive 2026-06-22 re-audit superseded the "29 items resolved" claim (110 new findings: 25C/32H/30M/23L), tracked in the local stabilization plan.
- `docs/PROJECT_STRUCTURE.md` — clarified `docker-compose.yml` (dev/staging) vs `docker-compose.prod.yml` (prod); added `Procfile.dev`.
- Project docs — fixed false claim that `main.py` has "celery auto-heal" (`check_and_start_celery()` was removed; celery is managed by the process supervisor). Documented the honcho dev workflow.

### Operational notes for deploy
1. **Set `INTEGRATION_SECRET_KEY`** (Fernet key) in `.env`. Generate one with:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. **Run the api_key backfill** after deploying:
   ```bash
   cd backend && PYTHONPATH=. python scripts/encrypt_existing_api_keys.py --dry-run
   cd backend && PYTHONPATH=. python scripts/encrypt_existing_api_keys.py
   ```
3. **Run migration `f1a2b3c4d5e6`** (`alembic upgrade head`) — creates the previously-dropped `idx_ai_models_provider_active` index.
4. **Frontend follow-up recommended**: `ProviderManager.tsx` should surface the new `has_api_key` field for cleaner edit UX (currently the masked value pre-fills the edit input — functional, but not ideal).
5. **Set `POSTGRES_PASSWORD`** explicitly in `.env` — the insecure `admin123` default was removed. Production boot will refuse known-weak values.
6. **Webhook HMAC**: integrations that want HMAC verification should set `user_config["webhook_secret"]` to a strong random string. The sender must compute `HMAC-SHA256(secret, raw_body)` and send the hex digest in the `X-Webhook-Signature` header.
7. **WebSocket frontend migration**: update the frontend `useWebSocket` hook to connect with `new WebSocket(url, ["bearer", token])` so the token travels via the subprotocol instead of the query string. The query-string fallback remains for backward compatibility.
8. **AuditLog**: the `audit_logs` table is now populated for FHIR create/delete operations. Query it for provenance: `SELECT * FROM audit_logs WHERE resource_type = 'Observation' ORDER BY created_at DESC`.

### Tests
- **205 new tests** across the full security pass: 87 from the first pass + 118 from the second pass. See the changelog below "Branch progress" sections for the file/coverage matrix.

### Post-rc.1 batch (32 commits since `v0.3.0-rc.1`)

#### Added — tenant administration & switching
- **Tenant admin service + endpoints** (`backend/app/services/tenant_admin_service.py`, `backend/app/api/v1/endpoints/admin_tenants.py`): comprehensive admin management for tenants — create/update/suspend/delete, member management, stats. 546 new tests in `test_admin_tenants.py`.
- **Tenant switcher UI** (`frontend/src/components/layout/TenantSwitcher.tsx`, `TenantSwitchBanner.tsx`, `store/slices/tenantSwitchSlice.ts`): SYSTEM_ADMIN can switch active tenant context from the header dropdown; state synced from JWT; re-switching allowed. `SYSTEM_ADMIN` treated as a global role, not a tenant member.
- **Tenant detail page** (`frontend/src/pages/Admin/TenantDetail.tsx`): full tenant management UI with members, stats, and settings.
- **Tenant management page** (`frontend/src/pages/Admin/TenantManagement.tsx`): expanded list view with create/suspend/delete actions.

#### Added — deployment & CI
- **Standalone production flavor** (`docker/docker-compose.standalone.yml`): all-in-one production deployment (backend + worker + beat + flower + frontend + nginx) for single-host installs. Production-ready `nginx.conf` with SSE and WebSocket support.
- **GHCR Docker publishing** (`.github/workflows/docker-publish.yml`): GitHub Action builds and publishes images to `ghcr.io`; images renamed to `health-assistant-*` (hyphen consistency); `latest` tag applied to main branch.
- **Environment setup script** (`scripts/setup_env.py`): interactive Python script to auto-generate `SECRET_KEY` + `INTEGRATION_SECRET_KEY` and write `.env` from `.env.example`.
- **Renamed dev compose** — `docker-compose.yml` → `docker/docker-compose.dev.yml` for clarity against the standalone/prod flavors.

#### Added — frontend UX
- **User profile page** (`frontend/src/pages/Account/MyAccount.tsx`): identity, linked patient/doctor records, account metadata; accessible to all authenticated users via avatar dropdown.
- **Settings sidebar layout** (`frontend/src/components/settings/SettingsLayout.tsx`): dedicated sections (Preferences, Security, Appearance, AI Config, Integrations, Notifications, Export/Import). Settings menu group removed from main sidebar.
- **NoPatientState component** (`frontend/src/components/ui/NoPatientState.tsx`): reusable empty-state for all patient-scoped pages — adapts to tenant state (prompts selection / guides creation on fresh instances). Rolled out across Dashboard, Clinical Alerts, Calendar, Medications, Events, Documents, Examinations, Biomarker Trends, Correlative Analytics, Biomarker Detail, Notifications, Integrations.
- **Smart dashboard layout generator** — overview / vitals / clinical / compact presets; auto-saves generated layouts; improved packing algorithm.
- **Dashboard card registry + 4 new cards** (`frontend/src/components/dashboard/cardRegistry.ts`): `HealthSummaryCard`, `MultiBiomarkerComparisonCard`, `RangeGaugeCard`, `AnomalyCard`. Old `Alerts`/`RecentDocuments`/`VitalStats` cards replaced.
- **Patient detail summary cards** — replaced tabbed PatientDetail with rich summary cards: `BiomarkerSummary`, `ClinicalEventSummary`, `ExaminationSummary`, `ScheduleSummary`, plus updated `AllergySummary` / `MedicationSummary`. Shared `SummaryCardHeader` component.
- **Reusable UI components**: `CreateMenu` (global create-action dropdown), `OpenPageButton`, `InfoTooltip` (refactored), `useBiomarkerChange` + `useCreateIntent` hooks.
- **Default dashboard layout refined** — Unified Health Calendar, Clinical Alerts, Biomarker Trends, Latest Documents widgets by default; aligned with `seed_demo.py` data.
- **UI presentation tooling** (`scripts/capture_ui.sh`, `frontend/tests-e2e/ui-capture/`): Playwright-based screenshot pipeline; `docs/images/` populated with desktop screenshots + visual tour GIF; `docs/UI_CAPTURE_PIPELINE.md` + `docs/SCREENSHOTS.md` added.
- **Preferences page** (`frontend/src/pages/Settings/Preferences.tsx`) and **Security page** (`frontend/src/pages/Settings/Security.tsx`) — extracted from the removed `Profile.tsx`.

#### Changed — environment configuration
- **Single root `.env` consolidation** (`c5b6be7`) — eliminated the `backend/.env` vs root `.env` drift. `config.py` now uses `_resolve_env_file()`: 3-tier precedence (`HA_ENV_FILE` env var → walk-up from `__file__` to nearest `.env` → None for docker/prod). `backend/.env` and `backend/.env.example` deleted. `run-dev.sh` exports root `.env` so direct-uvicorn/IDE/script launches work without honcho. Docker compose files pass previously-missing env vars (`VAPID_ADMIN_EMAIL`, `MCP_*`, `UPLOAD_DIR=/app/uploads`, `AI_AGENT_MAX_ITERATIONS`, `APP_URL`).
- `.env.example` relocated to repo root; rewritten with grouped sections + inline generator commands.
- `docs/INSTALL.md` simplified — Quickstart focuses on standalone production path; dev setup moved to `docs/DEVELOPMENT.md`.
- `docker/README.md` refactored to remove redundant installation info; FHIR docker directory renamed to `docker/fhir-test-server/` for clarity.

#### Fixed
- **`create_system_admin.py` slug + error handling** — initial tenant now gets a generated slug; error messages point to root `.env`.
- **Docker compose env + seeding issues** — optional variables given empty fallback defaults (suppresses warnings); lowercase enum bug in `allergies.json` seed fixed.
- **Database password validation** — `config.py` now validates the password directly from `DATABASE_URL` (not just `POSTGRES_PASSWORD`).
- **Postgres hostname in Docker for Alembic** — properly resolved for migration runs inside containers.
- **Dashboard infinite loading on fresh instances** — `loadLayout` early-return left `isLoading` stuck true; fixed.
- **PWA update toast during screenshot capture** — suppressed when running the UI capture pipeline; native PDF viewer toolbars disabled via `#toolbar=0&navpanes=0` in embedded iframes.
- **Mini chart dots** — visible dot now rendered for single-point biomarker mini charts.
- **`/users/me` 404 for switched SYSTEM_ADMIN** — fixed tenant context resolution.

#### Docs
- `docs/TENANCY_AND_USER_MANAGEMENT.md` — replaced false three-tier hierarchy with accurate data model (recursive Organizations, peer entities, correct `User→Patient/Doctor` 1:N and `Dept↔Doctor` N:N cardinalities).
- `docs/DEVELOPMENT.md` — docker compose command fix, manual dev setup instructions.
- `docs/INSTALL.md` — separated bash commands into individual blocks, clarified AI settings as optional fallbacks, Buy Me a Coffee link in README.
- `docs/SEEDING_AND_DEMOS.md`, `docs/UI_CAPTURE_PIPELINE.md`, `docs/SCREENSHOTS.md` — new docs for the presentation pipeline.

---

## [0.3.0-rc.1] - 2026-06-21

**Release candidate — cleanup pass following the API surface consolidation in 0.3.0-alpha.**

### Fixed
- **RBAC gate on global examination categories.** `PATCH /examination-categories/{id}` previously had a `# TODO: Implement super-admin check here` placeholder; tenant admins could mutate global (`tenant_id=None`) categories. The route now requires `Role.SYSTEM_ADMIN` for global-category mutations. Tenant-scoped categories are unaffected. Locked in by 7 new tests in `test_examination_categories_endpoints.py`.
- **`test_import_backup_task_runs_service` no longer fails.** The test's `FakeImportService.run_import` was missing the `config=None` kwarg that the real `ImportService.run_import(job_id, archive_path, owner_id, config=None)` requires. The fake raised `TypeError` inside the task wrapper, returning `status="failed"`. Full backend test suite now green: 776 passed.

### Removed (dead code)
- **`POST /import` + `GET /import/status/{id}` placeholder routes.** Both were legacy stubs that returned redirect messages ("Use POST /import/backup, /import/fhir, …" / "Legacy in-memory jobs are no longer tracked"). Zero frontend callers (all imports go through `/import/backup` and `/import/jobs/{id}`); zero test coverage.
- **`UnitConverter` class** (`backend/app/services/unit_converter.py`, 81 lines) — defined but zero callers anywhere in `backend/app`, `backend/tests`, or `backend/scripts`. Deleted.
- **`services/__init__.py` re-exports** — `UnitConverter`, `AnomalyDetector`, `MedicationInteractor`, `NotificationService` were re-exported but no caller imported via the package; everyone uses direct path imports (`from app.services.anomaly_detector import AnomalyDetector`). File emptied (kept as package marker) with a comment explaining the convention.
- **Frontend deps `graphql` and `graphql-request`** removed from `package.json`. The `api/graphql.ts` client was deleted in 0.3.0-alpha (zero production callers); the dependency entries were left behind. Lockfile updated.

### Changed
- **Stale docstrings refreshed** in `backend/app/api/v1/endpoints/fhir_r4.py` and `backend/app/facade/__init__.py` — both still referenced "the legacy ORM-shape `/fhir/*` router (which the frontend keeps using)" after the router was deleted in 0.3.0-alpha. Now describe the facade as the interop-only surface.

---

## [0.2.0] - 2026-06-14

### Added
- **AI Telemetry Integration**: Enabled the AI Chatbot to access high-frequency telemetry data (heart rate, steps, etc.) stored in TimescaleDB using new aggregated tools.
- **Biomarker Discovery Tool**: Added `search_available_biomarkers` tool to the AI with regex support, allowing the LLM to identify correct metric slugs and data types (Telemetry vs Clinical).
- **Aggregated Trends Tool**: Created `get_aggregated_biomarker_trends` providing OHLC (Open-High-Low-Close) data to the AI, ensuring context window protection via record limiting.
- **Configurable Reasoning Loop**: Moved the hardcoded AI reasoning loop limit to a multi-tiered configuration system (Global Default -> System DB -> Tenant Override).
- **Persistent System Settings**: Implemented a `SystemSetting` database table and model for managing global application configurations via the UI.
- **AI Agent Admin UI**: Added a new "Agent Settings" management tab in the AI Configuration pages for both System and Tenant scopes.

### Changed
- Refactored `AnalyticsService.get_biomarker_trends` to support explicit `start_date` and `end_date` parameters for both clinical and telemetry data.
- Optimized AI tool-calling logic to prefer exact matches and prevent cross-metric data contamination (e.g., Heart Rate vs HRV).
- Updated AI System Prompts with strict biomarker routing rules and discovery-first logic.
- Bumped the default AI reasoning loop limit to 20 iterations.

### Fixed
- **Audit Column Mismatch**: Resolved a `ProgrammingError` by adding missing `created_by` and `updated_by` columns to the `system_settings` table via migration.
- **Fuzzy Match Hijacking**: Fixed a bug where substring matching in biomarker trends would return wrong data if a requested slug was a substring of another metric.
- **Analytics Endpoint Argument Mismatch**: Fixed a bug where positional arguments were incorrectly passed to `get_biomarker_trends` in the analytics endpoint, causing empty results due to a missing database session.
- **Timezone Inconsistency**: Fixed a `TypeError` (offset-naive/aware conflict) by standardizing all clinical timestamp columns (`effective_datetime`, `onset_date`, etc.) to use `TIMESTAMPTZ` via database migration and model updates.

---

## [0.1.0] - 2026-06-10

### Added
- **Centralized Versioning System**: Implemented a single source of truth for versioning loaded dynamically in FastAPI and root health endpoints.
- **Project Versioning Manager**: Created a unified CLI script (`scripts/version_manager.py`) to easily query, set, or bump semantic versions across all backend, frontend, and document files.
- **Reusable AppVersion Component**: Designed a theme-compatible, responsive `<AppVersion />` UI component for displaying the semantic version in both the Sidebar and the Login page.
- **Household-First Multi-Tenancy**: Added zero-config multi-tenant setup for home environments where the first registered user auto-scales to `SYSTEM_ADMIN` and households are dynamically isolated.
- **Identity & Record Linking**: Implemented dynamic linking connecting system login accounts to patient and doctor clinical records.
- **Biomarker-Event Bindings**: Programmed structural links connecting qualitative clinical events (like "Myopia") directly to quantitative biomarker observations (like "Visual Acuity").
- **Anatomical Body Part Mapping**: Expanded clinical events with physical anatomical coordinates (`BodyPartModel`) allowing symptoms and findings to bind to specific body systems.
- **Universal Health Calendar**: Developed an interactive health calendar component that integrates medical visits, medication timelines, and chronic event logs.
- **Drag-and-Drop Dashboards**: Added modular dashboard panels (`VitalStats`, `TrendsCard`, `AllergyAlertsCard`) with persistent grid locations saved to Zustand.

### Changed
- Refactored `Sidebar.tsx` and `Login.tsx` to consume the new `<AppVersion />` component.
- Updated core backend configurations to declare legacy API keys/variables as safe local fallback configurations.
- Audited and sanitized the repository in preparation for public release on GitHub, including masking potential secret keys/domains in documentation.
- Standardized all clinical and system enums to uppercase characters with automated database migration.
- Restructured frontend layouts using responsive `MasterDetailLayout` and `StickyToolbar` wrappers for optimized mobile viewing.

### Fixed
- **Pytest Suite Refactoring (92/92 Passing)**: Expanded tests count from 41 to 92, fixing database connection leaks, testing mock typings, and isolating conftest sessions.
- **Container Testing Sidecars**: Integrated PostgreSQL and Redis database service sidecars directly into testing workflows to eliminate dependency issues.
- **WebSocket & CORS Blockers**: Replaced local loopback connections with relative endpoints and configured Vite servers to allow binding over any local router hosts.
- **PageHeader Rendering**: Solved recursive rendering loop in PageHeader and optimized Zustand state subscriptions to increase application responsiveness.

---

## 2026-03-24

### Added
- **Asynchronous Notification Framework**: Implemented a modular system for handling medical reminders, clinical alerts, and system events.
- **Web Push (VAPID) Integration**: Added support for native browser notifications in the PWA, allowing alerts to be received even when the app is closed.
- **Automated Medication Reminders**: The system now automatically generates recurring notification triggers when new medications are prescribed.
- **Biomarker Event Hooks**: Integrated real-time event triggers for new observations, enabling future threshold-based alarms (e.g., high glucose).
- **Notification Center**: A new reactive global UI component with unread badges and status management for in-app messages.
- **Notification Management Page**: Dedicated dashboard to view active scheduled triggers (Next Run times) and historical delivery logs.
- **Smart Reminders UI**: Added a dedicated management component to the Medication detail page for creating custom alarms.
- **Custom Service Worker**: Implemented `sw.ts` with `injectManifest` to handle background push payloads and interaction logic.

### Changed
- Migrated from legacy `Alerts` system to the new `NotificationTrigger` and `Notification` models for better scalability and FHIR compliance.
- Updated PWA configuration to support advanced background capabilities.

## 2026-03-23

### Added
- **Clinical Events**: Introduced a comprehensive system for tracking longitudinal health narratives such as pregnancies, chronic pain, dental treatments, and surgical recoveries.
- **Global Events Dashboard**: A new top-level menu item and page providing a cross-patient view of all ongoing and historic clinical events.
- **Interactive Episode Tracking**: Events now support high-precision logging of individual occurrences (episodes) with date, time, intensity (1-10), and body location.
- **Type-Specific Metadata**: specialized schemas for different event types (e.g., Gestational Age for Pregnancy, Mechanism of Injury for Accidents, Diopters for Vision).
- **Bi-directional Visit Mapping**: Seamlessly link examinations to clinical events with specific clinical reasons. Associations can be managed directly from the Examination edit mode.
- **Enhanced Internationalization**: Full English and Greek support for all clinical event labels, placeholders, and status indicators.

### Changed
- Refactored `AssociatedEvents` into a modular, reusable component with "Compact" and "Detailed" rendering modes.
- Updated `PatientDetail.tsx` to support routable tabs via URL (`/history` and `/events`).
- Improved navigation consistency: clicking event badges in any list now navigates to the specialized Event Detail page.

### Fixed
- **Timezone Aware Dates**: Resolved `DataError` (offset-naive/aware conflict) by making all clinical event date columns timezone-aware in PostgreSQL.
- **Medication Modal Typings**: Fixed a critical frontend bug where the `onClose` handler was incorrectly typed as a boolean.
- **Build Integrity**: Cleaned up numerous TypeScript errors and unused imports across core frontend components.

## 2026-03-11

### Added
- **Global Patient Management**: Top-level header now contains a strict Patient selection dropdown that dynamically filters all dashboards, charts, and document views across the entire application context.
- **Examinations Platform**: Documents are now grouped categorically by individual medical visit instances ("Examinations"). Added new Models, Migrations, and CRUD endpoints to support this.
- **Rich-Text Medical Notes**: Doctors/Users can now write, edit, and save full HTML markdown notes directly into an Examination using a WYSIWYG editor (`react-quill`).
- **Dynamic AI Visualizer Factories**: Examinations page now dynamically mounts specific React UI components (Lab Results Table, Dual-Pane Imaging viewer) depending on what type of document the AI identified inside the visit.
- **DICOM (.dcm) Support**: The backend now natively extracts binary metadata from RAW DICOM files, converts the pixel matrix to an internal image buffer, and serves it seamlessly inline to the browser.
- **AI Categorization Framework**: The background OpenAI OCR agent now automatically categorizes any uploaded document (or unstructured image) into clinical buckets (e.g. Ophthalmology, Cardiology, Laboratory Tests, etc.) with safe fallbacks (`Other`).

### Changed
- Refactored `Dashboard.tsx` to automatically pull unique Biomarkers directly from the user's historical AI dataset.
- Refactored `/api/v1/users/me` endpoint to hit the database dynamically and construct full JWT tokens injected with correct `tenant_id` bindings.
- Modified File Response handlers to explicitly use `inline` content disposition and dynamic MIME guessing so PDFs/Images embed safely into frontend components instead of strictly downloading.
- Swept the `backend/` directory clean; moved all 9 stray maintenance/DB debug scripts into a dedicated `backend/scripts` folder.

### Fixed
- Fixed OpenAI JSON string parsing. (Previously if the LLM outputted ```json wrappers, it would trigger a parser failure and crash to the fallback local NLP).
- Fixed cross-chart timestamps. All FHIR observations now strictly extract their analytical graph dates from the parent `Examination` date, NOT the day the file was technically uploaded.
- Fixed 500 error in `get_patient` where the `to_dict()` serialization method was absent from the SQLAlchemy schema.
- Silenced numerous Pylance `MockResult` redeclaration warnings in testing suites.
- All 41 backend automated endpoints tests are currently passing natively (`100%` coverage on new workflows).
