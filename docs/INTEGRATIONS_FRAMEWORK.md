# Integrations Framework Architecture

Health Assistant includes a robust, pluggable Integrations Framework inspired by Home Assistant. It allows users to securely connect third-party platforms (like wearables, labs, or notifications) to their personal profile.

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
5. **Unified Sync Engine**: A background Celery worker automatically runs every 15 minutes, fetching data and mapping it to standard FHIR resources.
6. **Secure Webhook Routing**: The system provides dedicated, tokenless (UUID-based) endpoints for integrations that push data (e.g., Tasker, Notify App) directly into the unified patient record.

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
`celery_app.py` has a periodic beat that triggers `sync_active_integrations` in `tasks.py`. This task loops through all active `UserIntegration` records, loads their specific provider instance, and calls the `pull_data()` method to poll external APIs.

### 5. Webhooks (Push)
When a third party pushes data to the unique `/api/v1/integrations/{domain}/webhook/{integration_id}` URL, the framework intercepts it, loads the specific `UserIntegration` matching that UUID, and hands the payload off to the provider's `handle_webhook()` method. This ensures secure, tokenless routing to the exact integration instance.

### 6. FHIR Normalization
Whether data is pulled or pushed, the framework expects the integration to return standardized FHIR `ObservationCreate` objects. The core engine handles saving these observations to the database and mapping them to the correct semantic Clinical Ontology (Biomarkers).

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