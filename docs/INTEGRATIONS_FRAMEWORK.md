# Integrations Framework

Health Assistant includes a pluggable Integrations Framework inspired by Home Assistant. It allows users to securely connect third-party platforms (like wearables, labs, or notifications) to their personal profile.

This document covers the high-level architecture and how the system manages integration lifecycles. 

**For a guide on building new integrations or using the SDK tools, please see the [Integrations SDK & Developer Guide](INTEGRATIONS_SDK.md).**

---

## Architecture Highlights

1. **Pluggable & Modular**: Each integration lives in its own isolated folder under `integrations/`. The core platform discovers them dynamically.
2. **Two-Tiered Enablement**:
   - **System Admins** determine if an integration is globally available to the platform.
   - **Users** connect their individual accounts, provide credentials, and control sync preferences. Users can create **multiple instances** of the same integration (e.g., tracking two separate IoT scales).
3. **Dynamic Schema Setup (Config Flow)**: Instead of hardcoding forms in React, integrations expose a JSON Schema. The frontend dynamically generates the setup UI.
4. **Dynamic Documentation Trees**: The framework supports both simple (`README.md`) and complex, multi-page documentation structures. Complex integrations can define a `docs/docs-tree.json` file, which the backend parses and serves to the frontend to render interactive, nested navigation menus directly within the application.
5. **Unified Sync Engine**: A background Celery beat fires `sync_active_integrations` every 60 seconds. Each `UserIntegration` row has its own `sync_interval` (default 15 min) — the beat checks each integration's `last_synced_at + sync_interval` and skips the ones that aren't due yet. Per-integration dedup is enforced via a Redis lock (`sync_lock:{integration_id}`, `SET NX EX 600`) so overlapping beats **or a manual Sync Now** don't double-write Observations/telemetry (see audit C4). Both the background task and the manual sync endpoint delegate to a shared `IntegrationSyncService.run_sync` pipeline (pull → convert → biomarker-map → telemetry-split → push → log → commit).
6. **Secure Webhook + API Proxy Routing**: The system provides dedicated inbound routes for integrations that push or two-way-sync data:
   - `POST /api/v1/integrations/{domain}/webhook/{integration_id}` — tokenless UUID routing; opt-in HMAC-SHA256 via `user_config.webhook_secret` (`X-Webhook-Signature`). Webhook success/failure now triggers the same notification dispatch as `run_sync`.
   - `ANY /api/v1/integrations/{domain}/api/{integration_id}/{path}` — generic two-way proxy; opt-in HMAC-SHA256 via `user_config.api_secret` (`X-Api-Signature`, optional `X-Api-Timestamp` for replay protection; see audit B8 + [API.md → Integrations & Webhooks](API.md#integrations--webhooks)).
   - `POST /api/v1/integrations/{domain}/notification-action/{integration_id}/{action_id}` — server-side handler for clicked action buttons on integration-authored notifications. Routes to `provider.handle_notification_action` (see [SDK guide](INTEGRATIONS_SDK.md) §3.9).
7. **Tool Exposure Contract**: Integrations can opt-in to expose LangChain tools to the chat assistant by implementing `supports_tools()` + `get_tools()` on their provider. The platform tool aggregator (`integration_tool_aggregator.py`) is domain-agnostic — any integration that opts in is picked up automatically. See the [SDK guide](INTEGRATIONS_SDK.md) §3.6.
8. **Notification Exposure Contract**: Integrations can opt-in to emit rich, event-driven notifications (threshold alerts, HITL prompts, daily summaries, anomaly flags with action buttons + DisplayBlocks) by implementing `supports_notifications()` + `get_notifications()` on their provider. The platform calls these hooks from `run_sync` and the webhook handler after observations are persisted. Providers can additionally declare their notification **kinds** statically via `get_notification_types()` so users can toggle individual kinds on/off without losing the rest — surfaced in a per-integration "Notifications" tab + a central rollup in `/settings/notifications`. Three filter layers compose (per-source ∩ per-channel ∩ per-integration-type), all server-enforced. Mirror of the Tool Exposure pattern. See the [SDK guide](INTEGRATIONS_SDK.md) §3.9.
9. **Secret Encryption**: Integrations declare secret config fields via `get_secret_fields()`; the SDK encrypts them at rest (Fernet) and masks them on read. No per-domain code in the endpoint. See the [SDK guide](INTEGRATIONS_SDK.md) §3.7.

---

## How It Works Under the Hood

### 1. Startup & Discovery
FastAPI initializes the `IntegrationRegistry` during startup. It scans the `integrations/` folders, reads each `manifest.json`, and queries the database (`system_integrations` table) to see which discovered domains are marked `is_enabled=True`. Only enabled integrations have their Python classes loaded into memory.

### 2. User Configuration (Config Flow)
When a user clicks "Add Integration", the frontend requests `/api/v1/integrations/{domain}/config-flow`. The backend returns the JSON schema defined by the integration, and the frontend renders a dynamic form. 

### 3. Persistence & Security
The user's form inputs (and potentially OAuth tokens) are securely saved in the `user_integrations` table under the `user_config` JSON column. A unique UUID is assigned to this connection.

Because the system allows multiple instances of the same integration (e.g., configuring two different Webhook endpoints for two different phones), all instance management, deletions, syncs, and custom actions are routed via this `integration_id` (UUID) rather than the generic `domain`.

### 4. Background Syncing (Polling)
`celery_app.py` has a periodic beat (every 60 s) that triggers `sync_active_integrations` in `tasks.py`. This task loops through all active `UserIntegration` records, loads their specific provider instance, and delegates to `IntegrationSyncService.run_sync` — the shared pull → convert → biomarker-map → telemetry-split → push → log → commit pipeline. Each integration's per-row `sync_interval` (default 15 min) gates whether the beat actually syncs. The manual Sync Now endpoint (`POST /api/v1/integrations/{id}/sync`) also delegates to `run_sync`.

**Per-integration dedup lock (audit C4):** `run_sync` acquires a Redis lock keyed `sync_lock:{integration_id}` via `SET NX EX 600` (sync hard timeout). If the lock can't be acquired (another worker or a manual sync is already running this integration), the sync is SKIPPED — no duplicate writes. The lock is released in a `finally` block; if the process crashes mid-sync, the 600 s TTL expires it. Redis-down degrades gracefully to the legacy always-sync mode but logs a warning.

### 5. Webhooks (Push)
When a third party pushes data to the unique `/api/v1/integrations/{domain}/webhook/{integration_id}` URL, the framework intercepts it, loads the specific `UserIntegration` matching that UUID, and hands the payload off to the provider's `handle_webhook()` method. This ensures secure, tokenless routing to the exact integration instance.

Webhooks are now wired into the same notification dispatch path as `run_sync` — successful pushes fire both the baseline "synced N records" notification and any provider-authored notifications (when the provider opts in via `supports_notifications`, see [INTEGRATIONS_SDK.md §3.9](INTEGRATIONS_SDK.md#39-notifications-event-driven-rich-actionable)). Failures fire the same auth/data-failure escalation path (owner + tenant admins). Previously webhooks failed silently from the user's POV.

### 6. FHIR Normalization & Telemetry Split
Whether data is pulled or pushed, the framework expects the integration to return standardized FHIR `ObservationCreate` objects. The core engine handles saving these observations to the database and mapping them to the correct semantic Clinical Ontology (Biomarkers).

**Frequency-based routing (FHIR vs TimescaleDB):** observations linked to a `BiomarkerDefinition` flagged `is_telemetry=True` are routed to the `telemetry_data` TimescaleDB hypertable (heart rate, steps, CGM, etc.); everything else lands in the standard `fhir_observations` table. This routing is the telemetry-split step inside `IntegrationSyncService.run_sync` (`apply_telemetry_split`), which is called by the background task and the manual sync endpoint (both via `run_sync`). The webhook handler inlines the same logic — the only persistence path not currently routed through `run_sync`, but the post-sync notification dispatch is now shared via `post_sync_notifications` (see `integration_sync_service.py`).

The helper stamps the `performer` reference (`Integration/<id>`) on FHIR rows that don't already have one, and routes the long-tail of telemetry slugs (anything without a dedicated `heart_rate`/`steps`/`calories` column) into the row's JSONB `data` payload alongside its unit.

### 7. Interactive Documentation Rendering
When a user views an integration's details in the UI, the frontend requests `/api/v1/integrations/{domain}/documentation`. The backend checks the integration's root folder for a `docs/docs-tree.json`. If found, it parses the JSON tree and returns it alongside the requested markdown file. The frontend uses this metadata to dynamically render a sidebar navigation menu, allowing users to browse complex SDK references or setup guides without leaving the platform.

---

## Enabling Integrations Globally

By default, newly written code for an integration is invisible to end-users. A system administrator must enable it globally. This can be done via the Admin UI at `/admin/system/integrations`, or directly via SQL:

```sql
INSERT INTO system_integrations (domain, is_enabled) 
VALUES ('your_integration_domain', true)
ON CONFLICT (domain) DO UPDATE SET is_enabled = true;
```