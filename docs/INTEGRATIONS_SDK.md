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

### 3.11 Catalog Proposals (Opt-in Catalog Contributions)

Providers that discover new catalog entries upstream — a wearable integration that adds a "Sleep Quality Score" metric the local catalog doesn't have, a hospital integration that knows about a disease↔biomarker mapping the local ontology is missing, a medication database the integration can subscribe to — can contribute them via the catalog-proposals hook. The platform engine **auto-applies** each proposal through the canonical catalog write path. For the human-in-the-loop variant (queue for review instead of auto-apply), see §3.12.

The pattern mirrors §3.10: safe defaults, no per-domain code anywhere in the engine, providers opt in by overriding.

#### The two hooks

```python
from integrations.sdk import (
    BaseHealthProvider, CatalogProposal,
    biomarker_proposal, medication_proposal,
    concept_proposal, edge_proposal,
)

class MyProvider(BaseHealthProvider):
    domain = "wearable_sync"

    def supports_catalog_proposals(self) -> bool:
        return True

    async def pull_catalog_proposals(
        self, integration: UserIntegration
    ) -> List[CatalogProposal]:
        # Inspect what was just pulled (or re-derive from your own state),
        # return one CatalogProposal per catalog entry to contribute.
        return [
            biomarker_proposal(
                name="Sleep Quality Score",
                slug="sleep-quality-score",
                category="Sleep",
                coding_system="custom",
                code="HKSleepQualityScore",
                is_telemetry=False,
                reference_range_min=0.0,
                reference_range_max=100.0,
                confidence=0.8,
                rationale="Observed recurring HKSleepQuality codes upstream",
            ),
        ]
```

`CatalogProposal` is a Pydantic spec with a `kind` discriminator (`"biomarker"` / `"medication"` / `"concept"` / `"edge"`) + a `payload` dict whose shape depends on `kind`. Four typed convenience constructors (`biomarker_proposal`, `medication_proposal`, `concept_proposal`, `edge_proposal`) build the spec without hand-writing the dict.

Payload shapes per kind (mirrors the chat-side `propose_create_*` HITL tools):

| `kind` | Payload fields |
|---|---|
| `biomarker` | `name`, `slug` (optional — derived from name), `category`, `coding_system`, `code`, `preferred_unit_symbol`, `reference_range_min/max`, `aliases`, `info`, `is_telemetry` |
| `medication` | `name`, `description`, `indications`, `dosage_info`, `contraindications`, `side_effects` |
| `concept` | `slug`, `name`, `kind` (a `ConceptKind` value like `"disease"` / `"body_system"`), `description`, `coding_system`, `code`, `aliases` |
| `edge` | `src_type`/`src_id`/`dst_type`/`dst_id`/`relation` (enum values), optional `properties` + `evidence` |

#### What the engine does for you

For each proposal the engine:
1. Resolves a service-context actor from the integration's owning user via `resolve_integration_actor` (same pattern as §3.10).
2. Calls `catalog_proposal_service.apply_proposal(db, actor, integration, proposal)`, which routes by `kind` to the matching service-layer write: `BiomarkerDefinition` for biomarkers, `MedicationCatalog` for medications, `ConceptService.create_concept` for concepts, `ConceptService.create_edge` for edges.
3. Stamps provenance:
   - `AuditMixin.created_by` = the integration's owning user (recorded via the actor).
   - `scope` / `tenant_id` derived from the actor's role via `CatalogWritePolicy.assign_create_scope` (USER → USER scope, ADMIN → TENANT scope, SYSTEM_ADMIN → SYSTEM scope).
   - `ConceptProvenance.INTEGRATION` on `ConceptEdge.source` (the only model with a dedicated provenance column today).
   - `BiomarkerDefinition.meta_data["_provenance"] = "integration"` tag (the model has no dedicated column).
4. Returns an `ApplyResult(created: bool, entity_id: UUID, ...)` so the engine knows whether a new row was actually inserted.

Per-proposal failures are logged and don't abort the sync (mirrors §3.10 + §3.9 resilience). `records_synced` in the resulting `IntegrationSyncLog` includes applied proposals alongside observations/events/exams.

#### Idempotency

Re-applying the same proposal on consecutive syncs is a no-op:

- `biomarker` — idempotent on `slug` (globally unique).
- `medication` — idempotent on `(tenant_id, name)`.
- `concept` — idempotent on `slug`.
- `edge` — idempotent on `(src_type, src_id, dst_type, dst_id, relation)`.

