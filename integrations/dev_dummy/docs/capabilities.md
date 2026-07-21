# Dev Dummy — Capability Reference

This doc maps each SDK hook to the toggle that enables it in Dev Dummy and the location to read in source. Use it as a cheat sheet when writing a new integration.

## Provider hooks (`BaseHealthProvider`)

| Hook | Toggle / behaviour | Source |
|---|---|---|
| `pull_data` (abstract) | Always on | `provider.py` §A |
| Exception mapping (`IntegrationAuthError` / `IntegrationRateLimitError`) | `simulate_auth_error`, `simulate_rate_limit` | §B |
| Cursor (`get_sync_cursor` / `set_sync_cursor`) | Always on (delta-sync 5-min increments) | §C |
| Debug logging (`log_debug_payload`) | UI debug toggle (`is_debug_enabled`) | §D |
| Quantitative + categorical observations | `generate_heart_rate` / `generate_blood_pressure` / `generate_weight` / `generate_mood` | §E |
| Sensor malfunction outlier | `simulate_sensor_glitch` (5% chance per sync) | §B |
| Custom UI actions | Always on (three actions: `reset_cursor`, `show_status`, `clear_errors`) | §F |
| Notifications (`supports_notifications` + types + emit + handle_action) | Always on | §G |
| Webhook (`handle_webhook`) + HMAC verification | Active whenever `webhook_secret` is set | §H |
| Two-way API (`handle_api_request`) | Always on (`status` / `cursor` / `reset` / `echo`) | §I |
| Chat tools (`supports_tools` / `get_tools`) | `enable_tools` | §J |
| Clinical events (`supports_clinical_events` / `pull_clinical_events`) | `enable_clinical_events` | §K |
| Examinations (`supports_examinations` / `pull_examinations`) | `enable_examinations` | §L |
| Catalog proposals (`supports_catalog_proposals`) | `enable_catalog_proposals` | §M |
| HITL proposals (`supports_hitl_proposals` + `handle_proposal_resolution`) | `enable_hitl_proposals` | §N |
| Documents (`supports_documents` / `pull_documents`) | `enable_documents` | §O |
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

## Capabilities Dev Dummy doesn't yet cover (recommendations)

These are SDK-level recommendations for follow-up work — none of them are blocking for Dev Dummy's role as a reference:

1. **`setup(config)` lifecycle hook** — currently a no-op everywhere; could demonstrate one-time per-instance initialisation (e.g. validating account entitlements).
2. **Streaming / chunked observation pull** — `pull_data` always returns a single batch. A `pull_data_stream()` async-generator variant would let integrations page through huge upstream datasets without buffering them in memory.
3. **Backpressure / rate-limit hints back to the engine** — providers raise `IntegrationRateLimitError` but can't tell the engine *when* to retry. A `retry_after_seconds` field on the exception would let the worker honour `Retry-After`.
4. **Webhook signature schemes beyond HMAC-SHA256** — `handle_webhook` receives the raw `Request`, so providers can implement any scheme today, but a built-in helper (e.g. for AWS-style `X-Amz-Signature` v4) would save repetition.
5. **`pull_documents` dedup at the platform layer** — currently the provider owns document idempotency via cursors. A `source_integration_id` + `external_id` pair on `DocumentModel` (mirroring clinical events + examinations) would close the gap.
6. **Bulk-apply for catalog proposals** — `supports_catalog_proposals` applies one row per call. A bulk variant could amortise DB round-trips for integrations that propose hundreds of entries per sync.
7. **Notification digesting** — providers can emit one notification per reading today; a hint like `digest_key="heart_rate"` would let the platform collapse bursts into a single inbox entry.
