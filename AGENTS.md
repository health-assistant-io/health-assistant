# AGENTS.md — Health Assistant (core)

Health Assistant is a self-hosted, privacy-first, open-source health records
platform (FastAPI + React + FHIR + a Biomarker Engine). This repo (`core/`) holds
the **backend, the integrations framework, and the web frontend**. The Android
companion app lives in a **sibling repo** at `../app/` (see *Mobile* below).

Before any task, load the matching skill in `.opencode/skills/`:
`backend`, `frontend`, `ai-pipeline`, `clinical-data`, `integrations`, `mobile`,
`documentation`, `versioning`, `seeding`, `hitl-task-cards`.

## Repo map
```
core/
├── backend/      # FastAPI app (app/), alembic migrations, tests/, scripts/, venv/
├── integrations/ # SOURCE OF TRUTH for integrations (NOT backend/app/integrations)
├── frontend/     # React 18 + Vite + TS + Tailwind + Zustand
├── docs/         # docs-tree.json is the source of truth for public nav + SEO
├── docker/ scripts/ uploads/ logging/
└── .opencode/skills/  # agent skills (local; gitignored)
```

## Build & test
```bash
# Backend (needs the dev DB up: docker/docker-compose.dev-db.yml)
./scripts/run-dev.sh                          # honcho: backend + worker + beat + flower + frontend
cd backend && ./run-tests.sh [tests/test_x.py] # pytest (async; requires the migrated test DB)
cd backend && ruff check && ruff format        # lint/format
# Frontend
cd frontend && npm run build && npm run lint   # build = tsc && vite build
```
- Backend tests: `backend/pytest.ini` (`asyncio_mode=auto`, `.env.test`). Real
  Postgres test DB required (`conftest.py` runs `alembic upgrade head`).
- `PYTHONPATH=.:..` from `backend/` so `app.*` + `integrations.*` both resolve.

## Conventions that apply everywhere
- **Tenant isolation is hard**: every query filters `tenant_id`; `USER` role is
  further restricted to `Patient.user_id == current_user.user_id`.
- **JSONB mutations** need `flag_modified(obj, "field")` before commit.
- **No comments unless requested**; Google-style docstrings on public APIs.
- **Integrations source of truth is `integrations/`**, never `backend/app/integrations/`.
- **Always update `CHANGELOG.md`** under `## [Unreleased]` for user-visible changes.
- **Never push to the online repo by default** — `version_manager.py --git` stops at a
  local commit + tag; add `--push` only when explicitly asked.

## Mobile (Android) — two-repo workflow
The app is at `../app/` (`app/android` Compose app + `app/shared` KMP core; the
Kotlin bridge SDK is in this repo at
`integrations/health_assistant_bridge/kotlin-sdk/`). Load the **`mobile`** skill
for full details. Quick:
```bash
export JAVA_HOME=/usr/lib/jvm/java-25-openjdk-amd64 ANDROID_HOME=/home/ilias/Android/Sdk
cd ../app/android && ./gradlew build          # app + shared + kotlin-sdk
```
Mobile-app code commits in `../app/` (its own git repo); Kotlin-SDK + backend
bridge changes commit here in `core/`.

## Skills index (load the matching one before the task)
| Task | Skill |
|---|---|
| Backend endpoint/service/model/migration/Celery | `backend` |
| Frontend page/component/store/hook/PWA | `frontend` |
| AI/OCR/NLP/chatbot/Magic Fill/AI config | `ai-pipeline` |
| FHIR/biomarkers/telemetry/taxonomy/analytics | `clinical-data` |
| Wearables/labs/webhooks/Integrations SDK | `integrations` |
| Android app / KMP / Kotlin SDK / bridge read paths | `mobile` |
| Docs, docs-tree.json, SEO, positioning | `documentation` |
| Version bump / release / CHANGELOG | `versioning` |
| New shared-looking UI / family UI library | `ha-assistant-ui` |

## Shared UI library (assistant-ui)

`@neuronection/assistant-ui` is **our first-party family library**
(sibling checkout `../../assistant-ui`), shared with study- and
career-assistant — see the `ha-assistant-ui` skill. Core rules:

- **Check the library first for NEW generic UI** — no new local copies of
  things the family ships — the app is fully adopted: `components/ui`
  are library re-export shims, the selectors are async Combobox wrappers,
  and the drift audit is clean. Never reintroduce a local implementation
  (`LegacyPopover` is deleted); extend the library instead.
- First-party means mutable: if the API doesn't fit, change the library
  (two-app rule + `scripts/verify-in-app.mjs` — details in the skill),
  never fork or wrap it.
- Styling via `--as-*` tokens / `data-as-*` only.
