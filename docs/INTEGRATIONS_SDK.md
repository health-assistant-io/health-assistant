# Integrations SDK & Developer Guide

Health Assistant provides an **Integrations SDK** (`integrations.sdk`) designed to make building new third-party integrations simple and secure.

This guide walks you through creating an integration, utilizing SDK tools, and handling both Polling (pull) and Webhook (push) data flows.

For an overview of the system architecture, see the [Integrations Framework](INTEGRATIONS_FRAMEWORK.md) document.

---

## 1. Directory Structure

To create a new integration, create a folder under `integrations/{domain}`. The domain should be a lowercase string (e.g., `smartwatch_sync`, `custom_webhook`).

Your integration must include three core files:
1. `manifest.json`
2. `config_flow.py`
3. `provider.py`

### Documentation Standard

For simple integrations, providing a single `README.md` at the integration root is sufficient.

For complex integrations (like SDK bridges or external APIs), create a `docs/` folder containing a `docs-tree.json` file. This defines a multi-page documentation structure that the frontend can parse to generate sub-navigation menus.

#### Example `docs-tree.json` Format

The `docs-tree.json` must be an array of categories, where each category contains an array of document items:

```json
[
  {
    "category": "Introduction",
    "items": [
      { "id": "overview", "file": "overview.md", "title": "Overview" },
      { "id": "setup", "file": "setup-guide.md", "title": "Setup & Configuration" }
    ]
  },
  {
    "category": "Advanced",
    "items": [
      { "id": "api-reference", "file": "api.md", "title": "API Reference" }
    ]
  }
]
```
- `category`: The header for the menu group.
- `id`: A unique string ID for the route/page.
- `file`: The relative path to the Markdown file inside the `docs/` folder.
- `title`: The display name of the document in the menu.

### Step 1: `manifest.json`
Defines the metadata of your integration.

```json
{
  "domain": "smartwatch_sync",
  "name": "Acme Smartwatch Sync",
  "version": "1.0.0",
  "integration_type": ["push"], 
  "description": "Receive push notifications directly from compatible smartwatches.",
  "author": "Community",
  "access_type": "hybrid",
  "categories": ["Wearables", "Notifications"],
  "icon": "Watch",
  "dependencies": []
}
```
*Note: `integration_type` can be `["pull"]` for polling APIs or `["push"]` for inbound webhooks.*
*Note: `access_type` must be exactly one of: `"local"`, `"cloud"`, or `"hybrid"`.*

### Step 2: `config_flow.py`
Defines the dynamic UI needed to set up this integration. Must inherit from `BaseConfigFlow`.

```python
from integrations.sdk import BaseConfigFlow

class NotifyConfigFlow(BaseConfigFlow):
    domain = "notify"

    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Configure Notify",
            "description": "Select the metrics you want to track.",
            "data_schema": {
                "type": "object",
                "properties": {
                    "track_heart_rate": {"type": "boolean", "default": True},
                    "track_steps": {"type": "boolean", "default": True}
                }
            }
        }

    async def validate_input(self, user_input: dict) -> dict:
        # Perform any validation. Raise ValueError if invalid.
        return user_input
```

#### Capability hooks (optional, all have safe defaults)

The SDK base class provides opt-in hooks so integrations can declare capabilities without the platform endpoint knowing about specific domains:

- **`max_instances_per_user: Optional[int]`** — per-user instance cap. `None` = unlimited (default). The endpoint enforces this generically on create.
- **`get_secret_fields() -> List[str]`** — field names that are secret. The platform encrypts them before storage (Fernet) and masks them as `"***"` on read. Default: `[]` (no secrets, no key required).

```python
class McpClientConfigFlow(BaseConfigFlow):
    domain = "mcp_client"
    max_instances_per_user = 5  # read from settings in practice

    def get_secret_fields(self):
        return ["env", "headers", "auth_token"]
```

See §3.7 for secret encryption details and §3.5 (Returning structured results) for display blocks.

### Step 3: `provider.py`
The core logic. Must inherit from `BaseHealthProvider`.

```python
from typing import List, Any
from integrations.sdk import BaseHealthProvider
from app.schemas.fhir.observation import ObservationCreate
from app.models.user_integration import UserIntegration

class NotifyProvider(BaseHealthProvider):
    domain = "notify"
    
    # ... See below for implementation details (Pull vs Push) ...
```

---

## 2. Handling Data (Pull vs. Push)

### Option A: Polling (Pull Data)
For REST APIs that require periodic polling (e.g., Cloud Health API), implement the `pull_data` method. A background Celery beat fires every 60 seconds and calls `sync_active_integrations`; each `UserIntegration` row's own `sync_interval` (default 15 min) gates whether the beat actually invokes `pull_data` for that integration on a given tick.

