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

### ⚠️ In Progress
- Advanced anomaly detection algorithms (Statistical)
- Multi-language OCR refinement
- Chart component enhancements
- Advanced form validation
- Real-time status updates via WebSockets (Polling fallback implemented)

### 📅 Roadmap / Future Tasks
1. **Real-time Notifications**: Implement WebSocket support for live document processing updates.
2. **Advanced Analytics**: Multi-axis charts for trend visualization.
3. **Data Portability**: Comprehensive patient history export (PDF/JSON). — *Partially delivered: FHIR Bundle + ZIP backup export/import landed (see [EXPORT_IMPORT.md](EXPORT_IMPORT.md)); PDF report export still pending.*
4. **Testing**: Add E2E tests using Playwright or Cypress.
5. **Mobile Sync**: Headless mobile sync architecture for wearable data.
6. **Biomarker Insights**: Deeper clinical insights and correlations (See [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)).
