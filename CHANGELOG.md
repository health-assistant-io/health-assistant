# Changelog

## [Unreleased]

### Added
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
- `Patient.to_dict()` returns the primary name as a single `HumanName` object (frontend contract); `Patient.to_fhir_dict()` emits `List[HumanName]` (FHIR). Storage shape (dict|list) is normalized on read via `_primary_human_name` / `_coerce_human_name_list`.
- `Observation.to_fhir_dict()` cleans `valueQuantity` (`_clean_quantity`): drops empty-string `unit`/`code`/`system` and keeps the `system`+`code` pair intact.
- `Medication.to_dict()` / `to_fhir_dict()` use `_enum_value` for `status` (tolerates enum-or-string pre-flush state).
- Replaced the stub `ImportService` (in-memory, non-persisting) with a real DB-backed service that validates FHIR resources and upserts by natural key.
- `Observation.to_dict()` now includes `subject`, `performer`, `comment`, and `value_codeable_concept` (previously missing FHIR fields).
- `DiagnosticReport` now has a `to_dict()` method (was missing).
- Export/import services use the resolved `UPLOAD_DIR` from `document_service_db.py` (with fallback chain) instead of `settings.UPLOAD_DIR` directly.

### Fixed
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