```python
    async def pull_data(self, integration: UserIntegration) -> List[ObservationCreate]:
        last_sync = self.get_sync_cursor(integration, "last_date", default="2024-01-01")
        
        # Use the shared SDK HTTP helper (token-aware, full-jitter retry/backoff).
        from integrations.sdk import http_request
        data = await http_request(
            self._http_client,
            "GET",
            f"https://api.example.com/data?since={last_sync}",
        )
        
        observations = []
        builder = self.create_observation_builder(integration)
        
        # ... parse data and build observations ...
        
        self.set_sync_cursor(integration, "last_date", "2024-02-01")
        return observations
```

### Option B: Webhooks (Push Data)
For platforms that push data to Health Assistant (e.g., Notify App, Tasker), implement the `handle_webhook` method.

The system automatically provisions a unique endpoint for each connected user:
`POST /api/v1/integrations/{domain}/webhook/{integration_id}`

```python
    async def handle_webhook(self, integration: UserIntegration, payload: Any, request: Any = None) -> List[ObservationCreate]:
        
        await self.log_debug_payload(integration, "Incoming Webhook", payload)
        
        observations = []
        builder = self.create_observation_builder(integration)
        config = integration.user_config or {}
        
        if payload.get("type") == "heart_rate" and config.get("track_heart_rate"):
            obs = (
                builder
                .set_biomarker("8867-4", "Heart rate")
                .set_value(float(payload["value"]), "bpm", "{beats}/min")
                .build()
            )
            observations.append(obs)

        return observations
```

### Option C: Two-Way API Proxies (Advanced)
For integrations needing to act as a bridge for headless clients (like a Browser Extension or Mobile App), you can expose a full two-way REST API rather than just a one-way webhook.

The system provisions a wildcard route for your integration:
`[GET/POST/PUT] /api/v1/integrations/{domain}/api/{integration_id}/{path}`

Implement the `handle_api_request` method:

```python
    async def handle_api_request(self, integration: UserIntegration, path: str, method: str, request: Any) -> Dict[str, Any]:
        
        if path == "status" and method == "GET":
            return {
                "status": "connected",
                "cursor": self.get_sync_cursor(integration, "last_timestamp")
            }
            
        if path == "sync" and method == "POST":
            payload = await request.json()
            # ... process payload ...
            return {"success": True, "message": "Synced"}
            
        raise NotImplementedError("Path not supported")
```

---

## 3. Advanced SDK Features

### 3.1 HTTP Client & Auto-Retries
Providers share a pooled `httpx.AsyncClient` (`self._http_client`). For robust HTTP, call the SDK's shared helpers from `integrations.sdk.http` (`http_request`) or `integrations.sdk.fhir` (`fhir_search`, `fhir_create`, `fhir_conditional_update`). All of them delegate to a single `_retry_request` primitive that handles:
- **Connection pooling** to preserve sockets.
- **Full-jitter exponential backoff** (AWS-recommended) for 5xx server errors and network timeouts — every client picks an independent random wait, so retry waves spread out instead of stampeding the server on each tick.
- **Rate Limit awareness**, explicitly pausing if a `429 Too Many Requests` is hit (respecting `Retry-After` headers when present, otherwise full-jitter backoff).

```python
from integrations.sdk import http_request

data = await http_request(
    self._http_client, "GET", url, access_token=token,
)
```

### 3.2 Cursor / State Management (Delta Syncs)
Save your position (cursor) between syncs to avoid pulling duplicate data.
```python
# Retrieve last state
last_timestamp = self.get_sync_cursor(integration, "last_timestamp", default=0)

# Save new state
self.set_sync_cursor(integration, "last_timestamp", new_timestamp)
```

### 3.3 Managed Exceptions & UI Feedback
Throw specific exceptions from `integrations.sdk.exceptions` to instruct the core engine.
- `IntegrationAuthError`: Pauses the integration (changes status to `ERROR`) and alerts the user in the UI. Automatically raised by the SDK HTTP helpers (`http_request`, `fhir_search`, `fhir_create`, `fhir_conditional_update`, `_request_json`) on 401/403 responses.
- `IntegrationRateLimitError`: Skips the current sync cycle gracefully. Automatically raised if 429 retries are exhausted.

### 3.4 Payload Debugging
When deciphering undocumented third-party APIs or receiving webhooks, you can securely dump raw payloads into a dedicated debug database. These logs can then be viewed directly in the Integration's Debug Console UI.

The platform automatically provides a **"Debug Mode: OFF/ON"** button for every connected integration in the frontend UI. You do not need to add any settings to your `config_flow.py`!

To utilize this in your provider code, simply call:

