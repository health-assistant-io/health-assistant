# Health Assistant - Project Structure

## Current Structure (Updated June 2026 - Beta)

```text
Health Assistant/
├── backend/                    # FastAPI backend
│   ├── alembic/                # Database migrations
│   ├── app/
│   │   ├── api/v1/endpoints/  # REST API endpoints
│   │   ├── core/              # Core utilities
│   │   ├── models/            # Database models (SQLAlchemy 2.0)
│   │   ├── processors/        # AI/NLP processing pipeline
│   │   ├── schemas/           # Pydantic validation schemas
│   │   ├── services/          # Business logic
│   │   ├── utils/             # Helpers
│   │   ├── workers/           # Celery background tasks
│   │   └── main.py            # FastAPI application
│   ├── scripts/               # DB maintenance & recategorization scripts
│   ├── tests/                 # Unit tests
│   ├── requirements.txt       # Python dependencies
│   └── pyproject.toml         # Project metadata
│
├── integrations/              # External Integrations & Connectors (Python & TS SDKs)
│   ├── sdk/                   # Base provider interfaces
│   ├── webhook/               # Generic webhook handler
│   ├── health_assistant_bridge/ # Mobile app bridge
│   └── ...
│
├── frontend/                  # React 18 / Vite / TypeScript frontend
│   ├── src/
│   │   ├── api/               # API clients (axios with interceptors)
│   │   ├── components/
│   │   │   ├── ai/            # Chatbot and AI UI components
│   │   │   ├── dashboard/     # Draggable grid layouts
│   │   │   ├── documents/     # Image/PDF immersive viewers
│   │   │   ├── events/        # Clinical Event journeys
│   │   │   ├── examinations/  # Rich-text notes & timelines
│   │   │   ├── integrations/  # External integrations SDK UI
│   │   │   ├── layout/        # App shells, sidebars
│   │   │   ├── shared/        # Reusable UI elements
│   │   │   └── ui/            # Tailwind UI components
│   │   ├── config/            # Frontend configuration
│   │   ├── constants/         # Magic strings/numbers
│   │   ├── hooks/             # Custom React hooks
│   │   ├── locales/           # i18n translation files (en, el)
│   │   ├── pages/             # Route views
│   │   │   ├── AI/            # Global AI settings
│   │   │   ├── Auth/          # Login
│   │   │   ├── Dashboard/     # Clinical Dashboard
│   │   │   ├── Documents/     # Image Gallery & Details
│   │   │   ├── Events/        # Clinical Event listing
│   │   │   ├── Examinations/  # Visit Timeline
│   │   │   ├── Patients/      # Patient context switcher
│   │   │   ├── Settings/      # App/User settings
│   │   │   └── ...
│   │   ├── services/          # Abstraction for API calls
│   │   ├── store/             # Zustand state management slices
│   │   ├── types/             # TypeScript interfaces
│   │   ├── utils/             # Helper functions (date formatting, units)
│   │   ├── __tests__/         # Frontend test specs
│   │   ├── App.tsx            # Main router
│   │   ├── index.css          # Tailwind directives
│   │   └── main.tsx           # Entry point
│   ├── package.json           # Dependencies
│   ├── tailwind.config.js     # Tailwind setup
│   └── vite.config.ts         # Vite bundler configuration
│
├── docker/                    # Docker configuration
│   ├── docker-compose.yml     # Production services
│   └── docker-compose.db.yml # Development services
│
├── docs/                      # Technical Documentation
│   ├── ARCHITECTURE.md        # Technical architecture details
│   ├── AI_SYSTEM.md           # AI provider factory design
│   ├── DEVELOPMENT.md         # Local developer guidelines
│   ├── DEVELOPMENT_PLAN.md    # Upcoming features / roadmap
│   ├── INSTALL.md             # Production setup guide
│   ├── PROJECT_STRUCTURE.md   # You are here
│   └── ...
│
├── scripts/                   # Root-level utility scripts
│   └── run-dev.sh             # Consolidated development startup script
├── uploads/                   # Local file storage (documents)
└── README.md                  # Project overview
```

## Key Directories Breakdown

### Backend

| Directory | Purpose | Status |
|-----------|---------|--------|
| `api/v1/endpoints/` | Fast API routing controllers | Complete |
| `core/` | Configuration, security, DB connections | Complete |
| `integrations/` | Connectors SDK and External Webhooks | Complete |
| `models/` | SQLAlchemy mappings & FHIR representations | Complete |
| `schemas/` | Pydantic types for request/response bodies | Complete |
| `services/` | Primary business logic / database interactions | Complete |
| `integrations/` | Connectors SDK and External Webhooks | Moved to root |
| `processors/` | Abstracted AI pipeline (OCR -> NLP -> Logic) | Complete |
| `workers/` | Asynchronous Celery task processing | Complete |
| `tests/` | Backend test suite | Complete |
| `TODO` | Biomarker-Clinical Event Binding API (from `DEVELOPMENT_PLAN.md`) | TODO |

### Frontend

| Directory | Purpose | Status |
|-----------|---------|--------|
| `pages/` | High-level React Views aligned to routes | Complete |
| `components/` | Domain-specific & reusable UI blocks | Complete |
| `store/` | Zustand context (Auth, Patient Switcher) | Complete |
| `api/` & `services/` | Axios interceptors & backend API layer | Complete |
| `hooks/` | Reusable state logic | Complete |
| `locales/` | Multilingual support JSON dictionaries | Complete |

## File Count & Scale

- **Backend Python files**: ~100+ (Includes tests & migration scripts)
- **Frontend TypeScript files**: ~150+ (Extensive componentization)
- **Documentation files**: ~10
- **Configuration files**: ~12 (Docker, Vite, Tailwind, TS, Poetry/Pip)
