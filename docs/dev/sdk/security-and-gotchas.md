# Security & Gotchas (authoring)

The trust model as the extension author and operator experience it. The
user-facing half — what you grant when you connect an integration — lives
at [What you grant when you connect](../../user/integration-security.md).

## The trust posture on this host

| Tier | Runs | On health | Disclosed residual |
|---|---|---|---|
| T0 declarative | nothing — validated data only | per-extension `docs/` trees and i18n strings; no executable T0 surface is consumed by endpoints | rendering is host-side against a closed vocabulary |
| T1 bundled | in-process, reviewed with the product | **the whole authoring surface** (`builtin/`) — the image ships the code | the review *is* the control |
| T2 sandbox | subprocess + capability RPC | not hosted on health (git import deferred) | — |
| T3 external | separate process the host calls | not hosted as importable extensions on health; the *remote servers* MCP Client points at are the T3-shaped surface | the remote's sockets/tools are its own; only tool results — capped and sanitized — enter the model context |

Two load-bearing rules:

- **Imported third-party code never runs in-process on health** — there
  is no import path; health's extension surface is reviewed product code.
  External parties connect over the wire (bridge/webhook) instead.
- **What an extension may do is declared in its manifest and gated at
  registration** — an undeclared capability is never called, and the
  tool risk classes (`read-only` / `state-changing` / `destructive`) are
  schema-required, cross-checked against the implementation at
  registration.

## The secrets law (x-secret)

Two families, one rule — **nothing secret is ever stored in plaintext**:

- **Declared credential slots** (`configSchema` fields marked
  `"x-secret": true`, cross-checked with the config flow's
  `get_secret_fields()` at registration — disagreement raises):
  encrypted with `HA_DATA_KEY` (Fernet) on save, masked `"***"` on read,
  decrypted only for use. A plaintext secret in stored config is a
  registration-time failure, never stored.
- **Provisioned machine secrets** (`webhook_secret` / `api_secret`):
  auto-minted per instance at creation (`token_urlsafe(32)`), sealed with
  the **instance id as context binding** (a ciphertext can't be
  cut-and-pasted to another row's config in multi-tenant JSONB), shown
  **once** in the config-flow response.

Operations on the machine secrets:

- The masked UI shows `"***"`; the raw value lives only in the sealed
  wrapper.
- Rotation: `POST /integrations/instance/{id}/rotate-secret`
  (owner-only, `field=api_secret`) mints a fresh one and shows it once;
  the walk-in path is disable + re-create.
- `HA_DATA_KEY` is set per deployment; key rotation rides the
  `HA_DATA_KEY_PREVIOUS` ring (runbook in the auth kit's `docs/atrest.md`,
  data-loss gate: never drop an old key before a `_kid` census reads zero).

## Politeness, SSRF and the transport runtime

Outbound HTTP rides the family Connector Runtime
(`app/extensions/runtime/http.py` over the vendored
`app/extensions/polite_http.py`) — one pool per provider
(`self._http_client`), never sockets of your own:

- **SSRF guard** — resolve-then-connect with the checked address pinned
  into the request (DNS-rebind TOCTOU mitigation); cloud-metadata
  (`169.254.169.254`), loopback, link-local, private and reserved ranges
  blocked; numeric-literal IPv4 obfuscations coerced and checked;
  **re-validated on every redirect hop** (stricter than the pre-charter
  guard).
- **Retries** — full-jitter exponential backoff on 5xx/network timeouts,
  429 honored explicitly with RFC 7231 `Retry-After` in both
  integer-seconds and HTTP-date forms (surfaced as `retry_after_ms` on
  the typed `rate-limited` error); 401/403 fail immediately as `auth`;
  every other status is returned as a normal response to the caller.
- **Escape hatches are Settings-routed, never environment reads by the
  runtime**: `INTEGRATION_ALLOWED_HOSTS` (a trusted LAN target),
  `INTEGRATION_BLOCK_PRIVATE_RANGES=false`,
  `INTEGRATION_ALLOW_PRIVATE`.
- The hostile-wall fixtures
  (`contracts/polite_http.fixtures.json`, run by
  `backend/tests/test_extensions_contract.py`) pin the guard's behavior;
  don't bypass the injected client with raw `httpx.AsyncClient()`.
- OAuth discovery/token calls pass the same `assert_safe_url` chokepoint.

## The inbound chokepoints

Two machine routes exist per capable instance, both **fail-closed**:

| Route | Gate |
|---|---|
| `POST /integrations/{domain}/webhook/{integration_id}` | HMAC mandatory (`webhook_secret`); verify via `get_signature_header` + one of the `verify_*` helpers; body ≤ 40 MiB; per-provider header conventions honored |
| `[ANY] /integrations/{domain}/api/{integration_id}/{path}` | HMAC mandatory (`api_secret`) over `METHOD\n<path>[?query]\n<timestamp>\n<body>`; `X-Api-Timestamp` required (missing → 401); ±5-min replay window; query params are inside the MAC |

The one unsigned exception: `GET /status` without a signature answers the
pre-pairing probe `{status, server_time}` only (least-privilege liveness).

Chokepoint law for authors:

- never "temporarily" accept unsigned traffic — a secret-less instance is
  rejected 401-plain (the routes refuse the instance, not fall back);
- validate *inside* the provider only stuff the platform route can't know
  (per-domain header names, parser schemas) — the algorithm itself comes
  from the shared module;
- every inbound write flows through `run_sync`-grade FHIR validation
  (the write-time gate inside `map_observations_to_biomarkers`), so
  enchanted payloads die before the catalog; let them — don't pre-shape
  data to smuggle it past validation.

## The chat-tools funnel

Every tool contribution declares one risk class in the manifest `tools`
list (schema-required per D20 when `chat-tools` is declared).
`read-only` auto-runs; heavier classes ride the approval ladder (chat
cards / enablement), and the model never authors the subsystem:
registration, enablement and imports are admin/user acts, never chat
outcomes.

## Gotchas authors actually hit

- **Manifest rejected at registration** — envelope violations throw
  there, never mid-run. `net` without `domains` is the classic.
- **The config flow / manifest secret drift** — a field encrypted by the
  flow but missing `x-secret` in the manifest fails registration
  (`_gate_secrets`).
- **A JSONB mutation you didn't flag** — user_config/cursor writes go
  through the base's helpers; mutating `integration.user_config` keys
  directly needs `flag_modified(obj, "field")` before commit (repo
  convention law).
- **Redirects re-validated** — an upstream that hops through an
  undeclared CDN host fails `net-denied`; add the host to
  `INTEGRATION_ALLOWED_HOSTS` or the declared domains.
- **The cooldown key is Redis-backed** — with Redis down the sync skips
  the cooldown and degrades to the per-instance `sync_interval` throttle.
- **Categorical vs quantity are exclusive** — the last `set_value*` wins;
  a `value_string` on a STATE biomarker dies in the hard validator.
- **Notifications must never raise** — swallow and return `[]`; one bad
  spec kills a sync otherwise.
- **Per-sync caps are real** — 50 catalog / 20 HITL proposals / 20 docs /
  50 MiB docs bytes; cheap batching beats cap-runs.