The provider doesn't need to track what it's already contributed — just keep emitting the same proposals; the engine dedups.

#### Per-sync cap

`INTEGRATION_MAX_PROPOSALS_PER_SYNC = 50` (in `app/services/integration_sync_service.py`). Excess proposals are dropped with a warning. The cap is module-level today; env-var-ification is on the roadmap.

#### Ordering matters for edges

`ConceptService.create_edge` validates that the source + destination concepts exist. If a provider proposes an edge to a concept it just proposed in the same batch, the concept proposal **must come first** in the returned list. The engine processes proposals in the order the provider returns them.

#### Role limitation for concept / edge writes

`ConceptService.create_concept` and `create_edge` require role `ADMIN` or higher (raises `PermissionError` for `USER`). A USER-role integration owner can still contribute biomarkers and medications (the catalog policy allows it for TENANT scope), but concept / edge proposals fail with `PermissionError` — the engine logs and skips. Document this for your users, or have an ADMIN co-own the integration.

---

### 3.12 HITL Proposals (Human-in-the-Loop Catalog Review)

The HITL layer is the **human-in-the-loop counterpart** to §3.11. Same payload shapes, same write path — but instead of auto-applying each proposal, the platform **queues it for human review**. The integration owner (or a tenant admin) approves / rejects / cancels each proposal through a dedicated endpoint, and only on approve does the canonical write fire.

Use §3.11 (`supports_catalog_proposals`) for entries the integration is confident about (low-risk, idempotent — e.g. declaring a known LOINC code the local catalog is missing). Use this section (`supports_hitl_proposals`) for entries that need a human's judgement (e.g. mapping a novel upstream biomarker to an existing catalog class, contributing a typed concept edge inferred from upstream data).

A single provider can opt into both — the engine treats them independently.

#### The three hooks

```python
from integrations.sdk import (
    BaseHealthProvider,
    biomarker_hitl_proposal, medication_hitl_proposal,
    concept_hitl_proposal, edge_hitl_proposal,
    ProposalOutcome,
)

class MyProvider(BaseHealthProvider):
    domain = "hospital_ehr"

    def supports_hitl_proposals(self) -> bool:
        return True

    async def pull_hitl_proposals(
        self, integration: UserIntegration
    ) -> List[IntegrationProposalSpec]:
        # Inspect upstream, propose mappings the user should review.
        return [
            biomarker_hitl_proposal(
                title="Define Biomarker: Apo-B",
                name="Apolipoprotein B",
                slug="apo-b",
                category="Lipids",
                confidence=0.7,
                rationale="Upstream lab reports Apo-B but local catalog lacks it",
            ),
        ]

    async def handle_proposal_resolution(
        self,
        integration: UserIntegration,
        proposal_id: UUID,
        outcome: ProposalOutcome,
    ) -> None:
        # Optional: react when the user resolves a proposal. Advance a
        # cursor so you don't re-propose, log audit, suppress duplicates, ...
        # Default is a no-op; safe to leave unimplemented.
        if outcome.error is None:
            self.set_sync_cursor(
                integration, "last_approved_proposal", str(proposal_id)
            )
```

The SDK spec `IntegrationProposalSpec` uses a `proposal_type` discriminator with values `create_biomarker_definition` / `create_medication_definition` / `create_concept` / `create_edge` — names mirror the chat-side AI HITL `task_type` strings so a future unified review UI can render both sources identically. Four typed constructors wrap the same payload shape as §3.11's constructors but tag each for human review.

#### Persistence + state machine

The engine persists each spec as an `IntegrationProposal` row in the `integration_proposals` table with status `PROPOSED`. The state machine:

```
                ┌────────────┐
                │  PROPOSED  │
                └─────┬──────┘
        ┌─────────────┼─────────────┐
    approve        reject        cancel
        │             │             │
        ▼             ▼             ▼
  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │CONFIRMED │  │DISMISSED │  │DISMISSED │
  └──────────┘  └──────────┘  └──────────┘
        │ apply error
        ▼
  ┌──────────┐
  │  FAILED  │
  └──────────┘
```

The resolver endpoint performs the transition:
- `approve` → routes the (possibly user-edited) payload through `catalog_proposal_service.apply_proposal` — **the same write path §3.11 uses**. `CONFIRMED` on success, `FAILED` on apply error (the row stays around for retry / re-review).
- `reject` → `DISMISSED`, no apply. User reviewed and declined.
- `cancel` → `DISMISSED`, no apply. User dismissed without considering (e.g. closed the modal). Distinguishable from `reject` in audit by the `note`.

