# Changelog

## [0.3.0-alpha] - 2026-06-21

**Critical & high-severity security fixes. Breaking changes — see details below.**

### Security
- **AI provider `api_key` is now encrypted at rest** (Fernet, via `INTEGRATION_SECRET_KEY`) and **masked in every API response** (`***<last4>`, plus a new `has_api_key` boolean). Plaintext keys persisted before this release must be migrated with `PYTHONPATH=. python scripts/encrypt_existing_api_keys.py` (supports `--dry-run`, idempotent). The factory layer reads plaintext only via the new `AIProviderModel.get_api_key_plaintext()` accessor at LLM-instantiation time.
- **Scope checks on `/ai-config/providers/{id}`, `/providers/{id}/with-models`, `/providers/{id}/models`, `/models/{id}`, `/providers/{id}/fetch-external-models`** — previously any authenticated user could read any provider (including its key) by UUID. New `verify_provider_access` / `verify_model_access` helpers enforce USER/TENANT/SYSTEM scope on all 7 entry points.
- **`fetch-external-models` SSRF guard**: in production (`DEBUG=False`) the `api_base` must be `http(s)://` and not point at a loopback / private / link-local address.
- **Telemetry endpoints are now tenant-scoped**. `/telemetry/data`, `/telemetry/data/summary`, and `/telemetry/anomalies` previously accepted only `device_id`; a user who guessed another tenant's `device_id` could read its data. All three endpoints now require and filter on the caller's `tenant_id`.
- **Global exception handler no longer leaks `str(exc)` in production**. 500 responses now include a `correlation_id`; the full detail is logged server-side with the same id. `DEBUG=True` preserves the verbose detail for developer convenience.

### Fixed (critical)
- **`list_observations` now applies its filters.** The function accepted `patient_id`/`code`/`start_date`/`end_date` but silently ignored them, returning every observation in the tenant. Cross-patient data exposure. Results are also ordered by `effective_datetime DESC`.
- **`/fhir/Observation/history` no longer raises `TypeError`.** It was calling `get_observation(patient_id, code, period)` — wrong arity. New `get_observation_history` service fn + reordered routes so `/Observation/history` is matched before `/Observation/{observation_id}`.
- **`sync_active_integrations` (background Celery task) now routes telemetry to TimescaleDB.** Every pulled observation previously landed in `fhir_observations` regardless of `BiomarkerDefinition.is_telemetry`, breaking the AI telemetry tools. New shared helper `app.services.integration_sync_service.apply_telemetry_split` — also wired into the manual-sync endpoint for DRY.
- **`/telemetry/anomalies` no longer raises `TypeError`.** Was calling the synchronous `AnomalyDetector.detect_biomarker_anomalies(device_id, metric, period)` with the wrong arity and an `await`. New wrapper `get_telemetry_anomalies` fetches history and feeds the detector correctly.

