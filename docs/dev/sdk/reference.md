# Reference

Tables, not prose. Everything here is extracted from code (the vendored
charter contract modules, the engine, the endpoints) — if one of these
tables is wrong, ask the code, not the table.

## The 15 extension error codes

`ExtensionError` (`app/extensions/errors.py`) — closed enum, detail
capped at 500 chars, `retryable` is a closed sub-vocabulary
(`yes | no | after-delay | after-config-change`); the defaults below are
family law (one source: `app/extensions/polite_http.py`).

| Code | Retryable default | Meaning |
|---|---|---|
| `not-ready` | after-config-change | Engine/model/task unassigned, extension disabled, or surface prerequisites missing. |
| `needs-secret` | after-config-change | A declared secret slot is absent or unresolved — the user must supply it. |
| `invalid` | no | Malformed capability arguments at the boundary (the install-time validator should have caught it earlier). Also: configuration invalid — the user must **reconfigure**. |
| `unsupported` | no | The host does not honor this capability/kind/tier/surface. |
| `io` | no | OS-level failure on an allowed path. |
| `not-found` | no | Unknown id or artifact past retention. |
| `path-denied` | no | Outside the root allowlist / traversal escape. Never retried with the same arguments. |
| `net-denied` | no | The SSRF guard blocked the request (reserved range, forbidden scheme, unallowed host hop). Non-retryable config-class: logged, the instance stays ACTIVE (the historical SSRFBlock semantics). |
| `auth` | after-config-change | Upstream rejected credentials (401/403 class) → the instance flips to **ERROR** (reconnect prompt). Never retried with the same credentials. |
| `rate-limited` | after-delay | 429 engaged; `retry_after_seconds`/`retry_after_ms` surface the `Retry-After` payload → the worker writes a Redis cooldown key (`sync_cooldown:{integration_id}`, TTL clamped `[60s, 1h]`). |
| `unreachable` | yes | Transport-level failure before an HTTP verdict. |
| `protocol` | after-config-change | Wire-shape violation from the endpoint (malformed payload, unexpected content type). |
| `provider` | yes | Upstream server failure (5xx after retries). |
| `budget` | after-config-change | Run/call budget exhausted; work stops cleanly and remains resumable. |
| `canceled` | no | Abort signal or the user canceled. |

## The bridge error vocabulary (`BridgeError`)

The inbound bridge API's client-facing typed errors (closed; adding a
code requires the schema regeneration + this table, same commit — the
published schema's `errors.codes` is extracted from the live code):

| Code | HTTP | Raised when |
|---|---|---|
| `not_found` | 404 | A foreign or missing session/exam/document/etc. row — patient-scoped isolation means "someone else's" and "doesn't exist" are indistinguishable by design. |
| `turn_conflict` | 409 | The session's single-flight turn lock is held (retry with a NEW turn, not a replay). |
| `hitl_conflict` | 409 | The HITL proposal was already resolved. |
| `validation` | 400 | Malformed input or a failed guard. |
| `stt_failed` | 502 | The upstream speech-to-text provider failed on `/ai/transcribe`. |

Wire shape: `{"detail": {"code": ..., "message": ...}}`; legacy servers
answer plain-string `{"detail": "..."}` (SDKs decode both shapes —
`code` is `null`/`undefined` for the plain-string form).

## Status vocabularies

**Instance status** (`UserIntegration.status`): `ACTIVE` · `PENDING`
(awaits OAuth) · `ERROR` (auth failure; reconnect prompt).

**Sync status** (`sync_status` on the views + `IntegrationSyncLog`):
`ok` → last sync succeeded · `error` → failure (with
`consecutive_failures`) · per-run statuses `success` / `partial`
(`dropped_invalid > 0`) / `skipped` (another sync holds the lock, or a
rate-limit cooldown) / `failed`.

**Bridge `bridge_features` (closed, append-only):** `exam_enrichment` ·
`ai_assistant` · `ai_streaming`. Absence of a flag = feature
unavailable — clients degrade gracefully, never error.

