# Changelog

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
