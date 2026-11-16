# Seed sources — internal implementation notes (program Phase 10A)

Maintainer-facing notes for the seed source lane.
Public documentation lives in `docs/SEEDING_AND_DEMOS.md` (§4, "The source
lane") and `docs/API.md` (§`admin/seed-sources`); this file carries the
shapes and decisions a contributor (notably the Sources UI) needs.

## Module map

| Path | Role |
|---|---|
| `backend/app/services/seed/transport.py` | The shared fetch transport: `fetch_pack(source)` → `FetchedPack` (`manifest` dict + `files` map + pins). `SeedTransportError(code, message)` — closed code set. |
| `backend/app/services/seed/secrets.py` | Secret-slot resolution through `nx_auth.secrets` (auth-kit 0.6.0; keyring `health-assistant/seed-sources`, env fallback `HA_SEED_SOURCE_SECRET_<SLOT>`). `redact_secret` is imported from `nx_auth.secrets` — it is no longer defined locally. |
| `backend/app/services/seed/sources_service.py` | Registration, sync, upload, lifecycle, plan/apply/adopt/retire, content cache. `SeedSourceError(code, message)`. |
| `backend/app/api/v1/endpoints/admin_seed_sources.py` | The admin router (`/api/v1/admin/seed-sources`, prefix set in `app/api/v1/__init__.py`). |
| `backend/app/services/seed/planner.py` / `applier.py` | The Phase-9B engine, now tenant-aware. |
| `backend/app/workers/tasks.py` (`sync_seed_sources`) + `celery_app.py` beat | The 6h sync cycle (soft 1500s / hard 1800s per-task limits). |

The source **grammar** is not app-side: `backend/app/extensions/extension_sources.py`
(vendored byte-identically from the family extension contract) is the one
parser for the https-only URL, the bounded ref / 40-64-hex commit pin, the
subpath and the pack-archive entry allowlist + `MAX_ARCHIVE_ENTRIES`.
`transport.py` maps its `SourceRefError.code` onto `SeedTransportError` 1:1
and keeps no regex of its own; the archive reader and the SSRF guard stay
here (`polite_http`).

## Secret slots (auth-kit 0.6.0)

Resolution goes through `nx_auth.secrets` (`service="health-assistant/seed-sources"`,
`store_path=None`, sealed with `SecretCipher`): probed keyring → env
fallback `HA_SEED_SOURCE_SECRET_<SLOT>`. SSO uses the same module with
`service="health-assistant/tenant-sso"` and `HA_SSO_CLIENT_SECRET_<SLOT>`.

Operator notes:

- **Provision a writable keyring.** The kit selects the OS keyring only
  after a successful write round-trip (write, read back, delete). A
  keyring that is readable but not writable (read-only mount, locked
  Secret Service, `keyring.backends.null`) is **not** selected and
  resolution falls back to the env slot — storing a credential there
  would silently discard it. Health's Docker deployment is env-only and
  reports `backend_name() == "none"`; that is expected, not a failure.
- **Env prefix spelling.** The prefixes are `HA_SEED_SOURCE_SECRET` and
  `HA_SSO_CLIENT_SECRET`, **without a trailing underscore** — the kit
  splices the separator itself, so `HA_SEED_SOURCE_SECRET_` produces a
  double separator that matches nothing.
- **Redaction is the kit's.** `redact_secret` comes from
  `nx_auth.secrets`; do not reintroduce a local copy.

## Route inventory / authz

16 routes, owner `admin_seed_sources`, all `session` class. Central rules
(`backend/app/core/authz/route_policy.py`): approve/reject/revoke require
`seed_source:approve@tenant`; everything else `seed_source:read` /
`seed_source:manage@tenant` (ADMIN holds all three at TENANT scope;
SYSTEM_ADMIN at SYSTEM). Row-level scoping is the endpoint's job: a
non-SYSTEM_ADMIN actor touching a source outside their tenant gets the
hidden 404 (`SOURCE_NOT_FOUND`; the runs endpoints hide other sources'
runs the same way, `RUN_NOT_FOUND` for unknown ids). Regenerate the
inventory/matrix with:

```bash
cd backend && HA_ENV_FILE=.env.test uv run python scripts/authz_inventory.py check
HA_ENV_FILE=.env.test uv run python scripts/authz_inventory.py matrix
HA_ENV_FILE=.env.test uv run python scripts/authz_inventory.py policies
```

## Wire shapes (the Sources UI contract)

Source view (`GET /admin/seed-sources` entries):

```json
{
  "id": "…", "tenant_id": null, "kind": "file|git|registry|bundled",
  "uri": "…", "ref_spec": "v1.2.3", "subpath": "packs/core",
  "priority": 100, "scope": "system|tenant", "enabled": true,
  "auth_secret_ref": "github-read",
  "current_version_id": "…", "last_check_at": "…", "last_error": null,
  "created_at": "…", "updated_at": "…",
  "versions": [
    { "id": "…", "digest": "<64 hex>", "commit_sha": "…", "etag": "…",
      "status": "pending|approved|rejected|revoked", "reason": null,
      "approved_by": "…", "approved_at": "…", "contract_version": "1.0",
      "created_at": "…", "updated_at": "…",
      "pack_id": "acme-catalog", "pack_version": "1.2.0",
      "pack_scope": "system", "pack_priority": 100,
      "pack_files": 12,
      "is_current": true,
      "latest_run": { "run_id": "…", "mode": "plan|apply",
                       "status": "ok|partial|failed|conflicted|running",
                       "started_at": "…", "stats": { "…": 0 } } }
  ]
}
```

Plan document (`GET /versions/{id}/plan`, or `POST …/apply` with the
default body): the deterministic `PackPlan.to_doc()` —
`{pack: {id, version, digest, scope, priority}, source: {id, kind},
stages: {<stage>: [{stage, entity_type, seed_key, entity_id, action,
content_hash, update_available, conflict, warnings}]}, retire: [...],
warnings: [...], errors: [], summary: {create: n, update: n, noop: n,
skip: n, conflict: n, retire: n, warnings: n, errors: n}}`. An entry with
`update_available: true` + `action: "skip"` + `conflict.reason ==
"user_modified_freeze"` is an "update available" row — the adopt door
(`POST /{source_id}/adopt` with `{"seed_key": …}`) clears it.

Apply response (`POST /versions/{id}/apply {"apply": true}`):
`{"mode": "apply", "runs": {"<version_id>": "<run_id>"},
"statuses": {"<version_id>": "ok|partial|failed|conflicted"},
"stats": {...}}`. `partial` keeps the pointer and names the item errors in
the run's `stats.errors` / run items — the re-apply door retries exactly
the failed items.

Run history (the Sources UI's per-source history view; Phase 10B):
`GET /{source_id}/runs?limit=` (≤100, newest first) returns
`{…run.to_dict(), digest}` per run — `run.to_dict()` carries
`stats`/`plan` JSONB since 10B; `GET /{source_id}/runs/{run_id}` adds the
per-item outcomes from `seed_import_run_items` (`entity_type`, `seed_key`,
`action`, `content_hash`, `conflict`).

Errors: every failure is `{"detail": {"code": "<TYPED_CODE>",
"message": "…"}}` with the codes listed in `docs/API.md`; transport codes
map to 409 except `PACK_INVALID` (422), grammar/`INVALID_*` (400) and
`SOURCE_NOT_FOUND` (404).

## Decisions worth remembering

- **Priority coherence (program item 5):** registration pins the manifest
  to the source row (`SCOPE_MISMATCH`/`PRIORITY_MISMATCH` otherwise); the
  planner/applier read `source.priority` only. The bundled source pins 0.
- **Content cache:** fetched/uploaded packs persist at
  `HA_UPLOAD_DIR/seed-packs/<source_id>/<digest>.zip` (zip, stored). Plan
  and apply read the cache; a missing entry re-fetches **once** and
  re-verifies the digest (`DIGEST_DRIFT` if the upstream moved). Deleting a
  source orphans its cache dir — harmless leftovers, safe to remove
  manually.
- **Tenant identity:** overlay rows derive `uuid5(NS,
  "<tenant>/<type>:<key>")` (`identity_id(..., tenant_id=...)` /
  `scoped_seed_uuid`) — the deterministic-id law extends per scope so an
  overlay may reuse a system `seed_key`. Provider claims and the
  "still provided" checks match on the entity id (which encodes the
  scope), so tenants and the system lane never see each other.
- **Deferred retries are scope-pinned:** the applier retries only refs of
  sources in its own scope (system applies retry system refs; a tenant
  apply its tenant's refs) — retrying cross-scope would seed the wrong
  lane.
- **Adopt commits before re-applying:** the applier plans on its own
  lock-holding connection, so the `user_modified` clears must be committed
  first (the lane already commits per stage).
- **Bundled guards:** the bundled lane cannot be created, synced,
  disabled, retired, approved or revoked (`BUNDLED_IMMUTABLE`) — its
  rollback is the previous bundled digest at boot; its PARTIAL re-apply
  door is the same apply endpoint.
- **Git auth:** the slot's token rides `GIT_CONFIG_*` environment
  (`http.extraHeader`), never argv/URL; git redirects are disabled
  (`http.followRedirects=false`) so the checked host is the connected
  host. The residual (documented in `polite_http`) is git's own DNS
  resolution — the pre-check pins what it can.

## Config knobs (`backend/app/core/config.py`)

`HA_SEED_SOURCE_ALLOWED_HOSTS` (comma list, "" = all public hosts),
`HA_SEED_SOURCE_BLOCK_PRIVATE_RANGES` (default true),
`HA_SEED_SOURCE_ALLOW_PRIVATE` (default false),
`HA_SEED_FETCH_TIMEOUT_SECONDS` (300), `HA_SEED_FETCH_MAX_BYTES` (64 MiB).

## Test map

`tests/test_seed_transport.py` (guard/grammar/archives/pins/secrets),
`tests/test_seed_sources.py` (service lifecycle),
`tests/test_seed_sources_api.py` (HTTP seam + permissions + hidden 404),
`tests/test_seed_tenant_packs.py` (overlays/isolation/edges/deferred),
`tests/test_seed_sync_job.py` (beat entry + cycle), plus the Phase-9B
suites (`test_seed_planner/applier/identity/user_modified`) which now
cover tenant rows too.