**`frontend_base_url` / `latest_sdks` / `cursor`:** the signed-status
discovery payload fields (see the bridge's api-reference table).

## Endpoints (the `/api/v1/integrations/` prefix)

| Route | Auth | Purpose |
|---|---|---|
| `GET /available` | user | The extension catalog (bundled, enabled-by-default profile) |
| `GET /active?patient_id=` | user | The caller's instances |
| `GET /{domain}/config-flow` | user | The config UI schema |
| `POST /{domain}/config-flow?patient_id=&integration_id=` | user | Create/update an instance (show-once machine secrets) |
| `GET /instance/{id}/details?patient_id=` | user | Instance detail (`user_config` with secrets masked) |
| `POST /instance/{id}/sync?patient_id=` | user | Manual sync (409 while locked/cooling down) |
| `DELETE /instance/{id}` | user | Delete instance |
| `POST /instance/{id}/action/{action_id}` | user | Custom action (optional JSON body → kwargs) |
| `POST /instance/{id}/rotate-secret` | owner | Rotate a machine secret (show-once) |
| `POST /{domain}/webhook/{integration_id}` | HMAC | Inbound webhook (machine body ≤ 40 MiB) |
| `[ANY] /{domain}/api/{integration_id}/{path}` | HMAC | Two-way API proxy |
| `POST /{domain}/oauth/start` · `GET /{domain}/oauth/callback` | user / state | The OAuth round-trip (SMART DCR + PKCE), provider must offer OAuth |
| `GET /{domain}/documentation` | user | In-app docs (`docs/docs-tree.json` category lists) |
| `GET /{domain}/schema` | user | Published sub-API schema (schema-capable bridges; today `health_assistant_bridge`) — no secrets |
| `GET/POST /instance/{id}/proposals[...]` | owner / ADMIN+ | HITL proposal list / detail / resolve |
| `POST /{domain}/notification-action/{iid}/{action_id}` | owner | Notification POST-action dispatch |
| `POST /admin/integrations/{domain}/enable|disable` | SYSTEM_ADMIN | Admin gating |
| `GET /admin/integrations` | SYSTEM_ADMIN | Admin catalog w/ enabled rows |

## The sync pipeline stages (in order, per instance run)

`pull` (declared `sync`) → **write-time FHIR validation + biomarker
map** (`validate_and_filter_observations` +
`map_observations_to_biomarkers`, auto-create missing definitions) →
**telemetry/FHIR split** (by `BiomarkerDefinition.is_telemetry`) →
`pull_clinical_events` → `pull_examinations` → `pull_catalog_proposals`
(auto-apply, cap 50/sync; concepts before edges) → `pull_hitl_proposals`
(cap 20/sync, dedup + inbox notification) → `pull_documents` (caps 20
docs / 50 MiB/sync) → `pull_medications` / `pull_allergies` /
`pull_immunizations` → `get_notifications` (≥1 persisted observation) →
`push_data` (declared `push`) → `IntegrationSyncLog` + `last_synced_at`
stamp → commit. Redis locks: `sync_lock:{iid}` around runs,
`sync_cooldown:{iid}` after rate-limits.

## Settings knobs (env)

| Name | Default | Governs |
|---|---|---|
| `HA_DATA_KEY` (+ `_PREVIOUS` ring) | — (dev: ephemeral) | The Fernet at-rest key for every secret slot |
| `INTEGRATION_MAX_TOOLS_PER_SESSION` | 20 | Integration tools merged per chat turn |
| `INTEGRATION_ALLOWED_HOSTS` | `""` | SSRF escape hatch (extra allowed hosts) |
| `INTEGRATION_BLOCK_PRIVATE_RANGES` | `true` | SSRF private-range posture |
| `INTEGRATION_ALLOW_PRIVATE` | `false` | SSRF private allow (trusted LAN targets) |
| `INTEGRATION_MAX_PROPOSALS_PER_SYNC` | 50 (module constant) | Catalog-proposal cap per sync |
| `INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC` | 20 (module constant) | HITL cap per sync |
| `INTEGRATION_MAX_DOCS_PER_SYNC` | 20 (module constant) | Document cap per sync |
| `INTEGRATION_MAX_DOC_BYTES_PER_SYNC` | 50 MiB (module constant) | Document byte cap per sync |
| `HA_RATELIMIT_AI` | 30/5min | The web + bridge AI budget (0 = disabled) |
| `MCP_*` | see the MCP table | MCP Client limits/errors/allowlist (mcp_client docs) |
| `INTEGRATION_RATE_LIMIT_*` | — | rate-limit cooldown window constants (sync service) |

## The driver cadence (workers)

- Celery **beat** fires every 60 s (`sync_active_integrations`) — each
  ACTIVE instance's `sync_interval` (default 15 min) gates whether it
  actually runs on a given tick.
- `pull`-less kinds (bridges, webhook receivers) declare nothing to the
  beat — their ingestion is push-driven by design.
- Manual "Sync Now" and the beat share `run_sync`; the Redis lock makes
  concurrency safe rather than double-writing.

## Where to read the code

| Surface | Module |
|---|---|
| error codes + wire shape | `backend/app/extensions/errors.py`, `backend/app/extensions/polite_http.py` |
| the pipeline | `backend/app/services/integration_sync_service.py` |
| the endpoints | `backend/app/api/v1/endpoints/integrations.py`, `admin_integrations.py`, `instances.py` |
| the schema exporter | `backend/scripts/export_bridge_schema.py` |
