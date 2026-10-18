# Developer diagnostics & FAQ

The user-facing half lives at
[Integration troubleshooting](../../user/integration-troubleshooting.md);
this page is the author diagnostics — every failure mode the tests pin
plus the FAQ. Symptom → cause → fix.

## Pull hooks and the sync engine

| Symptom | Cause | Fix |
|---|---|---|
| Instance flips to `ERROR` mid-sync | `pull_data` (or a helper) raised typed `auth` — 401/403 upstream or an exhausted token refresh | Reconnect (re-run the OAuth round-trip); the code is surfaced on the sync-log row |
| Sync "skipped" right after a 429 | `rate-limited` exhaustion wrote `sync_cooldown:{iid}` (TTL clamped 60 s – 1 h) | Wait out the cooldown or clean the Redis key; mind the surfaced `retry_after_ms` |
| Sync returns 409 on Sync Now | another sync holds `sync_lock:{iid}` (the 60 s beat beat you to it) | Retry in a moment |
| `"status": "partial"` + `dropped_invalid > 0` | some observations failed the write-time FHIR validation (bad shape, `value_string` on a STATE biomarker, unknown units) | Fix the malformed payloads; the counts (`pulled` vs `metrics_synced`) localize the damage |
| Observations "missing" but metrics_synced counts them | telemetry routing — `is_telemetry` biomarkers go to `telemetry_data`, not `/observations` | Check `GET /api/v1/telemetry/data` (or the FHIR `Observation` table for the rest) |
| Cursor isn't advancing between syncs | provider forgot `set_sync_cursor`, or a hook raised before the stamp | `set_sync_cursor` after persisting; the manual "Reset Sync Cursor" action clears stuck cursors |
| Sync stops writing after a deploy | the manifest broke registration (envelope rejected) — the extension self-disabled with a logged reason | Backend logs carry `Failed to load manifest for {item} …` — fix the envelope |
| Engine never calls your new hook | the capability/data class isn't declared in the manifest | Declare it — the engine asked the registry, not the provider |

Pinned by `tests/test_dev_dummy_provider.py` (auth/rate-limit raises, the
cursor round-trip, per-hook toggles) and
`tests/test_integration_sync_telemetry_split.py` (the split),
`tests/test_integration_registry.py` (the gates).

## The HMAC gate and machine secrets

| Symptom | Cause | Fix |
|---|---|---|
| Webhook/API route answers 401 "…carries no X-Api-Signature header" | the instance has a machine secret and you sent unsigned traffic | Sign (SDK constructor) or hand-roll with `sign_request` / `verify_*` helpers |
| 401 "Invalid or expired request signature" | body bytes mismatch (re-serialization), wrong path casing/slash, or clock skew beyond ±5 min | Sign the exact bytes; the path is the part after the integration id with a leading `/` (query included); NTP-sync the client |
| The secret never decrypts after a rotation | the ciphertext's `_kid` matches a dropped `HA_DATA_KEY_PREVIOUS` key | Restore the old key to the ring (see the kit's at-rest runbook); never drop before the `_kid` census |
| Saving config fails fast with 400 on secret fields | `HA_DATA_KEY` unset | Set it (the endpoint refuses to store plaintext) |

Pinned by `tests/test_integration_api_proxy_hmac.py`,
`tests/test_webhook_security_path_normalization.py`,
`tests/test_secret_encryption.py` (cipher),
`tests/test_dev_dummy_provider.py::test_webhook_*` (provider-side verify).

## Bridge client SDKs

| Symptom | Cause | Fix |
|---|---|---|
| Python `AttributeError: 'dict' object has no attribute 'model_dump_json'` | you passed a plain dict where the SDK expects the typed `SyncPayload` / you run the SDK under pydantic v1 | Construct `SyncPayload(...)` / `ClientRecord(...)`; run against the SDK's pydantic v2 dependency |
| SDK version-mismatch warning on `/status` | the server's `latest_sdks` advertises a newer client | Update the package; advisory — never blocks |
| `BridgeException` `code=validation` on `/changes` | a `types` CSV entry is not one of the six | Use the closed vocabulary |
| 429 on `/ai/*` | the shared per-integration AI budget (30/5 min) | Honor `Retry-After`; the SDKs surface it, never auto-retry AI turn POSTs |
| SSE stream aborted mid-turn | transport failure inside a streaming chat turn (no in-band resume) | Start a NEW turn (re-send the message, or `.../resume` for a HITL continuation) — replaying duplicates the turn |

Pinned by the SDK suites themselves: python —
`tests/test_bridge_sdk_signing.py` (sign → server-verify round-trip),
`tests/test_bridge_sdk_sync_client.py` (retries, export parity); TS —
`ts-sdk/test/*` (`npm test` in that dir); Kotlin —
`SchemaConformanceTest.kt`, `BridgeClientTest.kt`, `SigningParityTest.kt`
(`gradle test`, needs JAVA_HOME 25 + ANDROID_HOME).

## The `/map` AI loop

| Symptom | Cause | Fix |
|---|---|---|
| "`AI mapping service is currently unavailable`" (400) | the configured AI/NLP task assignment is unreachable (no provider, quota, wrong assignment) | Fix the AI settings; a cached `biomarker_id` still lets `/sync` land records |
| Duplicate biomarkers after pushes | your client pushed unmapped names and let the engine's heuristics create definitions | Run `/map` first; push the resolved `biomarker_id`; a re-sync carrying the id links cleanly |

## FAQ

**Can two extensions register the same domain?** No — the registry keys
by envelope id; the later directory would collide at load. Ids are
family-unique (reserved prefixes rejected).

**Do I need a DB migration for a new extension?** No — instances point
at the registry; no new tables. Only platform-machinery changes (like the
document dedup columns) are migrations, and those belong to the engine.

**Where do per-user errors surface?** The sync log rows
(`IntegrationSyncLog`) carry the typed code; the instance's `sync_status`
field summarizes (`ok` / `error` / `consecutive_failures`); the UI Debug
Console shows raw payloads when debug mode is on.

**Can an extension write clinical records directly to the DB?** It can —
but it must go through the canonical services (`create_event`,
`ingest_document_bytes`, …) with the actor resolved via
`resolve_integration_actor` so tenant scope, RBAC, audit and dedup all
hold. Raw table writes bypass audit and dedup — treat them as a bug.

**Why did my observation go to "the wrong table"?** It didn't — the
telemetry split is the engine's by-design routing on
`BiomarkerDefinition.is_telemetry`. Dashboard/trend reads merge both
sources, so clients never branch.
