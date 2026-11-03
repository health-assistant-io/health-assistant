# Dev Dummy — Capability Reference

This doc maps each SDK hook to the toggle that enables it in Dev Dummy and the location to read in source. Use it as a cheat sheet when writing a new integration.

## Provider hooks (`BaseHealthProvider`)

| Hook | Toggle / behaviour | Source |
|---|---|---|
| `setup(config)` lifecycle | Always on — logs the received `SystemIntegration.global_config` (one-time per-instance init hook) | top of `provider.py` |
| `pull_data` (abstract) | Always on | `provider.py` §A |
| Exception mapping (`IntegrationAuthError` / `IntegrationRateLimitError`) | `simulate_auth_error`, `simulate_rate_limit` (the latter now carries `retry_after_seconds`) | §B |
| Cursor (`get_sync_cursor` / `set_sync_cursor`) | Always on (delta-sync 5-min increments) | §C |
| Debug logging (`log_debug_payload`) | UI debug toggle (`is_debug_enabled`) | §D |
| Quantitative + categorical observations | `generate_heart_rate` / `generate_blood_pressure` / `generate_weight` / `generate_mood` | §E |
| Sensor malfunction outlier | `simulate_sensor_glitch` (5% chance per sync) | §B |
| Custom UI actions | Always on (three actions: `reset_cursor`, `show_status`, `clear_errors`) | §F |
| Notifications (`supports_notifications` + types + emit + handle_action) | Always on; the elevated-HR notification sets `digest_key` so consecutive syncs collapse into one inbox entry | §G |
| Webhook (`handle_webhook`) + HMAC verification | Active whenever `webhook_secret` is set; verification delegates to `integrations.sdk.webhook_security.verify_hmac_signature` | §H |
| Two-way API (`handle_api_request`) | Always on (`status` / `cursor` / `reset` / `echo`) | §I |
| Chat tools (`supports_tools` / `get_tools`) | `enable_tools` | §J |
| Clinical events (`supports_clinical_events` / `pull_clinical_events`) | `enable_clinical_events` | §K |
| Examinations (`supports_examinations` / `pull_examinations`) | `enable_examinations` | §L |
| Catalog proposals (`supports_catalog_proposals`) | `enable_catalog_proposals` | §M |
| HITL proposals (`supports_hitl_proposals` + `handle_proposal_resolution`) | `enable_hitl_proposals` | §N |
| Documents (`supports_documents` / `pull_documents`) | `enable_documents`; sets `external_id="dev_dummy_demo_report_v1"` so the engine dedups at the DB layer | §O |
| Outbound push (`push_data`) | Always on (logs only) | §P |
| Lifecycle (`close` / `revoke`) | Always on | §P |

## Config-flow hooks (`BaseConfigFlow`)

| Hook | Demonstrated by |
|---|---|
| `get_schema` / `validate_input` | Required — full JSON schema with string / integer / boolean fields and rich descriptions |
| `max_instances_per_user` | Set to `3` |
| `get_secret_fields` | Returns `["webhook_secret"]` |
| `prepare_for_storage` / `prepare_for_read` / `decrypt_for_use` | Inherited defaults — the `webhook_secret` field round-trips through the Fernet cipher |

## What's deliberately NOT demonstrated

- **SMART-on-FHIR OAuth round-trip** (`is_oauth` + `begin_oauth` / `complete_oauth`). Dev Dummy doesn't connect to a real OAuth provider, so faking the round-trip here would be misleading. See `integrations/fhir_server/` for the canonical reference.

## Recently shipped (was on the recommendations list)

The integrations-sdk-improvements pass (`dev/plans/integrations-sdk-improvements-2026-07-21.md`) closed four of the seven original gaps. They're listed here for traceability:

- ✅ **`setup(config)` lifecycle hook now receives real config.** The registry forwards `SystemIntegration.global_config` to `provider.setup(config)` instead of an empty dict. Dev Dummy overrides `setup` to log it.
- ✅ **Backpressure / rate-limit hints back to the engine.** `IntegrationRateLimitError` carries an optional `retry_after_seconds`; the worker writes a Redis cooldown key `sync_cooldown:{integration_id}` (TTL clamped to [60s, 1h]) so the next beat skips the integration.
- ✅ **`pull_documents` dedup at the platform layer.** `DocumentModel` gained `source_integration_id` + `external_id`; partial unique index on `(tenant_id, patient_id, source_integration_id, external_id)`. Dev Dummy sets `external_id` on the demo document.
- ✅ **Notification digesting.** `NotificationSpec.digest_key` + `emit(dedup_key=..., dedup_ttl_seconds=...)` collapse repeated emissions inside a TTL window (default 6h). Dev Dummy tags the elevated-HR notification with `digest_key="dev_dummy:elevated_heart_rate:patient/{uuid}"`.
- ✅ **Webhook HMAC helper extracted to a reusable module** (`integrations.sdk.webhook_security`). Dev Dummy's `_verify_signature` is now a 4-line wrapper around `verify_hmac_signature`.

## Remaining recommendations (future work)

1. **Streaming / chunked observation pull** — `pull_data` always returns a single batch. A `pull_data_stream()` async-generator variant would let integrations page through huge upstream datasets without buffering them in memory.
2. **Webhook signature schemes beyond HMAC-SHA256** — the new `verify_hmac_signature` helper covers GitHub/Slack/Stripe-style prefixed HMAC, but AWS-style `X-Amz-Signature` v4 and asymmetric (JWS) schemes would still need per-provider implementations.
3. **Bulk-apply for catalog proposals** — `supports_catalog_proposals` applies one row per call. A bulk variant could amortise DB round-trips for integrations that propose hundreds of entries per sync.
