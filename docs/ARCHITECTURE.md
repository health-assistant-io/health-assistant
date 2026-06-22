# Health Assistant - Technical Architecture

See [STATUS.md](STATUS.md) for current implementation progress and roadmap.

## Core Technologies

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python 3.12+) |
| Frontend | React 18+ (TypeScript) |
| Database | PostgreSQL + TimescaleDB |
| Cache/Queue | Redis + Celery |
| ORM | SQLAlchemy 2.0 |
| Migrations | Alembic |
| AI / NLP | Unified LangChain Factory |
| Containerization | Docker + Docker Compose |

## Database Schema

### Core Models (`app/models/`)

- **tenants**: Multi-tenant isolation (id, name, settings)
- **users**: Identity & Auth (id, tenant_id, email, role, settings)
- **fhir_organizations**: Hierarchical grouping (id, tenant_id, name, org_type, part_of_id, created_at, updated_at, deleted_at)
- **fhir_patients**: Clinical profiles (id, tenant_id, user_id, name, gender, birth_date, mrn)
- **clinical_event_categories**: Groups for health journeys (Reproductive, Acute, Specialized, etc.)
- **clinical_event_types**: Blueprint for specific journeys. Contains `metadata_schema` for dynamic field rendering.
- **clinical_events**: Longitudinal health journeys (patient_id, type_id, status, metadata, occurrences)
- **event_examination_links**: Many-to-many relationship between events and examinations with clinical reasoning.
- **examinations**: Clinical visit containers (id, patient_id, organization_id, examination_date, notes, patient_notes, category)
- **doctors**: Care team profiles (id, tenant_id, user_id, name, specialty, license_number, contact_info)
- **documents**: File tracking (id, owner_id, filename, file_path, status, progress, extracted_text, entities)
- **fhir_observations**: Biomarkers/Vitals (id, document_id, biomarker_id, raw_value, normalized_value, relative_score, effective_datetime)
- **units**: Smart units with conversion logic (id, symbol, quantity_type, conversion_multiplier)
- **biomarker_definitions**: Global catalog (id, slug, coding_system, code, name, aliases, preferred_unit_id)
- **biomarker_groups**: Clinical panels and system groupings (id, name, type)
- **laboratories**: Source tracking for lab reports (id, name, location)
- **telemetry_data**: Time-series health metrics (id, device_id, timestamp, data)
- **notification_triggers**: Scheduling and event rules for notifications
- **notifications**: Patient-specific message history (FHIR Communication)
- **notification_subscriptions**: Web Push credentials for PWA support
- **alerts**: Clinical threshold triggers (all endpoints tenant-scoped; see [API.md](API.md#alerts))

### FHIR Architecture & Biomarker Engine (`app/models/fhir/`)

The project follows the **HL7 FHIR** standard but enhances it with a high-performance **Biomarker Engine**:
- **Patient**: Demographic and administrative data.
- **Observation**: The primary model for biomarkers. Linked to a **BiomarkerDefinition** for standardized identity.
- **Dynamic Ontology**: The application uses a pluggable Clinical Ontology system. Rather than hardcoding LOINC mappings in Python, administrators can import massive custom catalogs (like the official Open Source Community Catalog via JSON). All biomarker definitions specify their exact `CodingSystem` Enum (e.g., `LOINC`, `SNOMED`, `CUSTOM`) allowing precise FHIR JSON serialization that is robust for external interoperability. (See [Ontology Catalog Schema](ONTOLOGY_CATALOG.md))
- **Normalized Value**: All measurements are automatically converted to a "System Unit" using database-driven multipliers, enabling smooth longitudinal charts across different labs.
- **Relative Score (0.0 - 1.0)**: Tracks a result's position within its specific lab's reference range, allowing for lab-agnostic trend analysis.
- **Clinical Grouping**: Biomarkers are organized into **Groups** (e.g., Lipid Panel, CBC) for diagnostic context.

### Telemetry & IoT Device Synchronization

To maintain absolute data privacy, Health Assistant relies on a "headless" mobile sync architecture rather than querying third-party clouds (like Google Fit or Apple iCloud). 
High-frequency device data is routed into TimescaleDB using dynamic `is_telemetry` flags on Biomarker definitions. This enables rapid querying of millions of rows while avoiding FHIR observation bloat. **Note:** This represents an architectural tradeoff—telemetry data is stored outside of strict FHIR compliance for performance reasons and is currently excluded from standard FHIR patient exports.
A custom React Native companion application bridges the on-device health databases (Android Health Connect / iOS HealthKit) directly to the local FastAPI instance.
For implementation details and API payload schemas, see the [Mobile Sync App Architecture](MOBILE_SYNC_APP.md).

### Longitudinal Health Tracking

Health Assistant bridges the gap between discrete clinical visits and long-term health narratives using a **Metadata-Driven Events Engine**:
- **Journeys**: Events represent a "Health Journey" (e.g., a 9-month pregnancy or a 2-year dental alignment) that spans multiple examinations.
- **Categorized Experience**: Journeys are grouped into clinical categories (Reproductive, Acute & Chronic, Routine, etc.) with specialized UI tabs for filtering.
- **Schema-Driven UI**: Instead of hardcoded logic, each journey type uses a flexible **JSONB Metadata Schema**. The frontend dynamically renders the correct inputs (Numeric Metrics, Temporal Fields, Boolean Flags) based on this blueprint.
- **Episodes/Occurrences**: Allows tracking of specific points in time within a journey (e.g., a specific migraine during a chronic pain journey) with high-precision time and intensity logging.
- **Association Mapping**: Examinations are linked to journeys with a `reason` field, providing clinical context for how a particular visit contributed to the overall health goal.

## Notification Framework

The project implements a comprehensive system for medical reminders and clinical alerts:
- **Modular Triggers**: Supports time-based schedules (Medication), specific dates (Examinations), and system events (Biomarker breaches).
- **Asynchronous Delivery**: Uses Celery to process triggers and deliver messages without blocking the main API thread.
- **Web Push Integration**: Full PWA support for background notifications using the VAPID standard.
- **FHIR Compliance**: Delivered notifications are mapped to **FHIR Communication** resources for interoperability.
- **Real-time Monitoring**: Periodic tasks check for upcoming triggers every minute, ensuring high-precision delivery.

## AI / OCR Processing Pipeline

Health Assistant uses a unified, provider-agnostic AI architecture. For a deep dive into the design and how to extend it, see [AI_SYSTEM.md](./AI_SYSTEM.md).

1. **Ingestion**: File is stored securely and a background task is queued.
2. **Model Resolution**: `AIProviderService` resolves the active model for the task (OCR/NLP) based on database configurations and multitenancy rules.
3. **Text Extraction (OCR)**: `LangChainOCRProcessor` converts images/PDFs/DICOMs into Markdown text.
4. **Pass 1 - Catalog Mapping (NLP)**: `LangChainStructuredExtractor` maps extracted metrics to existing catalog slugs.
5. **Pass 2 - Ontology Generation (NLP)**: Generates standardized definitions for unknown metrics to automatically expand the catalog.
6. **Deterministic Normalization**: `MedicalProcessingService` performs unit conversions and calculates `relative_score`.
7. **Persistence**: Saves FHIR Observations with live progress tracking.

## Data Serialization & FHIR Interoperability

Internal models are **FHIR-enhanced relational rows**: FHIR-shaped JSONB columns (`code`, `subject`, `value_quantity`) *plus* app-specific relational columns (`biomarker_id`, `normalized_value`, `tenant_id`). Because the Biomarker Engine needs the relational columns, the app does not store pure FHIR resources. Each model therefore exposes **two serialization paths**:

- **`to_dict()`** — ORM shape (snake_case + app fields). What the REST API returns, the frontend consumes, and the AI tools read. Not valid FHIR JSON.
- **`to_fhir_dict()`** — FHIR R4B shape (camelCase, valid FHIR JSON). Built and validated by the **`fhir.resources`** library (`Model(**fields).model_dump(by_alias=True, exclude_none=True, mode="json")`). Used by the export/import feature.

**`fhir.resources` is the single source of truth for FHIR shape** at the two interop boundaries (`app/services/fhir_helpers.py` → `build_fhir_resource` / `parse_fhir_resource`):
- **Export** (`to_fhir_dict()`): ORM → construct `fhir.resources` model → canonical dump. Construction-time validation guarantees spec-compliant output. The export loop applies a **fail-loud** policy — a resource that fails validation throws an `ExportError` and aborts the backup so data is never silently dropped. (However, strict write-time validation prevents invalid data from entering the database to begin with).
- **Import** (`fhir_converter.fhir_to_orm()`): canonical FHIR → `fhir.resources.model_validate()` (validates + types) → ORM-shape dict. Invalid resources raise `FhirSerializationError` and are skipped + logged.

The REST CRUD path (`/fhir/*` endpoints + `fhir_service.create_*`) **does not** use `fhir.resources` for parsing — the frontend speaks ORM-shape (snake_case), so `create_*` just coerces types (str→UUID/date, interpretation-list→string). It **does** validate on write: every `create_*`/`update_*`, as well as integration and OCR writes, calls `assert_valid_fhir()` (→ `to_fhir_dict()`) before persisting, so invalid FHIR can never be stored; `FhirSerializationError` maps to HTTP 400 or skip-and-log depending on the path. FHIR parsing of canonical input lives at the import boundary.

Low-level primitives (`_clean`, `_as_list`, `build_meta`, `_normalize_timing`, `_extract_patient_id`, `_flatten_interpretation`) live in `app/services/fhir_helpers.py`. `validate_bundle()` (Bundle-level) remains in `fhir_converter.py`.

Telemetry (`TelemetryDataModel`) is intentionally excluded from FHIR exports by design (see `TELEMETRY_AND_AGGREGATION.md`).

### FHIR Server Integration (Stage 2)

Beyond export/import (file-based), Health Assistant can connect to a **live external FHIR server** as an integration under the SDK — the reference provider is `integrations/fhir_server/`. It pulls a patient's `Observation`s into the Biomarker Engine:

- **Auth modes** (`auth_mode` config): `smart` (SMART-on-FHIR standalone launch + Dynamic Client Registration, for hospitals/`r4.smarthealthit.org`) or `none` (tokenless, for a local/open server like vanilla HAPI FHIR). `smart` instances start `PENDING` until the OAuth callback; `none` go straight to `ACTIVE`.
- **Pull** (`provider.pull_data`): bounded FHIR search `Observation?_lastUpdated=gt<cursor>&_count=100&_sort=_lastUpdated[&patient][&category]` → each FHIR Observation is mapped to an `ObservationCreate` on the **local** patient (`sdk/fhir.fhir_observation_to_create`) → the existing biomarker-mapping waterfall resolves `biomarker_id` and routes telemetry.
- The SDK auth/HTTP/FHIR helpers (`integrations/sdk/{auth,http,fhir}.py`) are reusable by any cloud integration and by the Stage 3 facade (see below).

### FHIR R4 Facade (Stage 3)

Health Assistant now also **acts as** a conformant FHIR R4 REST server at `/api/v1/fhir/R4/*` — this is the **interop surface** for external systems (FHIR servers, HL7 importers, export/import jobs, SMART-on-FHIR clients). The frontend does **not** use the facade; it speaks the domain endpoints (`/patients/*`, `/observations/*`, `/examinations/*`, etc.) which return ORM-shape dicts optimized for the UI.

- **`GET /fhir/R4/metadata`** returns a dynamic CapabilityStatement built from `RESOURCE_REGISTRY` (no auth per FHIR spec; Cache-Control 5 min). Advertises every registered resource + supported interactions + search params.
- **`GET /fhir/R4/{Resource}`** returns a FHIR Bundle (`type=searchset`) with `total`, `link[]` pagination (self/first/last/previous/next), and `entry[]` of `{fullUrl, resource}`. Honors standard search params (`_id`, `_lastUpdated`, `_count` capped at 250, `_sort`, `_format`) plus per-resource params (`patient`, `code`, `date`, `status`, `category`, …). Tenant-scoped by default; soft-deleted rows excluded.
- **`GET /fhir/R4/{Resource}/{id}`** returns canonical FHIR JSON + `ETag`/`Last-Modified` headers. Reads of deleted rows return `410 Gone` (OperationOutcome) — tombstone semantics, not `404 Not Found`.
- **`POST /fhir/R4/{Resource}`** accepts canonical FHIR JSON, validates via `fhir.resources`, returns `201 Created` + `Location` header + canonical body. Records a `Provenance` resource targeting the new row.
- **`PUT /fhir/R4/{Resource}/{id}`** — full replacement, bumps `VersionedMixin.version`, returns 200 + canonical body.
- **`DELETE /fhir/R4/{Resource}/{id}`** — soft-delete (`deleted_at = now()`), returns `204 No Content`. Records a final Provenance.

**15 registered resources**: Patient, Observation, Condition, Encounter, AllergyIntolerance, MedicationStatement, MedicationRequest, Medication (catalog, read-only via facade), DiagnosticReport, DocumentReference, Device, Communication, Organization, Practitioner, Provenance.

**Hybrid storage (no dual-write)**: existing tables became FHIR-canonical via `to_fhir_dict()` projections. `Condition` ← `clinical_events` (metadata-driven JSONB stays untouched; the projection interprets it per `metadata_schema`). `Encounter` ← `examinations` (model + UI vocabulary unchanged — "Examination" is the user-facing word, "Encounter" is the FHIR name). `DocumentReference` ← `documents` (metadata-only attachment; binary lives in app storage, referenced via `urn:ha-document:<id>`). Three **new tables** hold concepts with no app-table analog: `fhir_provenance` (immutable, multi-target audit), `fhir_devices` (reference table backfilled from `user_integrations`), `fhir_communications` (clinical messaging, distinct from push notifications).

**Medication intent discriminator**: one `fhir_medications` table serves both MedicationStatement (`intent=statement`) and MedicationRequest (`intent=order|plan|proposal`). The facade routes to the right FHIR resource based on the discriminator column.

**Provenance-on-write**: every facade `POST`/`PUT`/`DELETE` records a `Provenance` targeting the affected resource (best-effort — never aborts the parent write on Provenance failure). Agents are the authenticated user or the integration.

**Error shape**: every error response is a FHIR `OperationOutcome` resource with `issue[]` blocks (severity, code, diagnostics). The existing global exception handler still wraps unexpected 500s with a correlation id; facade-specific errors map to 400/404/405/410 with OperationOutcome bodies.

Developer guide: [FHIR_R4_FACADE.md](FHIR_R4_FACADE.md).

## Frontend Architecture

### Centralized Data Extractor (`useBiomarkers`)
A robust custom hook serves as the single source of truth for all biomarker rendering:
- **Universal Parsing**: Handles known, unknown, and legacy biomarker data formats seamlessly.
- **Definition Enrichment**: Fetches the biomarker definition catalog once per session and enriches every observation with the canonical definition name + UUID, so `BiomarkerDefinition` is the authority for both identity and display (not the raw observation text). Unmapped observations (no definition) are flagged and show a popup to create or map them.
- **Multi-Perspective Views**: Provides dynamic grouping logic for three perspectives:
    - **By System**: Clinical panels (e.g., Heart Health, Liver Function).
    - **By Technical**: Technical source (e.g., Blood Lab, Imaging, Vitals).
    - **By Examination**: Grouped by specific clinical visits.
- **Interpretation Logic**: Standardizes the display of abnormal flags (High/Low) and reference ranges.

### State Management (Zustand)
- **authSlice**: Session and identity management.
- **patientSlice**: Contextual data for the currently active patient.
- **dashboardSlice**: Layout and card configurations.
- **uiSlice**: Global modal and notification management.


### Draggable Dashboard
Uses `react-grid-layout` with a persistent backend storage for layouts. Users can customize which biomarker cards, trend graphs, and imaging previews are visible for each patient.

## Deployment

Fully containerized environment via `docker-compose`:
- **Postgres/TimescaleDB**: Primary data and time-series storage.
- **Redis**: Broker for background tasks.
- **Celery Worker**: Dedicated AI/OCR processing node.
- **FastAPI**: Main API service.
- **React**: Served via Vite in development / Nginx in production.
