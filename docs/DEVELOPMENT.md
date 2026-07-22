# Health Assistant — Development Guide

This guide covers local development for Health Assistant — a self-hosted, open-source health records platform built on FastAPI, React, and PostgreSQL/TimescaleDB. See [STATUS.md](STATUS.md) for what's implemented and [ARCHITECTURE.md](ARCHITECTURE.md) for the system design.

## Development Setup

### Quick Start

1. **Install Docker** and Docker Compose on your system if you haven't already.
2. **Clone the project:**
   ```bash
   git clone https://github.com/health-assistant-io/health-assistant.git
   cd health-assistant
   ```
3. **Configure the environment:**
   Copy the Docker environment example file to `.env` in the root directory and update it with your specific values (e.g., adding `OPENAI_API_KEY`):
   ```bash
   cp .env.example .env
   ```
4. **Start the database:**
   ```bash
   docker compose --env-file .env -f docker/docker-compose.dev-db.yml up -d
   ```
5. **Start the development application:**
   Run the unified development script:
   ```bash
   ./scripts/run-dev.sh
   ```
6. **Access the application:**
   Once the script finishes starting the services, open your web browser and navigate to:
   - **Main Application (Frontend):** [http://localhost:3000](http://localhost:3000) - *This is the main user interface where you will interact with the Health Assistant.*
   - **API Documentation (Backend):** [http://localhost:8000/docs](http://localhost:8000/docs) - *Interactive developer documentation for the backend API.*
   - **Flower (Celery Monitoring):** [http://localhost:5555](http://localhost:5555) - *Real-time view of background workers, queues, and task history.*

   The unified script (`scripts/run-dev.sh`) runs every dev process under
   [honcho](https://github.com/nickstenxing/honcho) using `Procfile.dev`:
   `backend`, `worker`, `beat`, `flower`, and `frontend`. A single `Ctrl+C`
   cleanly stops all of them, and if any process crashes honcho stops the
   others so you see the error immediately in the foreground. To start just
   one process (e.g. the worker): `honcho start worker -f Procfile.dev`.

   **Testing the first-run setup wizard:** by default the script creates an
   admin (`admin@healthassistant.local` / `admin123`) so you can skip
   login. To exercise the browser setup wizard instead, pass `--no-admin`
   and start from a clean DB:

   ```bash
   ./scripts/reset-dev-db.sh -y --migrate
   ```

   ```bash
   ./scripts/run-dev.sh --no-admin
   ```

   The app now has zero users, so visiting http://localhost:3000 redirects
   to `/setup`. (Localhost/dev skips the setup token.)

   **Wiping the dev database:** when a migration diverges from your local
   schema or seed data gets into a bad state, `scripts/reset-dev-db.sh`
   stops the dev Postgres + Redis containers, removes their volumes, and
   brings up a fresh empty database + cache (it confirms before destroying
   anything and waits for Postgres to be healthy). Flags: `-y` (skip
   confirm), `--keep-redis`, `--no-start`, `--migrate`, `--purge-dangling`.
   Pair with `./scripts/run-dev.sh` afterwards — it re-runs `seed_all()` on
   the fresh DB.

   ```bash
   ./scripts/reset-dev-db.sh -y        # wipe + restart the dev DB stack
   ./scripts/reset-dev-db.sh -y --migrate   # also apply migrations so pytest works immediately
   ```

### Alternative: All-in-Docker Development

If you prefer to run the entire development stack (including the backend and frontend code) inside Docker containers with mounted volumes, you can use the `run-docker.sh` script or the `docker-compose.dev.yml` file directly:

```bash
# Using the helper script:
./scripts/run-docker.sh

# Or using docker compose directly:
docker compose --env-file .env -f docker/docker-compose.dev.yml up --build
```
*(Note: This approach isolates your environment completely, but hot-reloading may be slightly slower depending on your operating system's file-sharing performance with Docker).*

### Environment Configuration
- **Backend**: Requires `OPENAI_API_KEY` for OCR/NLP functionality.
- **Frontend**: Configured via `VITE_API_URL`.

## Recent Changes & Optimizations
- **Decoupled Telemetry Aggregation:** Separated temporal scoping from aggregation resolution (TimescaleDB gapfilling), complete with real-time CLI migration scripts located in `backend/scripts/`.
- **In-App Viewers**: Replaced external downloads with full-screen Image, PDF, and Text viewers.
- **Smart Interpretation**: Added automated status detection (High/Low/Normal) for all biomarkers based on clinical reference ranges.
- **Enhanced Timeline**: Implemented clinical-interval filtering (Last 30 Days, Custom Range, etc.) in the Examinations list.
- **Safe Deletion**: Implemented cascaded deletion that cleans up physical files and extracted health data when an examination is removed.

### Manual Start

The recommended path is `./scripts/run-dev.sh` (it manages all 5 processes
under honcho). If you need to start services individually, you must replicate
its environment manually — and remember that running only `uvicorn` without
the worker means background jobs (OCR, export, import, notifications,
integration sync) will silently queue and never run.

#### Backend

```bash
cd backend
source venv/bin/activate
export PYTHONPATH=.:../   # ../ so `integrations.*` resolves
uvicorn app.main:app --reload
```

#### Frontend

```bash
cd frontend
npm run dev
```

#### Celery Worker + Beat + Flower

```bash
cd backend
source venv/bin/activate
export PYTHONPATH=.:../

celery -A app.workers.celery_app worker --loglevel=info
# In a separate shell:
celery -A app.workers.celery_app beat --loglevel=info
# And another for monitoring (http://localhost:5555):
celery -A app.workers.celery_app flower --port=5555
```

## Testing

### Backend

The `pytest` suite tests the FastAPI backend endpoints asynchronously.

```bash
# Run the entire test suite
./backend/run-tests.sh

# With coverage
./backend/run-tests.sh --coverage
```

### Frontend

```bash
# Build check
npm run build

# Lint check
npm run lint
```

## Development Workflow

### 1. Backend Development
- Server auto-reloads on code changes.
- API docs: http://localhost:8000/docs
- Check terminal for reloader logs.

### 2. Frontend Development
- Vite provides HMR (Hot Module Replacement).
- Check browser console for errors.
- Use React DevTools for state debugging.

## Seed System

All reference/catalog data (medications, clinical event types, allergies, anatomy, the taxonomy, the default biomarker catalog, biomarker panel memberships) is seeded on every application startup by `SeedService.seed_all()` — a single ordered pipeline (`backend/app/services/seed_service.py`). The order is declared in `_SEED_STAGE_NAMES` so dependencies land first (e.g. `concepts` before `concept_edges`; `body_parts` before `concept_edges` which resolve anatomy slugs).

Each stage returns a standard stats contract `{added, updated, skipped, errors}` and is **idempotent** — re-runs reconcile existing rows to the JSON (including a concept's multi-kind tags), so editing a seed file and restarting is the normal way to evolve catalog data. Seed files live under `backend/data/seeds/` and use a standard `{metadata, items}` envelope.

To re-run a single stage against a running DB (without restarting the app):
```bash
cd backend && source venv/bin/activate
export PYTHONPATH=.:../
python -c "import asyncio; from app.core.database import AsyncSessionLocal; from app.services.seed_service import seed_service; asyncio.run(seed_service.seed_concepts())"
```
(replace `seed_concepts` with any of the `_SEED_STAGE_NAMES` methods.)

**Exporting an instance back to seeds** — the inverse of seeding. Build the
canonical taxonomy/anatomy/catalog in a running instance (UI + AI), then
snapshot it into `data/seeds/`. `SeedExportService`
(`backend/app/services/seed_export_service.py`) emits the slug-keyed format
for all nine seed files; output is deterministic so `git diff` is clean.
```bash
python scripts/export_seeds.py --dry-run           # preview counts
python scripts/export_seeds.py                     # global -> data/seeds (backed up)
python scripts/export_seeds.py --source TENANT_ID  # a template tenant as source
python scripts/unpack_seeds_zip.py seeds.zip       # unpack a downloaded ZIP (backup + extract)
```
There's also a UI path for instances on another machine: the **Export seeds**
button in the Catalogs workspace toolbar (`/catalogs`, SYSTEM_ADMIN-gated)
downloads a ZIP, which you transfer to the dev machine and unpack. See
[SEEDING_AND_DEMOS.md §7](SEEDING_AND_DEMOS.md#7-exporting-an-instance-back-to-seeds)
and [`backend/data/seeds/README.md`](../backend/data/seeds/README.md).

See [SEEDING_AND_DEMOS.md](SEEDING_AND_DEMOS.md) for the anatomy graph, demo data, and screenshot-friendly frozen-date seeding.

## Extending Clinical Events

The clinical events system is metadata-driven. To add new clinical event categories or types:

1.  **Edit the Seed File**: Add the new configuration to `backend/data/seeds/clinical_event_types.json`.
    -   Each item declares a `category_slug` (resolves to an `event_category` concept) and a `metadata_schema`.
    -   The `metadata_schema` is **validated** by the typed Pydantic model (`app/schemas/clinical_event.py:MetadataSchema`) on seed load and on API create — a malformed field raises a precise error instead of silently rendering nothing.
2.  **Sync with Database**:
    ```bash
    cd backend && source venv/bin/activate
    export PYTHONPATH=$PYTHONPATH:.
    python -c "import asyncio; from app.core.database import AsyncSessionLocal; from app.services.seed_service import seed_service; asyncio.run(seed_service.seed_clinical_event_types())"
    ```
3.  **UI Auto-Generation**: The `ClinicalEventModal` will automatically render the new category as a tab and the `DynamicMetadataForm` will generate the input fields based on the schema you defined.

### Field types (`metadata_schema.fields[]`)

Each field is a typed descriptor. The `type` discriminator drives an exhaustive renderer switch in `DynamicMetadataForm` (adding a new type without a render branch is a compile error).

| `type` | Render | Field-specific keys |
|---|---|---|
| `text` | Text input | `required` |
| `number` | Number input + optional scale badge | `min`, `max`, `required` |
| `date` | Date picker | `required` |
| `boolean` | Toggle switch | `required` |
| `catalog-select` | **`CatalogField` → `CatalogItemPicker`** (search any catalog) | `catalogs` (required), `concept_kind`, `multi`, `relation` |

### Authoring a `catalog-select` field

A `catalog-select` field lets the user pick from one or more catalogs (biomarker, medication, allergy, anatomy, vaccine, concept) via the same reusable picker that powers the catalog workspace. The picked value is stored in `event_metadata[field.name]` as `{type, id, label}` (single) or an array of those (when `multi: true`).

```jsonc
// Pick a single anatomy site (the legacy "body-parts" use case)
{"name": "body_location", "label": "Body Location",
 "type": "catalog-select", "catalogs": ["anatomy"], "multi": false,
 "relation": "primary_site"}

// Pick multiple related medications/allergies
{"name": "triggers", "label": "Related triggers",
 "type": "catalog-select", "catalogs": ["medication", "allergy"], "multi": true}

// Pick an examination category concept (narrowed by ConceptKind)
{"name": "category", "label": "Category",
 "type": "catalog-select", "catalogs": ["concept"],
 "concept_kind": "examination_category", "multi": false}
```

**Constraints (enforced server-side, fail-loud):**
- `catalog-select` requires a non-empty `catalogs` list.
- `concept_kind` may only be set when `catalogs` is exactly `["concept"]` (a kind filter is meaningless for other catalogs).

The `concept_kind` filter is applied by `search_catalogs(kind=…)` (`GET /catalogs/search?kind=…`), which powers examination-category / event-category / specialty pickers from the same endpoint.

## Extending Notifications

The notification framework is unified and multi-source — every kind of notice (scheduled reminders, biomarker thresholds, HITL proposals, integration sync, clinical events, system broadcasts) flows through one `notification_service.emit()` API. For detailed instructions on adding a new source or wiring new triggers, see [NOTIFICATION_SYSTEM.md](./NOTIFICATION_SYSTEM.md).

## Project Versioning

We utilize a centralized semantic versioning manager script located in
`scripts/version_manager.py` to synchronize versions across backend configs,
APIs, frontend packages, and installation docs.

> **Changelog rule:** every user-visible change adds **one bullet** under
> `## [Unreleased]` in `CHANGELOG.md` at commit time (see
> [RELEASE_PROCESS.md](RELEASE_PROCESS.md)). Do this proactively — do not wait
> to be asked.

> **Push policy:** the version manager defaults to **local-only**. Use `--git`
> to stage + commit + tag locally, and **stop there**. Do **not** add `--push`
> unless you explicitly want to publish to the online repository (it pushes to
> **every** configured remote and triggers CI/CD — Docker image builds + GitHub
> Release automation). When in doubt, ask before pushing.

For the full release workflow (commit-time changelog rule, RC/final flow,
catch-up procedure, GitHub Release automation), see
[RELEASE_PROCESS.md](RELEASE_PROCESS.md).

### Versioning Commands:
- **Show Current Version**:
  ```bash
  python3 scripts/version_manager.py show
  ```
- **Set Explicit Version**:
  ```bash
  python3 scripts/version_manager.py set 0.3.0-rc.2
  ```
- **Automatically Bump Version**:
  ```bash
  python3 scripts/version_manager.py bump [major | minor | patch | rc]
  ```
  *   `major`: Promotes to next major release (e.g. `0.3.0` -> `1.0.0`)
  *   `minor`: Promotes to next minor release (e.g. `0.3.0` -> `0.4.0`)
  *   `patch`: Promotes to next patch release or removes release candidate suffix (e.g. `0.3.0` -> `0.3.1`, `0.3.0-rc.2` -> `0.3.0`)
  *   `rc`: Sets or increments release candidate number on the upcoming release (e.g. `0.3.0` -> `0.3.1-rc.1`, `0.3.0-rc.1` -> `0.3.0-rc.2`)

### Git flags (local-first by default):
- `--git` or `-g` (**default stop point**): stages updated files (version files
  + `CHANGELOG.md` + `docs/RELEASE_PROCESS.md`), commits them with
  `chore(release): bump version to X.Y.Z`, and creates an annotated git tag
  `vX.Y.Z` — **locally only**. No push.
- `--push` or `-p` (**opt-in — only when you explicitly want to publish**):
  pushes both the new commit and the release tag to **every** configured remote,
  which triggers:
  - **Docker image builds** (`.github/workflows/docker-publish.yml`) —
    publishes backend + frontend images to `ghcr.io`.
  - **GitHub Release** (`.github/workflows/release.yml`) — creates a
    GitHub Release with notes extracted from `CHANGELOG.md`, automatically
    marked as a **prerelease** for RC/beta/alpha versions.
- **Catch-up (commit + tag the version already in `config.py`)**:
  ```bash
  python3 scripts/version_manager.py release --git
  ```
  Use this when you ran `set`/`bump` without `--git`, or edited
  `CHANGELOG.md` after the version bump. Add `--push` only if you want to
  publish.

**Examples:**
```bash
# Bump patch version, local commit + local tag (DEFAULT — no push)
python3 scripts/version_manager.py bump patch --git

# Bump RC version, local commit + local tag (DEFAULT — no push)
python3 scripts/version_manager.py bump rc --git

# Catch-up: you forgot --git earlier, or edited CHANGELOG after bump (local only)
python3 scripts/version_manager.py release --git

# ONLY when you explicitly want to publish to the online repository + trigger CI:
python3 scripts/version_manager.py release --git --push
```

## Key Files

### Backend (`backend/app/`)
- `main.py`: Entry point.
- `core/`: Config, Security, Database connection.
- `api/v1/endpoints/`: Route handlers.
- `services/`: Business logic & Database operations.
- `models/`: SQLAlchemy models (Core + FHIR).
- `ai/`: AI/ML pipeline — provider factory (`providers/`), OCR (`processors/ocr/`), structured extraction (`processors/nlp/`), the medical processing service (`pipeline/`), the agentic chat loop (`agents/`), chat tools (`tools/`), and Pydantic schemas (`schemas/`).
- `processors/`: Legacy NLP helpers and importers (most AI logic now lives under `ai/`).
- `workers/`: Background tasks.

### Frontend (`frontend/src/`)
- `App.tsx`: Routing.
- `components/ui/`: Immersion & Reusable components.
- `pages/`: View components.
- `store/`: Zustand state management.
- `services/`: API abstraction layer.

## Known Issues
1. **DICOM**: Local conversion requires `pydicom` and `numpy` in the environment.

## Code Style
- **Backend**: PEP 8, Type hints, Google-style docstrings.
- **Frontend**: Functional components, TypeScript for all props/state, Tailwind for layout.