### Fixed (high)
- **`AIModel.__table_args__` typo corrected** (was missing trailing `__`, silently dropped the `idx_ai_models_provider_active` composite index). Migration `f1a2b3c4d5e6` creates the index on existing databases.
- **Dead `processors/fhir_mapper.py` deleted** — broken import (`from app.models.fhir.observation import Observation` — module doesn't exist). Would have raised `ModuleNotFoundError` if imported.
- **`from app.models import *` works again** — removed the stale `WearableDataModel` alias (renamed to `TelemetryDataModel`); `TelemetryDataModel` is now exported in `__all__`.
- **`telemetry_service.get_telemetry_data` and `get_telemetry_summary` are no longer stubs.** Real tenant-scoped `SELECT` + aggregate queries against the `telemetry_data` hypertable.

### Frontend
- **PWA manifest shortcut `/examinations/new` → `/examinations/upload`** (the actual route).
- **PWA runtime caches no longer hardcoded to `http://localhost:8000`.** Switched to same-origin + pathname predicate callbacks so caching works in any deployment.
- **GraphQL client auth header is now per-request.** New `graphqlRequest()` wrapper reads the live token on every call. Previously the header was captured at module-load and never refreshed, so every call 401'd after the first token rotation.

### Fixed (FHIR conformance — runtime)
- **SDK-built Observations no longer silently dropped by FHIR validation.** `ObservationBuilder.build()` stripped tzinfo "for asyncpg compat" (asyncpg handles tz-aware natively), so `isoformat()` produced e.g. `'2026-06-20T22:39:56.471381'` which failed the FHIR R4 regex. Every pulled observation from `dev_dummy` (and any future SDK provider) was being dropped by `assert_valid_fhir`. The builder now keeps tzinfo; new `fhir_isoformat()` helper on the ORM side defends against naive datetimes anywhere else. The dropped-count now also surfaces to the integration UI: the manual-sync response and `IntegrationSyncLog` carry `dropped_invalid` / `status="partial"` instead of reporting a silent "success" with zero metrics.

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

### P0 completion (second pass — 14 items, 118 tests)

**Security hardening:**
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

**Functional fixes:**
- **OHLC double-aggregation fixed** (`analytics_service.py`) — the raw-table path set `avg_col = "AVG(col)"` and the SQL template wrapped it in `AVG(AVG(col))` — invalid SQL, silently caught by the except handler, producing empty charts for any non-cagg-stride bucket (all sub-hour/day + 1-week/month aggregations). Now uses single-level `AVG(col)`/`MAX(col)`/`MIN(col)`. Added `_ALLOWED_TELEMETRY_BUCKETS` whitelist to guard the `INTERVAL '{bucket}'` f-string interpolation.
- **`relative_score` boundary logic fixed** (`analytics_service.py`) — `_get_observation_status` used `< 0 → Low` / `> 1.0 → High` on a value clamped to [0, 1], so every score short-circuited to Normal. Now only returns Normal for strictly-interior scores (0 < s < 1); boundary values (0.0/1.0) defer to the explicit reference-range comparison.
- **Magic Fill live date** (`ai_assistance_service.py`) — hardcoded `"Today's date is 2026-03-22."` replaced with `datetime.now(timezone.utc)` injection for both `today_iso` and `current_year`.

---

## [Unreleased]

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
- **AI Chatbot — human-in-the-loop (HITL) `add_biomarker_to_examination` proposal**: the agentic chat can propose adding a lab-result biomarker to an examination; the user reviews/edits in a card + modal and explicitly confirms before anything is saved. The AI never writes — commit flows through the canonical `POST /fhir/Observation` endpoint, FHIR-validated at write time via `assert_valid_fhir`. The target exam resolves from the open exam in the chat context **or** an explicit `examination_id` the AI resolves via `get_recent_examinations` (hard-fails on unknown / cross-patient exam). Second HITL task type after `create_clinical_event`. See [AI_SYSTEM.md §4](docs/AI_SYSTEM.md) and the `hitl-task-cards` skill.

### Changed
- **Biomarker Citations & Telemetry Tooling**: The system prompt now strictly enforces referencing biomarkers by their `id` (UUID) instead of `slug`. Backend tools (`get_biomarker_history` and `get_aggregated_biomarker_trends`) have been updated to accept `id` instead of `slug` to prevent collision bugs.
- **AI Chatbot Biomarker Tool**: `search_available_biomarkers` tool was upgraded from unindexed Regex (`~*`) to indexed trigram search, improving performance and accuracy.
- **`ChatbotTools` examination context**: `ChatbotTools` now accepts `examination_id`, threaded from the chat context in both `_stream_chat` and `_general_chat`, so the propose/inspect tools can target the active examination.
- **Write-time FHIR validation (FHIR architecture Stage 1.1)**: every `fhir_service.create_*` / `update_*` now calls `assert_valid_fhir()` before persisting, so invalid FHIR can never be stored — the root-cause fix for the shape-drift bug class. A dedicated handler maps `FhirSerializationError` → HTTP 400 (more specific than the global 500). See `dev/fhir-architecture-roadmap.md`.
- **Integrations SDK OAuth2 + SMART auth module** (`integrations/sdk/auth.py`): reusable Authorization Code + PKCE primitives, SMART discovery (`.well-known/smart-configuration`), **Dynamic Client Registration** (users enter only the server URL), encrypted `OAuthTokenStore`, Redis-backed `OAuthStateStore`, and a composed `SmartOAuth` (incl. `force_refresh` for refresh-on-401). Foundation for all cloud integrations (FHIR, Fitbit, Withings, …). 23 unit tests. See [INTEGRATIONS_SDK.md §3.8](docs/INTEGRATIONS_SDK.md).
- **Integrations SDK HTTP + FHIR helpers** (`integrations/sdk/http.py`, `integrations/sdk/fhir.py`): token-aware `http_request` (Bearer inject, retry/backoff, SDK exception mapping) + `paginate_bundle` (follows `link[rel=next]`); and `fhir_search`, `fhir_observation_to_create`, `parse_operation_outcome`. Reused by Stage 2 (client) and the future Stage 3 (facade). 19 unit tests.
- **FHIR Server integration** (`integrations/fhir_server/`): SMART Patient/Standalone Launch connect + **bounded FHIR search pull**. Each sync runs `Observation?patient=<remote>&_lastUpdated=gt<cursor>&_count=100&_sort=_lastUpdated` (+ optional `category`), maps each FHIR Observation to the local patient via `ObservationCreate`, and feeds the Biomarker Engine. Push is deferred to Stage 2b.
- **Platform OAuth round-trip**: `POST /{domain}/oauth/start` + `GET /{domain}/oauth/callback` (generic; secured by a one-shot Redis `state`). New `BaseConfigFlow.is_oauth` flag and `BaseHealthProvider.begin_oauth` / `complete_oauth` hooks (opt-in; existing integrations untouched).
- **Enable/disable integration script** (`backend/scripts/enable_integration.py`): headless toggle for a system integration domain (dev/CI equivalent of the admin UI).
- **FHIR Server auth modes**: per-instance `auth_mode` (`smart` | `none`). `smart` runs the full SMART round-trip (hospitals/sandbox); `none` is **tokenless** — for local/open FHIR servers (e.g. a vanilla HAPI FHIR, which has no SMART module and 404s on `/.well-known/smart-configuration`); the instance goes straight to `ACTIVE`. `fhir_search` is decoupled from `SmartOAuth` (takes an optional `access_token`).
- **Docker dev stacks**: `docker/docker-compose.dev-db.yml` (Postgres+TimescaleDB on 5433 + Redis on 6379, ports pinned to match `backend/.env`) and `docker/docker-compose.fhir.yml` (local HAPI FHIR R4 on `${HAPI_PORT:-8080}` for offline Stage 2 testing). `docker/init-test-db.sh` auto-creates `health_assistant_test`. See [docker/README.md](docker/README.md).
- **FHIR seed script** (`backend/scripts/seed_fhir_server.py`): POSTs a Patient + LOINC-coded lab/vital sample Observations (glucose, lipids, CBC, BP, HR, SpO₂, …) with reference ranges, spread over N months, to a FHIR server for testing the pull path.
- **Export & Import (Backup) System**: Comprehensive data export and import at patient/group/system scopes with three formats (FHIR R4B Bundle, full BagIt-style ZIP backup, catalog-only). Includes FHIR validation via `fhir.resources`, SHA256 manifest verification, cross-tenant id remapping, and a Celery-driven async job system. Admin-only UI at `/settings/export-import` with export configuration, drag-and-drop restore, live job polling, and detailed job modals. See [EXPORT_IMPORT.md](docs/EXPORT_IMPORT.md).
- **FHIR Converter** (`services/fhir_converter.py`): Bidirectional ORM ↔ FHIR R4B conversion for Patient, Observation, MedicationStatement, AllergyIntolerance, DiagnosticReport, Organization, Practitioner.
- **Modal component** (`components/ui/Modal.tsx`): Reusable accessible modal with Portal, ESC-to-close, overlay click, and scroll lock.

### Changed
- **Headless `AddBiomarkerForm`**: extracted the biomarker-entry form out of `AddBiomarkerModal` into a reusable headless component (catalog search + FHIR Observation build + prefill path), shared by the manual modal and the HITL `AddBiomarkerHandler`. `AddBiomarkerModal` is now a thin portal wrapper.
- **Doctor `address` schema is FHIR-correct**: `DoctorResponse` / `DoctorCreate` / `DoctorUpdate.address` is now `Optional[List[Address]]` (FHIR `Practitioner.address` is `0..*`). The frontend reads `address?.[0]` (mirroring `Organization`). Previously single-typed, which conflicted with FHIR-list-shaped storage.
- `Patient.to_dict()` returns the primary name as a single `HumanName` object (frontend contract); `Patient.to_fhir_dict()` emits `List[HumanName]` (FHIR). Storage shape (dict|list) is normalized on read via `_primary_human_name` / `_coerce_human_name_list`.
- `Observation.to_fhir_dict()` cleans `valueQuantity` (`_clean_quantity`): drops empty-string `unit`/`code`/`system` and keeps the `system`+`code` pair intact.
- `Medication.to_dict()` / `to_fhir_dict()` use `_enum_value` for `status` (tolerates enum-or-string pre-flush state).
- Replaced the stub `ImportService` (in-memory, non-persisting) with a real DB-backed service that validates FHIR resources and upserts by natural key.
- `Observation.to_dict()` now includes `subject`, `performer`, `comment`, and `value_codeable_concept` (previously missing FHIR fields).
- `DiagnosticReport` now has a `to_dict()` method (was missing).
- Export/import services use the resolved `UPLOAD_DIR` from `document_service_db.py` (with fallback chain) instead of `settings.UPLOAD_DIR` directly.

### Fixed
- **HITL tasks lost on stream interruption**: tasks were only saved at the END of the `_chat_stream` generator. If the stream was interrupted (LLM error, client disconnect) after the `[HITL_TASK]` SSE chunk but before completion, the task was never persisted — breaking `/resolve` (404) and `/resume`. Tasks are now **proactively saved** the moment they're detected; `update_message_fields` patches the final content when the stream completes normally.
- **`"object async_generator can't be used in 'await' expression"`**: `resume_after_hitl` is an async generator (uses `yield`) — the `/resume` endpoint was incorrectly awaiting it. Fixed: `async for chunk in service.resume_after_hitl(...)` (no await).
- **`_general_chat` missing HITL system prompt**: the non-streaming chat path had no HUMAN-IN-THE-LOOP section in its system prompt, despite running the same HITL tool-call branch. Now mirrors `_chat_stream`.
- **`GET /api/v1/doctors` 500** (`ResponseValidationError: ... 'address' Input should be a valid dictionary ... input: [{...}]`): root-caused to a Pydantic schema / FHIR cardinality mismatch (the schema typed `address` single while stored JSONB + FHIR use a list). Fixed at the schema level (see "Changed") rather than masked by a silent coercing validator. The FHIR cardinality + fail-loud rules are now documented in the `clinical-data` and `backend` skills.
- **FHIR export abort on `name`/`valueQuantity.code`**: legacy shape drift in stored JSONB. Export is fail-loud by design (`ExportError`); the serializers now normalize via `_coerce_human_name_list` / `_clean_quantity`.
- **`UniqueViolationError` on `Patient.mrn`**: empty-string `mrn` collided under the UNIQUE constraint (Postgres treats `''` as equal, unlike `NULL`). `create_patient`/`update_patient`/`import_service` now normalize `mrn` → `NULL` for empty/whitespace.
- **Frontend blank page** (`patient.name.given is undefined`): 3 non-defensive name-access sites (`PatientDetail`, `MedicationList`, `CalendarPage`) hardened with optional chaining.
- **Enum value mismatch**: `ExportScope` and `ExportType` enums now use `values_callable` so SQLAlchemy sends lowercase values (`patient`) matching the DB enum, not the uppercase names (`PATIENT`).
- **FHIR Observation.category dropped on pull**: FHIR `category` is `0..*` (a list) but `ObservationCreate.category` stores a single dict; `fhir_observation_to_create` now coerces via `_first_codeable_concept`. Previously every categorized observation failed validation and was silently skipped (0 pulled from a real server).

## [1.1.0] - 2026-06-14

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

## [1.0.0] - 2026-06-10

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