```python
# Automatically checks if integration.is_debug_enabled before saving
await self.log_debug_payload(integration, "API Response", raw_json_data)
```

### 3.5 Custom Actions (Services)
Integrations can expose custom interactive buttons to the frontend UI (e.g., "Reset Sync Cursor", "Trigger Device Ping").

```python
    def get_custom_actions(self) -> List[Dict[str, str]]:
        return [
            {"id": "reset_cursor", "label": "Reset Sync Cursor", "style": "warning"}
        ]

    async def execute_custom_action(self, integration: UserIntegration, action_id: str, **kwargs) -> Dict[str, Any]:
        if action_id == "reset_cursor":
            self.set_sync_cursor(integration, "last_date", None)
            return {"message": "Cursor reset successfully!"} # Returns a Toast to the UI

        raise NotImplementedError(f"Action not supported.")
```

#### Returning structured results (display blocks)

Custom actions can optionally return a `results` array of typed display blocks. The frontend renders them in a modal (`ActionResultModal`). Backwards compatible: `{"message": "..."}` alone still shows just a toast.

```python
from integrations.sdk import kv_block, list_block, table_block, code_block, action_result

async def execute_custom_action(self, integration, action_id, **kwargs):
    if action_id == "list_tools":
        tools = await self._list_tools(integration)
        return action_result(
            message=f"Discovered {len(tools)} tool(s).",
            results=[
                kv_block("Summary", {"Total": len(tools), "Transport": "stdio"}),
                table_block("Tools", ["Name", "Description"],
                            [[t.name, t.description] for t in tools]),
            ],
        )
```

Available block builders (from `integrations.sdk`): `kv_block`, `list_block`, `table_block`, `json_block`, `text_block`, `code_block`, `action_result`. See `integrations/sdk/display.py` for the full API.

### 3.6 Tool Exposure (for the Chat Assistant)

Integrations can expose tools to the chat assistant by implementing two methods on the provider:

```python
class MyProvider(BaseHealthProvider):
    domain = "my_integration"

    def supports_tools(self) -> bool:
        return True

    async def get_tools(self, integration: UserIntegration) -> List[Any]:
        # Return LangChain StructuredTool / @tool objects.
        # Swallow per-instance errors and return [] on failure.
        return [my_tool_1, my_tool_2]
```

The platform tool aggregator (`app/ai/tools/aggregator.py`) iterates all active integrations, calls `supports_tools()` on each, and merges `get_tools()` results with the built-in `app/ai/tools` (assembled by `get_tools`) before `llm.bind_tools()`. **No platform code references any specific integration domain** — any integration that opts in is picked up automatically.

`INTEGRATION_MAX_TOOLS_PER_SESSION` (default 20) bounds the total number of integration tools per chat turn.

Reference: `integrations/mcp_client/provider.py` is the first integration to use this contract.

### 3.7 Secret Encryption (at rest)

Integrations with secret config fields (API keys, tokens, env vars) declare them via `get_secret_fields()`. The SDK handles the rest:

- **On save:** `submit_config_flow` calls `config_flow.prepare_for_storage()` → encrypts secret fields with Fernet (`INTEGRATION_SECRET_KEY`).
- **On read:** `get_integration_details` calls `config_flow.prepare_for_read()` → masks secret fields as `"***"`.
- **On use:** the provider calls `config_flow.decrypt_for_use()` or `decrypt_fields()` to get plaintext.

No per-domain code in the endpoint. Set `INTEGRATION_SECRET_KEY` in `.env` (Fernet key, base64 32 bytes):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If the key is missing, saving config with secret fields fails fast with a 400 — no plaintext secrets are ever stored. Integrations with no secret fields are unaffected.

### 3.8 OAuth / SMART-on-FHIR (Cloud Integrations)

For integrations that connect via an OAuth2 Authorization Code flow (FHIR servers,
Fitbit, Withings, Apple Health cloud, etc.), the SDK ships a reusable auth module
in `integrations/sdk/auth.py`. It provides:

- **PKCE** (`generate_pkce`, `generate_state`) — stdlib-based, no extra deps.
- **SMART discovery** (`discover_smart`) — reads `/.well-known/smart-configuration`.
- **Dynamic Client Registration** (`register_client`, RFC 7591) — the user enters
  *only the server URL*; the SDK auto-registers a public client (no `client_id`
  pasted by the user).
- **Token exchange + refresh** (`exchange_code`, `refresh_token`).
- **`OAuthTokenStore`** — persists tokens in `user_config["_oauth"]` with
  `access_token` / `refresh_token` / `client_secret` Fernet-encrypted; stores
  connection metadata (`token_endpoint`, `revocation_endpoint`, `client_id`,
  `scope`, `patient`, `fhir_base_url`, `expires_at`) so refresh + revoke work later.
