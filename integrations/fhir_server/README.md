# FHIR Server (Hospital Sync)

Connect an external FHIR R4 server — a hospital (Epic/Cerner), a personal health
record, or any SMART-on-FHIR endpoint — and **two-way sync** your Observations:
pull remote results into the Health Assistant Biomarker Engine, and push local
Observations back out.

**Stage 2 + 2b (current):** pull + push, SMART **Patient/Standalone Launch**
with Dynamic Client Registration, configurable sync direction, and manual
action buttons.

## How it works

Choose an **Authorization** mode when configuring the instance:

- **SMART** (`smart`, default) — for hospitals / Epic / Cerner / the SMART Health IT
  sandbox. After saving, click **Authorize** to run the SMART standalone-launch
  round-trip (Health Assistant auto-registers via Dynamic Client Registration —
  no client ID needed). The instance is `PENDING` until the callback stores the
  encrypted tokens, then flips to `ACTIVE`.
- **None / tokenless** (`none`) — for local or open FHIR servers (e.g. a local
  [HAPI FHIR](https://hapiproject.org/) in Docker). No authorize step; the
  instance goes straight to `ACTIVE` and operates without a token.

> Vanilla HAPI FHIR does **not** serve `/.well-known/smart-configuration`, so use
> the **None** mode for it. (The SMART Health IT sandbox is the simplest server
> that supports the full SMART round-trip.)

### Sync direction

`sync_direction` controls what the **scheduled** sync and the platform
**Sync Now** button do:

| Value | Behaviour |
|-------|-----------|
| `both` (default) | Pull remote Observations in **and** push local Observations out |
| `pull_only` | Only pull from the FHIR server into Health Assistant |
| `push_only` | Only push local Observations to the FHIR server |
| `none` | No automatic sync — use the action buttons manually |

### Pull

A bounded FHIR search
(`Observation?patient=<remote>&_lastUpdated=gt<cursor>&_count=100&_sort=_lastUpdated`,
+ optional `category`) pulls new results, maps each through the Biomarker Engine,
and routes telemetry. A `_lastUpdated` cursor makes subsequent pulls incremental.
Multi-component observations (e.g. blood pressure `85354-9` with systolic +
diastolic) and `note[]` are preserved end-to-end (component → JSONB column,
note → `comment` field).

### Push

Local Observations are pushed to the external server via **FHIR conditional
update** — `PUT /Observation?identifier=urn:healthassistant:observation|<local-uuid>`
with the canonical FHIR body. This is idempotent:

- The **subject** is rewritten from `Patient/<local>` to `Patient/<remote>`.
- A stable **identifier** (the local UUID) lets the server dedupe across pushes.
- The server-assigned `id` and `meta.versionId` are dropped so the server owns them.
- `412 Precondition Failed` is treated as **skipped** (no change needed).
- Observations **sourced from this integration** are excluded (no pull→push echo).
- Only **LOINC/SNOMED**-coded observations are pushed — custom biomarkers have no
  hospital terminology.
- The `last_pushed_at` cursor only advances past **successfully** pushed rows —
  transiently-failed rows are retried on the next sync. If an entire batch fails,
  the cursor doesn't move (full retry next cycle).
- After each successful push, a **Provenance** is POSTed to the remote server
  (best-effort — a server that doesn't support Provenance is silently skipped).
  The push result carries `provenance_created` / `provenance_failed` counts.
- **Per-row 401-race retry**: if a token expires mid-batch, the push force-refreshes
  and retries that row once. One bad row counts as `error` and the batch continues.
- **Insufficient scope**: if the SMART server rejects a push with `insufficient_scope`
  (the token lacks `patient/*.write`), the push stops and surfaces an actionable
  "re-authorize" warning in the result.

## Action buttons

The instance detail page exposes these manual actions (they ignore
`sync_direction`):

| Action | What it does |
|--------|--------------|
| **Check Connection** | `GET {base}/metadata` — verifies the server is reachable and (SMART) the token still authenticates; shows the CapabilityStatement summary (FHIR version, software, supported resources). |
| **Pull Now** | Runs an explicit pull **and persists** the results immediately (bypasses `sync_direction`). |
| **Push Now** | Runs an explicit push of local Observations (bypasses `sync_direction`). Reports created / updated / skipped (412) / errors + provenance counts. If the token lacks write scope, stops early with an actionable "re-authorize" warning. |
| **Push Preview** | Dry-run: lists the Observations that *would* be pushed (after echo + coding filters), without sending anything. Use this to verify the echo exclusion. |
| **Reset Cursors** | Clears the pull/push cursors so the next sync re-pulls/re-pushes the full configured window. |

## Scope

### Coding scope

Only LOINC/SNOMED-coded observations sync meaningfully. Local-only (custom)
biomarkers stay internal — they don't exist in hospital terminology.

### OAuth scope

Push requires `patient/*.write` (requested via `PUSH_SCOPES` when
`sync_direction` is `both` or `push_only`). If you switch `sync_direction`
to push after initially authorizing with read-only scopes, click
**Authorize** again — the SMART consent screen will request the write
scope. A missing write scope surfaces as an `insufficient_scope` 403 on
the first push attempt (the push stops with an actionable warning).

Disconnecting an instance (DELETE) now **revokes the remote tokens** at
the SMART `revocation_endpoint` (best-effort — if the server doesn't
support revocation, the integration is deleted regardless).

## Debugging

Toggle **Debug Mode** on the instance to capture structured per-step payloads in
the Debug Console: search URLs/params, response counts, per-resource mapping
decisions, push candidate filtering (echo / non-standard coding), per-observation
push status, token refresh events, and HTTP headers (Authorization redacted).

## Local development / testing

- **SMART Health IT sandbox** — `https://r4.smarthealthit.org` (allows
  `localhost` redirect URIs, dynamic registration, synthetic patients).
- **Local HAPI FHIR** — run in Docker for offline edge-case testing
  (pagination, `OperationOutcome`, 412 conflicts).

### Tokenless / local server (e.g. HAPI FHIR)

No Redis or secret key needed for this mode.

1. **Enable + run a local FHIR server**:
   ```bash
   docker compose -f docker/fhir-test-server/docker-compose.yml up -d   # HAPI on ${HAPI_PORT:-8080}/fhir
   cd backend && python scripts/enable_integration.py fhir_server && # restart backend
   ```
2. **Configure** — Settings → Integrations → FHIR Server. Enter
   `http://localhost:${HAPI_PORT:-8080}/fhir` as the FHIR Base URL, set
   **Authorization = None**, pick the local patient + window/categories + sync
   direction, save. The instance is `ACTIVE` immediately.
3. **Check Connection** — verify the server responds (CapabilityStatement).
4. **Sync / Pull / Push** — use the platform Sync button or the action buttons.

### SMART (sandbox / hospital)

1. **Enable the integration** (headless):
   ```bash
   cd backend && source venv/bin/activate
   python scripts/enable_integration.py fhir_server
   # restart the backend so the registry loads the provider
   ```
   (or via SQL: `INSERT INTO system_integrations (domain, is_enabled) VALUES ('fhir_server', true) ON CONFLICT (domain) DO UPDATE SET is_enabled = true;`)
2. **Configure** — FHIR Base URL `https://r4.smarthealthit.org`, **Authorization =
   SMART**, pick the local patient + window/categories + sync direction, save.
   Instance is `PENDING`.
3. **Authorize** — the "Authorization required" banner shows an **Authorize**
   button. It discovers `/.well-known/smart-configuration`, dynamically
   registers, and redirects to the sandbox login. On return the callback stores
   the encrypted tokens and flips the instance to `ACTIVE`.
4. **Check Connection** — confirm the token works.
5. **Sync** — hit **Sync** (or wait for the background cadence) / use Pull/Push.
   Toggle **Debug Mode** to inspect raw payloads.

Prerequisites in the root `.env` (SMART mode only):
```
INTEGRATION_SECRET_KEY=<fernet key>     # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
REDIS_URL=redis://localhost:6379        # OAuth state lives here
FRONTEND_URL=http://localhost:3000      # where the OAuth callback redirects
```

## Files

- `manifest.json` — `integration_type: ["pull", "push"]`, `access_type: "cloud"`.
- `config_flow.py` — PENDING instance, pull bounds, `sync_direction`; `is_oauth = True`.
- `provider.py` — `SmartOAuth` refresh-on-use + `revoke()` on disconnect;
  `_run_pull` (bounded search + biomarker mapping);
  `_run_push` (conditional update + echo exclusion + 412 handling +
  remote Provenance POST + per-row 401-race retry + cursor integrity +
  insufficient_scope detection); 5 custom actions.

See [docs/FHIR_R4_FACADE.md](../../docs/FHIR_R4_FACADE.md) for the Stage 3 facade
that exposes Health Assistant itself as a FHIR R4 server, and
`integrations/sdk/auth.py` / `integrations/sdk/fhir.py` for the auth + FHIR
primitives this provider builds on.
