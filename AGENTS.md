# AGENTS.md — Health Assistant (core)

Health Assistant is a self-hosted, privacy-first, open-source health records
platform (FastAPI + React + FHIR + a Biomarker Engine). This repo (`core/`)
holds the **backend, the extension subsystem (integrations framework), and
the web frontend**. The Android companion app lives in a separate repository;
the Kotlin bridge SDK ships here under
`integrations/health-assistant-bridge/kotlin-sdk/`.

The API is server-first and always authenticated: multi-tenant with hard
`tenant_id` filtering on every query, roles, invites, SMART-on-FHIR scopes,
an ownership cascade, and per-purpose signing keys (session / refresh /
data-at-rest).

## Repo map
```
core/
├── backend/      # FastAPI app (app/), alembic migrations, tests/, scripts/, venv/
│   └── app/extensions/   # the extension subsystem (family extension
│                         # charter home): vendored family contract modules,
│                         # registry, runtime, the authoring SDK and builtin/
├── integrations/ # bridge client SDK satellites only (python/ts/kotlin —
│                 # consumer-facing packages, never imported by the backend);
│                 # the framework itself lives in backend/app/extensions/
├── frontend/     # React 18 + Vite + TS + Tailwind + Zustand
├── docs/         # docs-tree.json is the source of truth for public nav + SEO
└── docker/ scripts/ uploads/ logging/
```

## Build & test
```bash
# Backend (needs the dev DB up: docker/docker-compose.dev-db.yml)
./scripts/run-dev.sh                          # honcho: backend + worker + beat + flower + frontend
cd backend && ./run-tests.sh [tests/test_x.py] # pytest (async; requires the migrated test DB)
cd backend && ruff check && ruff format        # lint/format
bash scripts/install.selftest.sh              # install path (offline; no Docker daemon)
# Frontend
cd frontend && npm run build && npm run lint   # build = tsc && vite build
```
- Backend tests: `backend/pyproject.toml` `[tool.pytest.ini_options]`
  (`asyncio_mode=auto`, `.env.test`). Real Postgres test DB required
  (`conftest.py` runs `alembic upgrade head`).
- `PYTHONPATH=.` from `backend/` — the extension subsystem is an ordinary
  `app.*` package (`app/extensions/`).

## Conventions that apply everywhere
- **Tenant isolation is hard**: every query filters `tenant_id`; `USER` role is
  further restricted to `Patient.user_id == current_user.user_id`.
- **JSONB mutations** need `flag_modified(obj, "field")` before commit.
- **No comments unless requested**; Google-style docstrings on public APIs.
- **Extension source of truth is `backend/app/extensions/`** (registry, SDK,
  runtime, `builtin/`); `integrations/` holds only the bridge client SDK
  satellites (never imported by the backend).
- **Always update `CHANGELOG.md`** under `## [Unreleased]` for user-visible changes.
- **A shipped feature updates `core/docs/capabilities.json`** (family contract: `contracts/product-capabilities.schema.json`) in the same change.
- **Never push to the online repo by default** — `version_manager.py --git` stops at a
  local commit + tag; add `--push` only when explicitly asked.
- **Generic UI primitives** come from
  [`@neuronection/assistant-ui`](https://github.com/neuronection/assistant-ui)
  (first-party: published npm package + public repo, shared with the other
  Neuronection assistants) — extend the library rather than forking it; its
  repo documents the contribution flow.
- **Translations ship complete, in the same change**: `frontend/src/locales/en.json`
  is the source of truth and `el.json` must match it key-for-key
  (`python3 scripts/translations/check_translations.py` — the family manager,
  CI-gated). Four walls guard it: that gate (dictionary ↔ dictionary),
  `frontend/src/tests/i18n.audit.test.ts` (static `t()` keys + dynamic
  families), `backend/tests/test_frontend_i18n_keys.py` (keys the backend
  emits: labels, settings options, setup steps, seeded event copy), and
  `scripts/translations/inventory.py --check` (reachability — no key that
  nothing renders). Never leave
  an English `defaultValue` where a key belongs: a missing key renders the raw
  key to the user. See `docs/DEVELOPMENT.md § Internationalization`.

## Git & GitHub

`main` is protected by the repository ruleset `ruleset_v1` (created 2026-11-07):

- changes land through a pull request; direct pushes are rejected (`GH013`);
- six required checks, with “require branches to be up to date before
  merging”:
  - `Run Test Suite / Run Backend Pytest Suite`
  - `Run Test Suite / Run Frontend Lint & Build Test`
  - `Run Test Suite / Run Frontend Vitest Suite`
  - `pip-audit (backend)`
  - `pnpm audit (frontend)`
  - `gitleaks (repo history)`
- no bypass actors; zero required approvals; merge, squash and rebase are
  allowed;
- auto-merge is disabled.

Work on a feature branch, push it to `github` (the public repository), open a
PR against `main`, wait for the six checks, then merge. Never push to `main`,
add a bypass actor, or disable the ruleset to land a change. The `origin`
remote is the private mirror; do not use it for release or PR work.

## Mobile (Android)
The Kotlin bridge SDK satellite commits in this repo
(`integrations/health-assistant-bridge/`); build it with Gradle after setting
`JAVA_HOME` (JDK 25) and `ANDROID_HOME` to your local SDK path:
```bash
cd integrations/health-assistant-bridge/kotlin-sdk && ./gradlew build
```

## Docs
Public documentation lives in `docs/` and is navigated via `docs-tree.json`
(the source of truth for the website nav + SEO metadata). Plans, internal
process docs, and maintainer instructions are maintained **locally, outside
this repository**.