- **`OAuthStateStore`** — Redis-backed short-lived `state` + PKCE verifier
  (reuses `app/core/redis.py`), one-shot **atomic** consume via `GETDEL`
  (Redis ≥ 6.2) — no TOCTOU window for duplicate callbacks.
- **`SmartOAuth`** — composes discovery → DCR → authorize → exchange, with
  refresh-on-use (`get_live_token`), force-refresh on a 401 race (`force_refresh`),
  and best-effort token revocation on disconnect (`revoke`, RFC 7009). When DCR
  is advertised but returns no `client_id`, raises an actionable
  `IntegrationAuthError` instead of producing a broken authorize URL.

The connect flow is **decoupled from the config modal** — config creates a
`PENDING` instance, then a separate Authorize action runs the OAuth round-trip:

1. Config flow sets `is_oauth = True`. `submit_config_flow` then creates the
   instance with status `PENDING` (not `ACTIVE`) **only when its `auth_mode` is
   `smart`** — a tokenless instance (`auth_mode == "none"`) skips OAuth and goes
   straight to `ACTIVE`. `auth_mode` is a per-instance config field the
   integration declares (e.g. `smart` for hospitals/SMART sandbox, `none` for a
   local/open FHIR server like vanilla HAPI, which has no SMART module).
2. The provider implements `begin_oauth(integration, redirect_uri, extra_state=)`
   and `complete_oauth(integration, pending, code)` (default raise
   `NotImplementedError`). For SMART, delegate to `SmartOAuth`.
3. The platform exposes two generic routes (no per-domain code):
   - `POST /{domain}/oauth/start` → discover + DCR + authorize URL; stores
     `state` → `{integration_id, user_id, …PKCE…}` in Redis.
   - `GET /{domain}/oauth/callback?state=&code=` (unauthenticated, secured by the
     one-shot `state`) → exchange + encrypt tokens → flip status `ACTIVE` → 302
     to the SPA `/integrations/{domain}/connected` landing.
4. The frontend shows an "Authorize" banner on `PENDING` instances and a toast
   landing page on return.

For **inbound FHIR data** (pull), use `sdk/fhir.py` — `fhir_search(http_client,
base_url, resource_type, params, *, access_token=None)` (tokenless when
`access_token` is `None`) and `fhir_observation_to_create(fhir_obs, tenant_id=,
patient_id=)` → `ObservationCreate` attached to the local patient. Multi-component
observations (blood pressure, panels) and `note[]` are preserved (H2). Multi-range
`referenceRange[]` is preserved as the canonical FHIR list (H6). The provider
owns the token lifecycle for SMART (`SmartOAuth.get_live_token` +
`force_refresh` on a 401 race); see `integrations/fhir_server/provider.py`.

For **outbound FHIR data** (push), use `fhir_conditional_update(http_client,
base_url, resource_type, body, *, search_params=, access_token=)` — FHIR
conditional update via PUT. Returns `(status, response_body)`; `412` is returned
(not raised) so the caller can treat it as "skipped". Raises the standard
exception hierarchy (`IntegrationAuthError` / `IntegrationRateLimitError` /
`IntegrationDataError`) with **OperationOutcome-parsed diagnostics** in the
error message (H8). Use `fhir_create(http_client, base_url, resource_type, body,
*, access_token=)` for a simple POST (e.g. remote Provenance after a push, H3).
`parse_operation_outcome(response_json)` extracts the first `issue[].diagnostics`
from a FHIR error body.

```python
from integrations.sdk import BaseHealthProvider, SmartOAuth

class MyProvider(BaseHealthProvider):
    domain = "my_oauth_integration"

    async def setup(self, config):
        self._smart = SmartOAuth(self._http_client)

    async def begin_oauth(self, integration, redirect_uri, *, extra_state=None):
        return await self._smart.begin_connect(
            integration.user_config["server_url"], redirect_uri,
            "Health Assistant", extra_state=extra_state,
        )

    async def complete_oauth(self, integration, pending, code):
        return await self._smart.complete_connect(integration, pending, code)
```

On use, refresh-on-access: `await provider.get_live_token(integration)` returns a
valid token, refreshing first if expired (raises `IntegrationAuthError` if the
refresh token is gone). Reference implementation:
`integrations/fhir_server/provider.py` (SMART standalone launch, two-way pull +
push with conditional update, remote Provenance, push resilience, and write-scope
detection).

Requires: `INTEGRATION_SECRET_KEY` (Fernet) + Redis (`REDIS_URL`) configured.

---

### 3.9 Notifications (Event-Driven, Rich, Actionable)