Re-resolve from a terminal state returns **409** (idempotent contract).

#### Dedup contract

The engine computes a `dedup_key = sha256(canonical_json({"type": proposal_type, "payload": proposed_payload}))` per spec. A partial unique index on `(integration_id, dedup_key) WHERE dedup_key IS NOT NULL` enforces idempotency at the DB layer:

- Re-emitting the same spec on the next sync is a no-op (returns the existing PROPOSED row, no new notification).
- Re-emitting after the user has decided (CONFIRMED / DISMISSED / FAILED) is also a no-op — the engine doesn't re-spam the inbox. **Providers wanting stronger "don't re-propose after decision" semantics should advance their own cursor in `handle_proposal_resolution`.**

#### Resolver endpoints (3 new)

| Route | Auth | Purpose |
|---|---|---|
| `GET /api/v1/integrations/instance/{id}/proposals?status=&limit=&offset=` | user (owner) | List proposals, optionally filtered by status |
| `GET /api/v1/integrations/instance/{id}/proposals/{proposal_id}` | user (owner) | Fetch one |
| `POST /api/v1/integrations/instance/{id}/proposals/{proposal_id}/resolve` | ADMIN+ for `approve`; USER can `reject`/`cancel` | Resolve |

The resolve body is `{action: "approve"\|"reject"\|"cancel", payload?: Dict, note?: str}` — the optional `payload` overrides `proposed_payload` on approve (user edits in the review modal). The response includes `status`, `resolved_payload`, `applied_entity_id` (on successful approve), and `error` (on FAILED).

**USER role can list + view but not approve** catalog proposals (catalog writes require ADMIN+ under the catalog policy) — the endpoint returns 403 so the UI can hide the buttons.

#### Per-sync cap + notification

`INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC = 20`. Excess dropped with a warning. For each **newly-inserted** row (not existing-and-returned ones, to avoid inbox spam on re-sync), the engine fires a notification via `notification_service.emit` with:

- `source = NotificationSource.INTEGRATION`
- `type = NotificationType.HITL_TASK`
- `category = NotificationCategory.HITL`
- `severity = NotificationSeverity.WARNING`
- Action button linking to the integration's proposals view

The notification shape matches the chat-side AI HITL (`source = AGENT`), keyed on the integration source so the frontend can render them separately if desired.

#### Relationship to the chat-side HITL

This is the **integration-side** HITL, distinct from the existing AI-side HITL documented in `NOTIFICATION_SYSTEM.md`:

| | Chat-side HITL | Integration-side HITL (this section) |
|---|---|---|
| Source | `NotificationSource.AGENT` | `NotificationSource.INTEGRATION` |
| Triggered by | AI agent's `propose_*` chat tools | Provider's `pull_hitl_proposals` SDK hook |
| Persistence | `ChatMessage.tasks` JSONB | `integration_proposals` table (this section) |
| Resolver | `/ai-assistance/sessions/{id}/tasks/{id}/resolve` (records outcome; the frontend commits the write separately via canonical REST) | `/integrations/instance/{id}/proposals/{id}/resolve` (performs the write server-side via `apply_proposal`) |

A future workstream may consolidate the two onto a single persistence layer (flagged in the parent plan); for now they coexist.

#### Frontend status

The review card + modal UI (parallel to `HitlTaskCard`) is on the roadmap. Until then, proposals are visible in the notification inbox and resolvable via the REST endpoints above.

---

### 3.13 Document Pull (Opt-in Document Ingestion)

Providers that can deliver document bytes from upstream — a hospital integration that pulls scanned lab reports, a fax-to-email gateway that forwards PDFs, a wearable companion app that syncs ECG printouts — can hand them to the platform's OCR + LLM extraction pipeline via the documents hook.

#### The two hooks

```python
from integrations.sdk import BaseHealthProvider, DocumentPull

class MyProvider(BaseHealthProvider):
    domain = "hospital_portal"

    def supports_documents(self) -> bool:
        return True

    async def pull_documents(
        self, integration: UserIntegration
    ) -> List[DocumentPull]:
        # Fetch the bytes yourself (HTTP download, base64 decode, ...).
        # The platform ingests whatever bytes you return.
        reports = await self._fetch_recent_reports(integration)
        return [
            DocumentPull(
                filename=report.filename,
                content=report.pdf_bytes,
                content_type="application/pdf",
                examination_external_id=report.encounter_id,
                category_concept_slug="lab-report",
                include_in_extraction=True,
            )
            for report in reports
        ]
```

