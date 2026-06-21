# Project Status & Roadmap

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
- Comprehensive Clinical Visit system (Examinations & Doctors)
- AI OCR & NLP Pipeline (OpenAI Vision/LLM + spaCy)
- Background task processing via Celery & Redis
- Modular Notification Framework with Web Push (VAPID) support
- Unit converter service
- Anomaly detector service (Reference range based)
- Medication interactor service
- Centralized semantic versioning manager
- **Export & Import (backup) system** — FHIR R4B Bundle + BagIt-style ZIP exports at patient/group/system scope; validated imports with SHA256 manifest verification and cross-tenant id remapping (see [EXPORT_IMPORT.md](EXPORT_IMPORT.md)). Admin-only UI at `/settings/export-import` (export form, drag-and-drop restore, live job polling, download).
- **Agentic AI Copilot + human-in-the-loop (HITL) proposals** — the chat assistant proposes clinical write actions (create a clinical event, add a biomarker to an examination, add a medication, define a new biomarker/medication in the catalog); the user reviews/edits and explicitly confirms before anything is saved (the AI never writes directly). After resolution, the agent gets an **auto-resume continuation turn** with structured outcomes fed back. **Parallel proposals** (multiple independent actions per turn) and a **Continue button** for partial resumes are supported. See [AI_SYSTEM.md §4.1](AI_SYSTEM.md) and the `hitl-task-cards` skill.
- **0.3.0 security & critical-fix pass** (see [CHANGELOG.md](../CHANGELOG.md)) — **all 29 Critical/High items resolved**:
  - *First pass:* AI provider `api_key` encrypted at rest + masked in responses; scope checks on `/ai-config/providers/*` and `/models/*`; telemetry endpoints tenant-scoped; global exception handler no longer leaks `str(exc)` (now uses a `correlation_id`); `fetch-external-models` SSRF guard; `list_observations` actually applies its filters; `/fhir/Observation/history` no longer raises `TypeError`; `sync_active_integrations` correctly routes telemetry to TimescaleDB; `AIModel.__table_args__` typo fixed + migration `f1a2b3c4d5e6`; dead `processors/fhir_mapper.py` deleted; `from app.models import *` works again; telemetry service stubs replaced with real implementations.
  - *Second pass (P0 completion):* OHLC double-aggregation fixed (`AVG(AVG(col))` → single-level) + telemetry bucket whitelist; `relative_score` boundary logic fixed (strictly-interior = Normal, boundary defers to range check); Magic Fill date now uses `datetime.now()`; FHIR single-resource reads tenant-scoped at the service level (`get_observation`/`get_diagnostic_report`/`get_medication`/`delete_observation`); **prompt-injection guard** (`app/utils/prompt_guard.py`, 8 OWASP LLM01 patterns + `DEFENSE_PREAMBLE`); `print()` leaks in AI service replaced with `logger.debug`; **SVG sanitizer rewritten** (all event-handler quoting forms + `javascript:`/`vbscript:`/`data:text/html` URLs + `<script>`/`<foreignObject>` elements); CORS fallback hostname fixed (underscore → hyphen); **WebSocket hardened** (subprotocol auth + fixed 10Hz busy-loop + error logging + 30s keepalive ping); **AuditLog provenance** (`app/services/audit_service.py`) wired into FHIR create/delete endpoints; insecure `POSTGRES_PASSWORD` default removed + production validator; SQL threshold validated before inlining; **webhook HMAC-SHA256** verification (opt-in via `webhook_secret`); auth on integration listing + documentation endpoints.

#### Frontend
- React 18 app with Vite & TypeScript
- Immersive frontend with Tailwind CSS
- Draggable & Persistent Dashboard (react-grid-layout)
- Secure full-screen viewers for Images, PDFs, and Text/Markdown
- Centralized data extractor (`useBiomarkers` hook)
- Zustand state management
- Notification Center & PWA Push support
- Document gallery and clinical timeline
- **Export & Import UI** (`/settings/export-import`, admin-only) — create exports, restore from ZIP/JSON, live job polling, download
- **0.3.0 PWA + GraphQL fixes**: manifest shortcut `/examinations/new` → `/examinations/upload`; PWA runtime caches now use same-origin predicates (no longer hardcoded to `localhost:8000`); GraphQL client auth header is now per-request via `graphqlRequest()` (no longer captured at module-load).

### ⚠️ In Progress
- Advanced anomaly detection algorithms (Statistical)
- Multi-language OCR refinement
- Chart component enhancements
- Advanced form validation

### 📅 Roadmap / Future Tasks
1. **Advanced Analytics**: Multi-axis charts for trend visualization.
2. **Data Portability**: Comprehensive patient history export (PDF/JSON). — *Partially delivered: FHIR Bundle + ZIP backup export/import landed (see [EXPORT_IMPORT.md](EXPORT_IMPORT.md)); PDF report export still pending.*
3. **Testing**: Add E2E tests using Playwright or Cypress.
4. **Mobile Sync**: Headless mobile sync architecture for wearable data.
5. **Biomarker Insights**: Deeper clinical insights and correlations (See [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)).
6. **FHIR R4 Conformance**: CapabilityStatement, Bundle search responses, missing resources (Condition/Encounter/Device/Provenance), SDK multi-resource builders.