Providers can emit rich, event-driven notifications — threshold alerts, HITL-style prompts, anomaly flags, daily summaries — by overriding three opt-in hooks on `BaseHealthProvider`. The pattern mirrors `supports_tools` / `get_tools` (§3.6) and `get_custom_actions` / `execute_custom_action` (§3.5): safe defaults, no per-domain code in any endpoint, providers opt in by overriding.

The platform emits two kinds of notifications for every integration:

1. **Baseline** (always on, automatic) — "synced N records" / "sync failed" from `integration_sync_service._notify_sync_outcome`. Default, no provider code required.
2. **Provider-authored** (opt-in) — domain-specific events from `get_notifications`. The provider inspects the just-synced observations and decides what (if anything) to surface to the user.

#### Hooks

There are three runtime hooks (`supports_notifications`, `get_notifications`, `handle_notification_action` — documented below) plus one static declaration hook (`get_notification_types` — documented in the **Per-type user preferences** subsection at the end of §3.9).

```python
class MyProvider(BaseHealthProvider):
    def supports_notifications(self) -> bool:
        """Return True to opt in. Default False — existing providers unaffected."""
        return True

    async def get_notifications(
        self,
        integration: UserIntegration,
        *,
        observations: List[ObservationCreate],
        context: Dict[str, Any],
    ) -> List[NotificationSpec]:
        """Inspect the just-persisted observations + return specs to emit.

        ``context`` contains:
          * ``sync_result`` — SyncResult (counts, status, timing)
          * ``patient_id`` — the integration's resolved patient_id (may be None)
          * ``integration_id`` / ``domain`` — identifying metadata

        Default: ``[]``. Never raise — swallow errors and return ``[]`` so one
        bad notification doesn't break the sync.
        """
        return []

    async def handle_notification_action(
        self,
        integration: UserIntegration,
        action_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle a clicked action button of ``type='post'``.

        Default: ``NotImplementedError``. Override only when you emit POST
        actions. Return an ``action_result(...)`` dict; the frontend renders
        it in a follow-up modal with the same DisplayBlocks renderer used
        by ``execute_custom_action``.
        """
        raise NotImplementedError(...)
```

#### `NotificationSpec` builder

```python
from integrations.sdk import NotificationSpec
from integrations.sdk.display import kv_block, table_block

spec = (
    NotificationSpec.builder(
        title="Elevated heart rate detected",
        body="120 bpm observed (reference 60–100).",
        category="alert",        # any NotificationCategory value
        severity="warning",      # info | warning | critical
        type="INTEGRATION_EVENT",  # any NotificationType value (default)
    )
    .patient_id(integration.patient_id)
    .source_ref("biomarker_code", "8867-4")
    .add_link_action(
        "View trend",                       # navigate (relative URL → router)
        f"/patients/{pid}",                 # ← patient dashboard; see "Deep-link gotcha" below
        style="primary",
    )
    .add_post_action(
        "Acknowledge",                      # POST → handle_notification_action
        endpoint=f"/integrations/{self.domain}/notification-action/{iid}/ack",
        style="ghost",
    )
    .display_block(                         # rendered in the notification modal
        kv_block("Reading", {"value": 120, "unit": "bpm", "range": "60–100"})
    )
    .build()
)
```

**Builder methods:**

| Method | Purpose |
|---|---|
| `body_text(s)` | Override the body |
| `patient_id(pid)` | Attach patient context (linked FHIR Communication gets written automatically for clinical categories) |
| `payload_field(k, v)` | Add an arbitrary key to `payload` |
| `source_ref(k, v)` | Add an arbitrary key to `source_ref` (the platform auto-injects `integration_id` + `provider`) |
| `add_link_action(label, url, *, style)` | Button that navigates to an app URL |
| `add_post_action(label, *, endpoint, style)` | Button that POSTs to a platform endpoint that dispatches to `handle_notification_action` |
| `add_action(NotificationAction)` | Build-it-yourself escape hatch |
| `display_block(block)` | Attach a DisplayBlock (`kv_block` / `list_block` / `table_block` / `json_block` / `text_block` / `code_block`) |
| `targets([{kind, id}])` | Override the default "integration owner only" target |

#### Action button contract

The frontend renders `payload.actions[]` and supports:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Unique per notification |
| `label` | string | Button text |
| `type` | `"link"` \| `"post"` | `link` → navigate; `post` → POST to `endpoint` |
| `url` | string? | Required for `type="link"`. Relative URL → react-router; absolute → new tab |
| `endpoint` | string? | Required for `type="post"`. Always under `/integrations/{domain}/notification-action/{iid}/{action_id}` |
| `method` | string | Default `"POST"` |
| `style` | `"primary"` \| `"danger"` \| `"ghost"` \| `"default"` | Visual treatment |