`DocumentPull` is a Pydantic spec with `filename` (gated by the medical-types allowlist — PDF, images, DICOM, plain text), `content` (raw bytes), `content_type` (informational), `examination_external_id` (links to a pulled exam — see below), `category_concept_slug` (resolves to a catalog concept), and `include_in_extraction` (default `True` — fires the OCR + LLM extraction Celery task).

#### What the engine does for you

For each spec the engine:
1. **Cap check** — rejects if adding the document would exceed `INTEGRATION_MAX_DOCS_PER_SYNC = 20` (item count) or `INTEGRATION_MAX_DOC_BYTES_PER_SYNC = 50 MiB` (running byte total) for this sync. Dropped items log a warning.
2. **Examination link** — if `examination_external_id` is set, resolves it via a `{external_id: exam_id}` map built during the examinations step (§3.10) — i.e. against the exam that was just pulled in the same sync. Misses are non-fatal (document created unlinked).
3. **Category link** — if `category_concept_slug` is set, resolves it via `resolve_concept_by_slug`. Misses are non-fatal.
4. **Writes** via `document_service.ingest_document_bytes` — the **same canonical ingestion path the UI upload endpoint uses**. Writes the file under `UPLOAD_DIR/<tenant_id>/`, creates the `DocumentModel` row with `owner_id` = the integration's owning user, fires the OCR task best-effort when `include_in_extraction=True`.

Per-document failures are logged and don't abort the sync.

#### Idempotency

There are **no document-level dedup columns** on `DocumentModel` today (no `source_integration_id` / `external_id`). Re-pulling the same upstream file on the next sync creates a fresh row. **Providers are responsible for advancing their own cursor** via `set_sync_cursor` so they don't re-pull:

```python
async def pull_documents(self, integration):
    last_pull = self.get_sync_cursor(integration, "last_doc_pulled_at")
    new_docs = await self._fetch_docs_since(last_pull)
    if new_docs:
        self.set_sync_cursor(
            integration, "last_doc_pulled_at", new_docs[-1].fetched_at.isoformat()
        )
    return new_docs
```

A future workstream may add document-level dedup columns if providers prove unable to manage their own cursors.

#### Storage + size considerations

- **Local disk only** — the storage path is `UPLOAD_DIR` (env-configurable, defaults to `/var/healthassistant/uploads`). Self-hosted deployments can mount a persistent volume. S3 / object-store abstraction is on the roadmap.
- **Memory pressure** — a provider returning 100 MB of PDFs in one batch holds them all in memory. The 50 MiB per-sync byte cap mitigates the worst case; providers with very large documents should chunk across syncs.
- **Extension allowlist** — only medical-document types are accepted: PDF (`.pdf`), PNG/JPG/BMP/WebP/TIFF/GIF, DICOM (`.dcm`), plain text (`.txt`, `.md`). Others raise `HTTPException(400)` at the extension gate.

#### Relationship to the UI upload path

The UI's `POST /api/v1/documents` endpoint is a thin wrapper around the same `ingest_document_bytes` function this hook uses. Both write to the same `UPLOAD_DIR`, both stamp the same `DocumentModel` columns, both dispatch the same OCR task. A document pulled by an integration is indistinguishable in the UI from one uploaded by the user — the `owner_id` is the integration's owning user, same as if they'd uploaded it themselves.

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

---

## See also

- [Integrations Framework](INTEGRATIONS_FRAMEWORK.md) — high-level architecture, lifecycle, and highlight bullets for every opt-in capability.
- [REST API Reference](API.md) — the `/api/v1/integrations/*` endpoints (config-flow, sync, webhook, two-way API, HITL proposals resolve).
- [Notification System](NOTIFICATION_SYSTEM.md) — the chat-side AI HITL flow that the integration-side HITL (§3.12) parallels.
- [FHIR R4 Facade](FHIR_R4_FACADE.md) — 19 conformance resources, CapabilityStatement, search Bundles.
- `integrations/dev_dummy/` — reference provider demonstrating custom actions, notifications, cursors, and error simulation.
- `integrations/health_assistant_bridge/` — reference two-way API provider; its `provider.py` is the canonical example of examination creation via the service layer (§3.10).
- `integrations/fhir_server/` — reference SMART-on-FHIR + tokenless-FHIR pull/push integration (§3.8).