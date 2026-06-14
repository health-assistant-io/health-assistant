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
- **fhir_organizations**: Hierarchical grouping (id, tenant_id, name, org_type, part_of_id)
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
- **alerts**: Legacy clinical allergy triggers (deprecated)

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

## Frontend Architecture

### Centralized Data Extractor (`useBiomarkers`)
A robust custom hook serves as the single source of truth for all biomarker rendering:
- **Universal Parsing**: Handles known, unknown, and legacy biomarker data formats seamlessly.
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