POST clicks route through `POST /api/v1/integrations/{domain}/notification-action/{integration_id}/{action_id}` (tenant-scoped to the integration owner) → `provider.handle_notification_action(integration, action_id, payload)`. The return value is rendered in the same modal pattern as `execute_custom_action` results (§3.5).

##### Deep-link gotcha: biomarker detail

The biomarker detail route is **`/biomarkers/details/<definition-UUID>`** — not patient-scoped, and the path segment is the biomarker **definition id** (a UUID), **not** the LOINC code (e.g. `8867-4`). A URL like `/patients/{pid}/biomarkers/details/8867-4` will 404. Providers don't know the definition UUID at notification-emit time (only the LOINC they emitted in `ObservationBuilder.set_biomarker`), so deep-linking directly to a specific biomarker's detail page is generally not possible from a notification. Link to the **patient dashboard** (`/patients/{pid}`) instead — the user sees all biomarkers in context and can pick out the relevant one. The `dev_dummy` provider demonstrates this pattern.

#### Categories

`category` accepts any `NotificationCategory` value. Integrations commonly use:

- `"integration"` — default, neutral sync events
- `"alert"` — threshold breaches
- `"hitl"` — prompts that need user review/confirmation
- `"agent"` — AI-style prompts (e.g. "Open chat to discuss")
- `"system"` — daily summaries, informational

Pick whatever fits the message — there's no enforcement that an integration must use `"integration"`.

#### When notifications fire

The platform calls `get_notifications`:

1. After every **pull sync** (`run_sync`) that successfully persists ≥1 observation.
2. After every **webhook** that successfully processes ≥1 observation (closing the historical gap where webhooks bypassed `run_sync`).
3. **Not** on skipped syncs (rate-limit / no new data) — stay silent, matching the baseline behavior.

`source_ref.integration_id` is auto-injected so the admin center can group/filter by integration.

#### Targeting

Default = integration owner (USER target). Override per-spec with `targets([{kind, id}])` to also reach a patient's care team (`kind="PATIENT"`), a specific doctor (`kind="DOCTOR"`), or the whole tenant (`kind="TENANT"`). See [NOTIFICATION_SYSTEM.md](NOTIFICATION_SYSTEM.md) for the full target resolver spec.

Per-user preference filtering is applied at `emit()` time — if the recipient has disabled the `INTEGRATION` source or the relevant channel in their settings, the notification is silently dropped for them (admin center still sees the event row).

#### Per-type user preferences (opt-out)

Providers can statically declare the notification **kinds** they emit, so users can toggle individual kinds on/off without losing the rest. Mirrors Android Notification Channels / iOS per-app categories. The pattern matches `get_custom_actions` (§3.5): static declaration, single-line override, platform-aggregated UI.

**Declaration** — override `get_notification_types` on the provider:

```python
from integrations.sdk import NotificationTypeSpec

class MyProvider(BaseHealthProvider):
    def get_notification_types(self) -> list[NotificationTypeSpec]:
        return [
            NotificationTypeSpec(
                id="elevated_heart_rate",
                label="Elevated heart-rate alerts",
                description="Fires when a synced heart-rate reading exceeds 100 bpm.",
                category="alert",         # suggested category
                severity="warning",       # suggested severity
                default_enabled=True,     # opt-OUT convention (see below)
                channels=("IN_APP", "PUSH"),  # advisory
            ),
            NotificationTypeSpec(
                id="daily_summary",
                label="Per-sync summary",
                description="Informational table of every measurement imported.",
                category="system",
                severity="info",
                default_enabled=True,
            ),
        ]
```

**Linkage** — tag runtime specs with `type_id`:

```python
spec = (
    NotificationSpec.builder(title="Elevated heart rate", ...)
    .type_id("elevated_heart_rate")   # link to the declared type
    .build()
)
```

