# Health Assistant — Repository Structure

## Structure

```text
Health Assistant/
├── backend/                       # FastAPI backend
│   ├── alembic/                   # Database migrations (consolidated baseline + increments)
│   ├── app/
│   │   ├── ai/                    # AI/ML pipeline — provider factory, OCR/NLP, agentic chat
│   │   │   ├── agents/            #   agentic chat loop, HITL plumbing, prompt assembly
│   │   │   ├── assistance/        #   AIAssistanceService (chat orchestration, Magic Fill)
│   │   │   ├── parsers/           #   LangChainOCRProcessor + LangChainStructuredExtractor
│   │   │   ├── pipeline/          #   MedicalProcessingService (unit conv, ontology match)
│   │   │   ├── providers/         #   AIProviderService + TaskType enum (model resolution)
│   │   │   ├── schemas/           #   Pydantic schemas for structured AI output
│   │   │   └── tools/             #   LangChain chat tools + integrations aggregator
│   │   ├── api/v1/endpoints/      # REST API endpoints (~36 modules, 298 handlers)
│   │   ├── catalogs/              # CatalogRegistry + adapters (unified catalog meta-layer)
│   │   ├── core/                  # Config, security, database, encryption
│   │   ├── facade/                # FHIR R4 REST facade (registry, crud, search, terminology)
│   │   ├── instances/             # Instance search meta-layer (records vs definitions)
│   │   ├── models/                # SQLAlchemy 2.0 models (Core + FHIR + taxonomy)
│   │   ├── processors/            # Legacy NLP helpers + importers (most AI logic is in ai/)
│   │   ├── schemas/               # Pydantic validation schemas
│   │   ├── services/              # Business logic (~32 services)
│   │   ├── utils/                 # Helpers (prompt guard, etc.)
│   │   ├── workers/               # Celery tasks + task_logger + task_monitor
│   │   └── main.py                # FastAPI app entrypoint + lifespan
│   ├── data/seeds/                # JSON seed files (10: concepts, diseases, medications, vaccines,
│   │                              #   clinical_event_types, allergies, anatomy_structures,
│   │                              #   concept_edges, default_catalog, biomarker_panels)
│   ├── scripts/                   # Admin + maintenance scripts (~22: seed_demo, create_system_admin,
│   │                              #   export_seeds, encrypt_existing_api_keys, migrate_heart_rate, …)
│   ├── tests/                     # pytest suite (1800+ tests)
│   ├── requirements.txt
│   └── pyproject.toml
│
├── integrations/                  # External Integrations & Connectors (Python & TS SDKs)
│   ├── base.py                    # Shared base re-exports
│   ├── sdk/                       # BaseHealthProvider, BaseConfigFlow, ObservationBuilder,
│   │                              #   auth (OAuth2/PKCE/SMART), http, fhir, notifications
│   ├── dev_dummy/                 # Developer testing integration (OAuth mock, error sim)
│   ├── fhir_server/               # External FHIR server connector (SMART or tokenless)
│   ├── health_assistant_bridge/   # Mobile-app bridge integration + TS/Py SDKs
│   ├── mcp_client/                # Model Context Protocol client integration
│   └── webhook/                   # Generic webhook receiver (Tasker, Shortcuts, etc.)
│
├── frontend/                      # React 18 / Vite / TypeScript frontend
│   ├── src/
│   │   ├── api/                   # API clients (axios with interceptors)
│   │   ├── components/
│   │   │   ├── ai/                # Chatbot + AI UI (incl. HITL review cards)
│   │   │   ├── anatomy/           # Anatomy Explorer + body diagram atlas
│   │   │   ├── biomarkers/        # Biomarker trends, KPI strips, detail tabs
│   │   │   ├── catalog/           # Catalogs workspace + info tabs
│   │   │   ├── charts/            # Reusable chart primitives
│   │   │   ├── dashboard/         # Draggable react-grid-layout cards
│   │   │   ├── documents/         # Image/PDF immersive viewers
│   │   │   ├── events/            # Clinical Event journeys
│   │   │   ├── examinations/      # Rich-text notes, timelines, preview
│   │   │   ├── instances/         # Instance picker (patient records search)
│   │   │   ├── integrations/      # External integrations SDK UI
│   │   │   ├── layout/            # App shells, sidebars, header
│   │   │   ├── medications/       # Medication forms + detail
│   │   │   ├── notifications/     # Notification bell, center, modal
│   │   │   ├── patients/          # Patient context + detail
│   │   │   ├── settings/          # App/user settings panels
│   │   │   ├── shared/            # Reusable UI elements
│   │   │   └── ui/                # Tailwind UI primitives
│   │   ├── config/                # Frontend configuration
│   │   ├── constants/             # Magic strings/numbers
│   │   ├── hooks/                 # Custom React hooks (useBiomarkers, useNotificationStream, …)
│   │   ├── locales/               # i18n translation files (en, el)
│   │   ├── pages/                 # Route views (21 page dirs)
│   │   │   ├── About/  Account/  Admin/  AI/  Analytics/  Anatomy/
│   │   │   ├── Auth/    Biomarkers/  Calendar/  Catalogs/
│   │   │   ├── Dashboard/  Doctors/  Documents/  Events/
│   │   │   ├── Examinations/  Medications/  Notifications/
│   │   │   ├── Organizations/  Patients/  Settings/  Vaccinations/
│   │   │   └── TaskManager.tsx
│   │   ├── services/              # Abstraction for API calls (+ Dexie offline cache)
│   │   ├── store/                 # Zustand state management (14 slices in store/slices/)
│   │   ├── types/                 # TypeScript interfaces
│   │   ├── utils/                 # Helpers (date formatting, units, biomarkerUtils)
│   │   ├── App.tsx                # Main router
│   │   ├── main.tsx               # Entry point
│   │   ├── sw.ts                  # PWA service worker (injectManifest)
│   │   ├── pwa.d.ts               # PWA type declarations
│   │   ├── globals.d.ts          # Global type declarations
│   │   ├── i18n.ts                # i18next config
│   │   └── index.css              # Tailwind directives
│   ├── tests/                     # Co-located vitest specs
│   ├── tests-e2e/                 # Playwright + ui-capture pipeline
│   ├── package.json               # Dependencies + scripts (dev/build/lint/capture:ui)
│   ├── tailwind.config.js
│   └── vite.config.ts
│
├── docker/                        # Docker configuration
│   ├── docker-compose.dev.yml         # Dev (builds from source, mounts volumes, hot-reload)
│   ├── docker-compose.prod.yml        # Prod (Bring-Your-Own-Proxy flavor, resource limits)
│   ├── docker-compose.standalone.yml  # Prod (All-in-one flavor with integrated Nginx)
│   ├── docker-compose.dev-db.yml      # Dev DB (Postgres+TimescaleDB + Redis only)
│   ├── Dockerfile                     # Backend image (uvicorn)
│   ├── Dockerfile.worker              # Worker image (celery)
│   └── Dockerfile.frontend            # Frontend image
│
├── docs/                          # Technical Documentation (see docs-tree.json for public nav)
│   ├── docs-tree.json             # Single source of truth for public docs nav + SEO metadata
│   ├── ARCHITECTURE.md  AI_SYSTEM.md  API.md  CI_CD_SETUP.md  CLINICAL_EVENTS.md
│   ├── DEVELOPMENT.md  DEVELOPMENT_PLAN.md  EXPORT_IMPORT.md  FHIR_R4_FACADE.md
│   ├── INSTALL.md  INTEGRATIONS_FRAMEWORK.md  INTEGRATIONS_SDK.md  MOBILE_SYNC_APP.md
│   ├── NOTIFICATION_SYSTEM.md  ONTOLOGY_CATALOG.md  PROJECT_STRUCTURE.md  SCREENSHOTS.md
│   ├── SEEDING_AND_DEMOS.md  STATUS.md  TAXONOMY.md  TELEMETRY_AND_AGGREGATION.md
│   ├── TENANCY_AND_USER_MANAGEMENT.md
│   ├── (internal — not in docs-tree.json: RELEASE_PROCESS, TASK_DEBUGGING,
│   │        TASK_DEBUGGING_GUIDE, TASK_PROGRESS_INDICATOR, UI_CAPTURE_PIPELINE,
│   │        AI_PROVIDER_TESTS)
│   └── images/                    # Screenshots used by SCREENSHOTS.md
│
├── dev/                           # Private working notes (gitignored: marketing copy, audits,
│                                  #   plans; tracked: design RFCs, notes)
│   └── audits/                    # Doc/code audits (AUDIT-DOCS-2026-07-20.md, etc.)
│
├── scripts/                       # Root-level utility scripts (~11 files)
│   ├── run-dev.sh                 #   Dev startup — bootstrap + `honcho start -f Procfile.dev`
│   ├── run-docker.sh              #   Docker dev convenience wrapper
│   ├── reset-dev-db.sh            #   Nuke + recreate dev DB + re-seed
│   ├── setup_env.py               #   Interactive env wizard (generates secrets, VAPID keys)
│   ├── version_manager.py         #   show/set/bump/release + CHANGELOG staging
│   └── ...
│
├── uploads/                       # Local file storage (documents, anatomy figures)
├── logging/                       # App log output (celery.log, etc.)
├── Procfile.dev                   # Dev process group (backend/worker/beat/flower/frontend)
├── CHANGELOG.md                   # Release notes (Keep-a-Changelog format)
├── NOTICE                         # Third-party attributions (anatomy diagrams, LOINC/SNOMED/ICD-10/CVX)
├── LICENSE
├── .env.example                   # Annotated config template
└── README.md                      # Project overview (the repo's front door)
```

