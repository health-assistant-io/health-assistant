# AGENTS.md — Health Assistant (core)

Health Assistant is a self-hosted, privacy-first, open-source health records
platform (FastAPI + React + FHIR + a Biomarker Engine). This repo (`core/`)
holds the **backend, the integrations framework, and the web frontend**. The
Android companion app lives in a separate repository; the Kotlin bridge SDK
ships here under `integrations/health_assistant_bridge/kotlin-sdk/`.

The API is server-first and always authenticated: multi-tenant with hard
`tenant_id` filtering on every query, roles, invites, SMART-on-FHIR scopes,
an ownership cascade, and per-purpose signing keys (session / refresh /
data-at-rest).

## Repo map
```
core/
├── backend/      # FastAPI app (app/), alembic migrations, tests/, scripts/, venv/
├── integrations/ # SOURCE OF TRUTH for integrations (NOT backend/app/integrations)
├── frontend/     # React 18 + Vite + TS + Tailwind + Zustand
├── docs/         # docs-tree.json is the source of truth for public nav + SEO
├── docker/ scripts/ uploads/ logging/
└── .opencode/    # local agent/tool config (gitignored)
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
- Backend tests: `backend/pyproject.toml` `[tool.pytest.ini_options]`
  (`asyncio_mode=auto`, `.env.test`). Real Postgres test DB required
  (`conftest.py` runs `alembic upgrade head`).
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
- **Generic UI primitives** come from
  [`@neuronection/assistant-ui`](https://github.com/neuronection/assistant-ui)
  (first-party: published npm package + public repo, shared with the other
  Neuronection assistants) — extend the library rather than forking it; its
  repo documents the contribution flow.

## Mobile (Android)
The Kotlin bridge SDK + backend bridge changes commit in this repo
(`integrations/health_assistant_bridge/`); build it with Gradle after setting
`JAVA_HOME` (JDK 25) and `ANDROID_HOME` to your local SDK path:
```bash
cd integrations/health_assistant_bridge/kotlin-sdk && ./gradlew build
```

## Docs
Public documentation lives in `docs/` and is navigated via `docs-tree.json`
(the source of truth for the website nav + SEO metadata). Plans, internal
process docs, and maintainer instructions are maintained **locally, outside
this repository**.