**Filtering** — the platform drops specs whose `type_id` the integration owner has muted, before dispatching. Prefs live at `user.settings["notifications.integration.{domain}.{type_id}"] = False`. Specs without a `type_id` always pass through (backwards-compatible — providers that don't declare types are unaffected).

**Default-on convention**: providers SHOULD set `default_enabled=True` for every type. Users opt OUT, not IN. The platform baseline is already opt-in (`supports_notifications()` returns `False` by default) — once a provider opts in, the types should default ON. Otherwise nobody discovers the feature. Reserve `default_enabled=False` for types that are genuinely noisy in practice (rare).

**Storage**: prefs are keyed by `(domain, type_id)`, NOT by integration instance. A user with two Fitbit accounts shares the same per-type prefs across both. This avoids state explosion; per-instance prefs are a power-user feature for later.

**UI surfaces** (auto-rendered by the platform; no per-domain code):
1. **IntegrationDetail → "Notifications" tab** (conditional — only renders when `get_notification_types()` returns ≥1 type). Each type shown with label, description, category/severity badges, channels hint, and a toggle switch. Deep-links to the central settings page.
2. **`/settings/notifications` → "Per-integration notification types" collapsible** (under "Advanced"; auto-hidden when no integrations declare types). Aggregates every enabled integration's types in one place. Deep-links back into each integration's tab.

**Three filter layers compose cleanly** — all enforced server-side at `emit()` / `_emit_provider_notifications` time:
- **Per-source** (global): "mute all INTEGRATION notifications" — `notifications.sources.INTEGRATION = false`
- **Per-channel** (global): "no PUSH, only IN_APP" — `notifications.channels.PUSH = false`
- **Per-integration-type** (specific): "mute dev_dummy daily summaries" — `notifications.integration.dev_dummy.daily_summary = false`

A notification fires only if it passes ALL three layers.

**Limitation**: the per-type filter is keyed on the **integration owner's** prefs. If a spec broadcasts beyond the owner (via `targets_override`), recipients past the owner still receive it — they're filtered only by the per-source + per-channel layers. Per-recipient type prefs would require coupling `emit()` to integration concepts, which is out of scope.

#### Worked example: `integrations/dev_dummy/`

The dev dummy provider overrides `supports_notifications() → True`, declares **4 NotificationTypeSpecs** covering every category/severity combination (alert/warning, hitl/warning, system/info, agent/critical), tags every emitted spec with the matching `type_id`, and implements `handle_notification_action` for the Acknowledge / Dismiss buttons. Read `integrations/dev_dummy/provider.py` as a copy-paste reference for the full type-declaration + runtime-link + action-handler round-trip.

---

### 3.10 Clinical Events & Examinations (Opt-in Write Hooks)

Providers that sync longitudinal data — hospital admissions, chronic conditions, pregnancies (clinical events) or lab encounters, imaging appointments, hospital visits (examinations) — can pull those records alongside observations via two opt-in hooks. The platform engine resolves a service-context actor from the integration's owning user, validates and writes each record through the canonical service (`clinical_event_service.create_event` / `examination_service.create_examination`), and dedups across syncs when the provider sets the upstream stable id.

The pattern mirrors `supports_tools` (§3.6) and `supports_notifications` (§3.9): safe defaults, no per-domain code anywhere in the engine, providers opt in by overriding.

#### The two hooks

```python
class MyProvider(BaseHealthProvider):
    domain = "my_ehr"

    # --- Clinical events (FHIR Condition / EpisodeOfCare) ---
    def supports_clinical_events(self) -> bool:
        return True

    async def pull_clinical_events(
        self, integration: UserIntegration
    ) -> List[ClinicalEventCreate]:
        # Fetch from upstream, build payloads, return.
        # Set external_id on each payload to the upstream's stable
        # encounter/episode id so the engine dedups across syncs.
        ...

    # --- Examinations (FHIR Encounter) ---
    def supports_examinations(self) -> bool:
        return True

    async def pull_examinations(
        self, integration: UserIntegration
    ) -> List[ExaminationCreate]:
        # Set external_id on each payload to the upstream's stable
        # visit id so the engine dedups across syncs.
        ...
```

Both `ClinicalEventCreate` and `ExaminationCreate` are re-exported from `integrations.sdk` — build payloads without reaching into `app.schemas`.

#### Dedup contract (set `external_id`)

Without a dedup key, every sync creates fresh duplicates. With one, the engine looks up an existing record by `(tenant_id, patient_id, source_integration_id, external_id)` and returns it as-is if found — no duplicate insert, no notification re-fire. A **partial unique index** at the DB layer catches the race window between the SELECT and INSERT so concurrent sync attempts can't double-insert.

- `external_id` — set by the provider on the payload. Use the upstream system's stable id (the hospital's encounter id, the wearable's session id, ...). Optional but strongly recommended.
- `source_integration_id` — **not** on the payload schema. The engine always supplies it (= the integration's own id) so providers can't fake their provenance.

For examinations, the service also runs a **heuristic UI dedup** (date + category + notes) when called by an interactive user — but that path is bypassed entirely for integration-sourced writes (any caller that sets `source_integration_id`), so an integration-sourced exam can't accidentally match an unrelated UI row.

#### What the engine does for you

For each pulled record the engine:
1. Resolves a service-context `TokenData` from the integration's owning user via `resolve_integration_actor` (`app/services/integration_actor.py`). The integration inherits its owner's tenant, role, and user_id — same RBAC, same audit provenance, same tenant scoping as an interactive UI request. No service-account-style "integration user" is created.
2. Calls the canonical write service (`create_event` / `create_examination`), which performs patient validation, category-text → concept resolution, the dedup check above, ORM construction with `source_integration_id` + `external_id` populated, doctor/category linking, audit columns stamped, and commit.
3. Logs per-record failures but does not abort the sync — mirrors the push hook's resilience.

Per-record failures are logged but don't break the sync turn; `records_synced` in the resulting `IntegrationSyncLog` includes both observations and pulled events/exams.

#### When to use which

| Use case | Hook |
|---|---|
| Lab integration grouping biomarker results under a single lab panel | `supports_examinations` |
| Hospital EHR syncing admission / discharge / visit records | `supports_examinations` |
| Chronic-condition tracking (ongoing pregnancy, long-term pain) | `supports_clinical_events` |
| Hospital EHR syncing problem list (Conditions) | `supports_clinical_events` |
| Wearable pulling heart-rate / steps telemetry | neither — return `ObservationCreate` from `pull_data`, the engine's telemetry/FHIR split handles it |

The two hooks are independent: a provider can implement one, both, or neither. Each fires only when its `supports_*` probe returns `True`.

#### Reference implementation

The bridge provider (`integrations/health_assistant_bridge/provider.py`) is the reference for examination creation via the canonical service — its `_process_and_save_sync_data` builds `ExaminationCreate` payloads from upstream client records and calls `examination_service.create_examination` directly from its two-way API handler. The same call shape applies inside `run_sync` when a provider opts into `supports_examinations`.

---

## 4. Building FHIR Observations
Use the `ObservationBuilder` to map raw third-party data into FHIR compliant schemas easily.

### Supported Coding Systems
When building observations, the system relies on coding systems to uniquely identify biomarkers without collisions. The `set_biomarker()` method defaults to LOINC, but you can explicitly define the system using the `CodingSystem` enum:

* **`CodingSystem.LOINC`** (Default) - Used for standard clinical laboratory measurements and standard vital signs.
* **`CodingSystem.SNOMED`** - Used for clinical terms and findings.
* **`CodingSystem.CUSTOM`** - **Crucial for wearables/IoT.** Use this when the external API provides a proprietary identifier (e.g., Apple's `HKQuantityTypeIdentifierStepCount` or Garmin's internal metric keys) to ensure it does not accidentally collide with a real clinical code.


> **Important Developer Note:** You might wonder how telemetry data (like high-frequency heart rate from a wearable) gets saved to the TimescaleDB hypertable if you are returning standard FHIR `ObservationCreate` objects. 
> 
> **You do not need to interact with TimescaleDB directly.** Simply return `ObservationCreate` objects for all data. The platform's core sync engine automatically intercepts your observations. It checks the associated Biomarker Definition's `is_telemetry` flag. If it is `true`, the sync engine will dynamically mutate the observation and route it to the `telemetry_data` hypertable instead of saving it as a standard FHIR observation.

```python
builder = self.create_observation_builder(integration)

obs = (
    builder
    .set_biomarker("8867-4", "Heart rate") # Defaults to LOINC Code
    .set_value(72.0, "bpm", "{beats}/min")
    .set_reference_range(low=60, high=100)
    .set_effective_date(timestamp_obj) # Defaults to now() if omitted
    .build()
)

# For proprietary codes (e.g., Apple HealthKit keys):
from app.models.enums import CodingSystem

obs_custom = (
    builder
    .set_biomarker("HKQuantityTypeIdentifierStepCount", "Step Count", coding_system=CodingSystem.CUSTOM)
    .set_value(5000, "steps", "{steps}")
    .build()
)
```

### Categorical / string values (`set_value_string`)

Some observations don't have a numeric value — lab PCR results (`"POSITIVE"` / `"NEGATIVE"`), sleep stages (`"REM"` / `"DEEP"`), subjective pain scores, questionnaire answers. FHIR R4 §3.1.1 allows exactly one `value[x]` per observation, so `set_value` (numeric → `valueQuantity`) and `set_value_string` (categorical → `valueString`) are **mutually exclusive**: the last setter wins, the other slot is cleared.

```python
obs_categorical = (
    builder
    .set_biomarker("94500-6", "SARS-CoV-2 PCR", coding_system=CodingSystem.LOINC)
    .set_value_string("POSITIVE")    # emits valueString, omits valueQuantity
    .set_effective_date(timestamp_obj)
    .build()
)
```

The builder leaves `raw_value` / `normalized_value` / `relative_score` unset for categorical observations — they're numeric concepts and downstream analytics must not try to plot a string on a numeric axis.

---

## 5. Enable your Integration
By default, newly written integrations are invisible. A system administrator must execute a command or use the Admin UI to enable it globally.

Via SQL:
```sql
INSERT INTO system_integrations (domain, is_enabled) 
VALUES ('notify', true)
ON CONFLICT (domain) DO UPDATE SET is_enabled = true;
```