## Key Directories Breakdown

### Backend

| Directory | Purpose | Status |
|-----------|---------|--------|
| `api/v1/endpoints/` | Fast API routing controllers (~36 modules, 298 handlers) | Complete |
| `ai/` | Unified AI pipeline — provider factory, OCR/NLP processors, agentic chat, HITL tools | Complete |
| `catalogs/` | CatalogRegistry + per-type adapters (biomarkers/medications/allergies/vaccines/anatomy/concepts) | Complete |
| `core/` | Configuration, security, DB connections, encryption | Complete |
| `facade/` | FHIR R4 REST facade (registry, crud, search, terminology projections) | Complete |
| `instances/` | Patient-record instance search meta-layer (exams/meds/observations/etc.) | Complete |
| `models/` | SQLAlchemy mappings & FHIR representations | Complete |
| `schemas/` | Pydantic types for request/response bodies | Complete |
| `services/` | Primary business logic / database interactions | Complete |
| `processors/` | Legacy NLP helpers + importers (most AI logic migrated to `ai/`) | Complete |
| `workers/` | Asynchronous Celery task processing + task_logger | Complete |
| `tests/` | Backend test suite (1800+ tests) | Complete |
| `services/{export_service,import_service,fhir_converter,seed_service,seed_export_service}.py` + `api/v1/endpoints/{export,import_data}.py` | Export/Import/Seed pipeline — FHIR R4B Bundle + BagIt-style ZIP at patient/group/system scope; see [EXPORT_IMPORT.md](EXPORT_IMPORT.md) + [SEEDING_AND_DEMOS.md](SEEDING_AND_DEMOS.md) | Complete |

### Integrations (Root Level)

| Directory | Purpose | Status |
|-----------|---------|--------|
| `sdk/` | Base Python classes (`BaseHealthProvider`, `BaseConfigFlow`, `ObservationBuilder`) + OAuth/HTTP/FHIR/notification helpers | Complete |
| `webhook/` | Generic webhook receiver for Tasker, Shortcuts, etc. | Complete |
| `health_assistant_bridge/` | Mobile app bridge integration + TS/Py SDKs | Complete |
| `fhir_server/` | External FHIR server connector (SMART or tokenless auth) | Complete |
| `mcp_client/` | Model Context Protocol client integration | Complete |
| `dev_dummy/` | Developer testing integration (OAuth mock, error sim, 4 notification types) | Complete |

### Frontend

| Directory | Purpose | Status |
|-----------|---------|--------|
| `pages/` | High-level React Views aligned to routes (21 page dirs) | Complete |
| `components/` | Domain-specific & reusable UI blocks (18 dirs) | Complete |
| `store/` | Zustand context — 14 slices (auth, patient, dashboard, ui, aiConfig, chart, document, settings, tenant, …) | Complete |
| `api/` & `services/` | Axios interceptors + backend API layer + Dexie offline cache | Complete |
| `hooks/` | Reusable state logic (useBiomarkers, useNotificationStream, useBiomarkerDetailData, …) | Complete |
| `locales/` | Multilingual support JSON dictionaries (en, el) | Complete |
| `tests-e2e/` | Playwright UI-capture pipeline (gallery.mjs generates SCREENSHOTS.md) | Complete |

## File Count & Scale

- **Backend Python files**: ~520 (includes tests, migrations, integrations, scripts)
- **Frontend TypeScript/TSX files**: ~525 (extensive componentization, 49 pages / 231 components)
- **Documentation files**: 28 (24 public + 5 internal + root README/CHANGELOG)
- **Configuration files**: ~15 (Docker, Vite, Tailwind, TS, ESLint, Alembic, Vitest, Playwright)
