# Authoring & Configuration

The complete authoring surface: the directory + manifest, the two
implementation classes, every capability hook, and building FHIR
observations. Everything here is re-chaptered verbatim-in-spirit from the
previously-merged extension guide; [Core Concepts](core-concepts.md) has
the nouns.

## Contents

[Directory & files](#the-directory-one-builtin-folder) ·
[manifest.json](#1-manifestjson-the-charter-envelope) ·
[config_flow.py](#2-configflowpy) · [provider.py](#3-providerpy) ·
[Pull data + cursors](#pull-data-and-cursors) ·
[Webhook handlers](#webhook-handlers-opt-in-push) ·
[Two-way API proxy](#two-way-api-proxy-opt-in-bridge-kind) ·
[OAuth / SMART](#oauth--smart-on-fhir-cloud-integrations) ·
[Notifications](#notifications-event-driven-rich-actionable) ·
[Tool exposure](#tool-exposure-for-the-chat-assistant) ·
[Custom actions](#custom-actions-services) ·
[Typed errors](#typed-errors-the-charter-model) ·
[Secrets](#secret-encryption-at-rest) ·
[Payload debugging](#payload-debugging) ·
[Write hooks](#clinical-events--examinations-write-hooks) ·
[Catalog proposals](#catalog-proposals-auto-applied) ·
[HITL proposals](#hitl-proposals-human-review) ·
[Document pull](#document-pull) ·
[Treatment resources](#medications-allergies-immunizations) ·
[Webhook signatures](#webhook-signature-verification-reusable-helpers) ·
[Building FHIR observations](#building-fhir-observations) ·
[Enabling](#enabling-your-integration)

## The directory: one `builtin/` folder

To create a new bundled extension, create a folder under
`backend/app/extensions/builtin/{domain}` (underscore form, e.g.
`smartwatch_sync`). The manifest's envelope `id` is the hyphenated form
(`smartwatch-sync`) — the underscore domain survives as the product alias
everywhere else (REST paths, stored `provider` values, package name),
derived mechanically.

Your integration must include three core files:
1. `manifest.json`
2. `config_flow.py`
3. `provider.py`

### Documentation standard

For simple integrations, a single `README.md` at the integration root is
sufficient. For complex integrations (like SDK bridges or external APIs),
create a `docs/` folder containing a `docs-tree.json` — a category list the
frontend parses to generate the sub-navigation in the integration's
"Documentation" dialog:

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

- `category`: the header for the menu group.
- `id`: a unique ID for the route/page.
- `file`: the relative path to the Markdown file inside the `docs/` folder.
- `title`: the display name in the menu.

These per-extension trees are the **legacy category-list form** of the
family docs-tree contract — the chaptered form (the one this doc set
declares) requires a reader upgrade and is not what the in-app dialog
consumes today. See [Versioning & Migration](versioning-and-migration.md)
for the migration note.

## 1. `manifest.json` — the charter envelope

The manifest is validated **at registration** against the vendored family
schema (`contracts/extension-manifest.schema.json`, bundled form); an
invalid manifest never loads. Required: `apiVersion` (`"extension/v1"`),
`kindVersion`, `kind` (`connector` for outbound data flows, `bridge` when
`inbound-webhook`/`inbound-api` are declared), `id`, `version`,
`capabilities` (closed charter taxonomy), `author`, `license`, `i18n`.
The connector kind also requires `itemSchema` + `supportsIncremental`;
declaring `net` requires static `domains`; `chat-tools` requires the
`tools` list with a risk class per contribution (the three classes
`read-only` / `state-changing` / `destructive`); credential fields in
`configSchema` carry `x-secret: true` (values live only in the sealed
store; a plaintext secret in stored config is a registration-time
failure).

A minimal envelope (see any file under `builtin/*/manifest.json`):

```json
{
  "apiVersion": "extension/v1",
  "kindVersion": 1,
  "id": "smartwatch-sync",
  "kind": "connector",
  "version": "1.0.0",
  "description": "…",
  "author": "You",
  "license": "Apache-2.0",
  "categories": ["data"],
  "capabilities": ["sync", "secrets"],
  "itemSchema": {
    "type": "object",
    "properties": {"observation": {"type": "string", "title": "Biomarker observation"}}
  },
  "supportsIncremental": true,
  "configSchema": {
    "type": "object",
    "properties": {
      "api_key": {"type": "string", "x-secret": true}
    }
  },
  "i18n": {"defaultLocale": "en", "locales": {"en": {"title": "Smartwatch Sync", "summary": "…"}}}
}
```

The old flat manifests (`domain`/`name`/`integration_type`/`icon`…) were
re-authored onto the envelope — never converted (no-users window).

## 2. `config_flow.py`

Defines the dynamic UI needed to set up the integration. Must inherit from
`BaseConfigFlow`:

```python
from app.extensions.sdk import BaseConfigFlow

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

Base-class declarations (read by the engine, no per-domain endpoints):

- **`max_instances_per_user: Optional[int]`** — per-user instance cap,
  enforced generically on create. `None` = unlimited (default).
- **`get_secret_fields() -> List[str]`** — field names that are secret.
  The platform encrypts them before storage (Fernet) and masks them as
  `"***"` on read. Default: `[]`.

```python
class McpClientConfigFlow(BaseConfigFlow):
    domain = "mcp_client"
    max_instances_per_user = 5  # read from settings in practice

    def get_secret_fields(self):
        return ["env", "headers", "auth_token"]
```

## 3. `provider.py`

The core logic. Must inherit from `BaseHealthProvider`:

```python
from typing import List, Any
from app.extensions.sdk import BaseHealthProvider
from app.schemas.fhir.observation import ObservationCreate
from app.models.user_integration import UserIntegration

class NotifyProvider(BaseHealthProvider):
    domain = "notify"

    # ... See below for implementation details (Pull vs Push) ...
```

One-time init: the registry calls `setup(config)` at app startup with the
`SystemIntegration.global_config` JSONB (when present). Use it for HTTP
pool warmup, OAuth discovery, signing-key preload; failures bubble up and
exclude the integration from the active set.

## Pull data and cursors

For REST APIs that require periodic polling, implement `pull_data`. A
background Celery beat fires every 60 seconds and calls
`sync_active_integrations`; each `UserIntegration` row's own
`sync_interval` (default 15 min) gates whether the beat actually invokes
`pull_data` for that integration on a given tick.

```python
    async def pull_data(self, integration: UserIntegration) -> List[ObservationCreate]:
        last_sync = self.get_sync_cursor(integration, "last_date", default="2024-01-01")

        # Use the shared SDK HTTP helper (token-aware, full-jitter retry/backoff).
        from app.extensions.sdk import http_request
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

Cursor/state in one call pair (JSONB-safe — the base mutates
`user_config["_sync_state"]` with the `flag_modified` dance so JSONB
change detection picks it up):

```python
last_timestamp = self.get_sync_cursor(integration, "last_timestamp", default=0)
self.set_sync_cursor(integration, "last_timestamp", new_timestamp)
```

## Webhook handlers (opt-in push)

For platforms that push data to Health Assistant, implement
`handle_webhook` and declare the `inbound-webhook` capability (bridge
kind). The system provisions a unique endpoint per user instance:

`POST /api/v1/integrations/{domain}/webhook/{integration_id}`

The HMAC gate is **route-level** and machine secrets are mandatory; inside
the handler you build observations:

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

## Two-way API proxy (opt-in, bridge kind)

For integrations acting as a bridge for headless clients (browser
extension, mobile app), expose a full two-way REST API rather than a
one-way webhook. The system provisions a wildcard route:

`[GET/POST/PUT] /api/v1/integrations/{domain}/api/{integration_id}/{path}`

Implement `handle_api_request`:

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

**Schema-first surfaces:** when a bridge API grows beyond a couple of
routes, formalize it the way `health_assistant_bridge` does — the
provider's route table is extracted into a machine-readable sub-API schema
(`backend/scripts/export_bridge_schema.py`, published at
`GET /api/v1/integrations/{domain}/schema`), the client SDK model layers
regenerate from it behind a CI `--check` drift gate, and one cross-SDK
fixture set (generated from the schema) is the conformance wall. A
hand-written route table without a schema is a drift bug, not a shortcut.

## OAuth / SMART on FHIR (cloud integrations)

For integrations that connect via an OAuth2 Authorization Code flow (FHIR
servers, Fitbit, Withings, Apple Health cloud, etc.), the SDK ships a
reusable auth module in `app/extensions/sdk/auth.py`:

- **PKCE** (`generate_pkce`, `generate_state`) — stdlib-based, no extra deps.
- **SMART discovery** (`discover_smart`) — reads `/.well-known/smart-configuration`.
- **Dynamic Client Registration** (`register_client`, RFC 7591) — the user
  enters *only the server URL*; the SDK auto-registers a public client.
- **Token exchange + refresh** (`exchange_code`, `refresh_token`).
- **`OAuthTokenStore`** — persists tokens in `user_config["_oauth"]`
  Fernet-encrypted; stores connection metadata (`token_endpoint`,
  `revocation_endpoint`, `client_id`, `scope`, `patient`, `fhir_base_url`,
  `expires_at`) so refresh + revoke work later.
- **`OAuthStateStore`** — Redis-backed short-lived `state` + PKCE
  verifier, one-shot **atomic** consume via `GETDEL` (Redis ≥ 6.2) — no
  TOCTOU window for duplicate callbacks.
- **`SmartOAuth`** — composes discovery → DCR → authorize → exchange, with
  refresh-on-use (`get_live_token`), force-refresh on a 401 race
  (`force_refresh`), and best-effort token revocation on disconnect
  (`revoke`, RFC 7009). When DCR is advertised but returns no `client_id`,
  it raises an actionable typed `auth` error instead of producing a broken
  authorize URL.

The connect flow is **decoupled from the config modal** — config creates a
`PENDING` instance, then a separate Authorize action runs the OAuth
round-trip:

1. Config flow sets `is_oauth = True`. `submit_config_flow` creates the
   instance with status `PENDING` **only when its `auth_mode` is `smart`** —
   a tokenless instance (`auth_mode == "none"`) skips OAuth and goes
   straight to `ACTIVE`.
2. The provider implements `begin_oauth(integration, redirect_uri,
   extra_state=)` and `complete_oauth(integration, pending, code)`.
   For SMART, delegate to `SmartOAuth`.
3. Two generic routes (no per-domain code):
   - `POST /{domain}/oauth/start` → discover + DCR + authorize URL.
   - `GET /{domain}/oauth/callback?state=&code=` (unauthenticated, secured
     by the one-shot `state`) → exchange + encrypt tokens → `ACTIVE` → 302
     to the SPA landing.
4. The frontend shows an "Authorize" banner on `PENDING` instances.

For **inbound FHIR data** (pull), use `sdk/fhir.py` —
`fhir_search(http_client, base_url, resource_type, params, *, access_token=None)`
(tokenless when `access_token` is `None`) and
`fhir_observation_to_create(fhir_obs, tenant_id=, patient_id=)` →
`ObservationCreate` attached to the local patient. Multi-component
observations (blood pressure, panels) and `note[]` are preserved;
multi-range `referenceRange[]` is preserved as the canonical FHIR list.

For **outbound FHIR data** (push), use
`fhir_conditional_update(http_client, base_url, resource_type, body, *, search_params=, access_token=)`
— FHIR conditional update via PUT. Returns `(status, response_body)`;
`412` is returned (not raised) so the caller can treat it as "skipped".
Raises the typed hierarchy (`auth` / `rate-limited` / `protocol`) with
**OperationOutcome-parsed diagnostics** in the message
(`parse_operation_outcome(response_json)` extracts the first
`issue[].diagnostics`). Use `fhir_create(...)` for a simple POST (e.g.
remote Provenance after a push).

```python
from app.extensions.sdk import BaseHealthProvider, SmartOAuth

class MyProvider(BaseHealthProvider):
    domain = "my_oauth_integration"

    async def setup(self, config: dict | None = None) -> None:
        await super().setup(config)
        self._smart = SmartOAuth(self._http_client)

    async def begin_oauth(self, integration, redirect_uri, *, extra_state=None):
        return await self._smart.begin_connect(
            integration.user_config["server_url"], redirect_uri,
            "Health Assistant", extra_state=extra_state,
        )

    async def complete_oauth(self, integration, pending, code):
        return await self._smart.complete_connect(integration, pending, code)
```

On use, refresh-on-access: `await provider.get_live_token(integration)`
returns a valid token, refreshing first if expired. Requires `HA_DATA_KEY`
+ Redis. Reference implementation:
`builtin/fhir_server/provider.py`.

## Notifications (event-driven, rich, actionable)

Providers can emit rich event-driven notifications — threshold alerts,
HITL-style prompts, anomaly flags, daily summaries — by overriding opt-in
hooks on `BaseHealthProvider` (declare the `notify` capability). The
platform always emits the **baseline** ("synced N records" / "sync
failed"); provider-authored events come from `get_notifications`, called
after a pull sync or webhook that persisted ≥ 1 observation (never on
skipped syncs).

```python
    async def get_notifications(
        self,
        integration: UserIntegration,
        *,
        observations: List[ObservationCreate],
        context: Dict[str, Any],
    ) -> List[NotificationSpec]:
        """Inspect the just-persisted observations and return specs to emit.

        ``context`` carries ``sync_result``, ``patient_id``,
        ``integration_id`` / ``domain``. Default ``[]``. Never raise —
        swallow errors so one bad notification doesn't break the sync.
        """
        return []

    async def handle_notification_action(
        self,
        integration: UserIntegration,
        action_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle a clicked action button of ``type='post'``.
        Default: ``NotImplementedError``. Return an ``action_result(...)``
        dict; the frontend renders it like custom-action results."""
        raise NotImplementedError(...)
```

The `NotificationSpec` builder:

```python
from app.extensions.sdk import NotificationSpec
from app.extensions.sdk.display import kv_block, table_block

spec = (
    NotificationSpec.builder(
        title="Elevated heart rate detected",
        body="120 bpm observed (reference 60–100).",
        category="alert",        # any NotificationCategory value
        severity="warning",      # info | warning | critical
        type="INTEGRATION_EVENT",
    )
    .patient_id(integration.patient_id)
    .source_ref("biomarker_code", "8867-4")
    .add_link_action(
        "View trend",                       # navigate (relative URL → router)
        f"/patients/{pid}",                 # ← patient dashboard; see the deep-link gotcha
    )
    .add_post_action(
        "Acknowledge",                      # POST → handle_notification_action
        endpoint=f"/integrations/{self.domain}/notification-action/{iid}/ack",
        style="ghost",
    )
    .display_block(
        kv_block("Reading", {"value": 120, "unit": "bpm", "range": "60–100"})
    )
    .build()
)
```

| Builder method | Purpose |
|---|---|
| `body_text(s)` | Override the body |
| `patient_id(pid)` | Attach patient context (FHIR Communication auto-written for clinical categories) |
| `payload_field(k, v)` / `source_ref(k, v)` | Add keys to `payload` / `source_ref` (the platform auto-injects `integration_id` + `provider`) |
| `add_link_action(label, url, *, style)` | Button that navigates to an app URL |
| `add_post_action(label, *, endpoint, style)` | Button that POSTs to `.../notification-action/{iid}/{action_id}` |
| `add_action(NotificationAction)` | Escape hatch |
| `display_block(block)` | Attach a `kv_block` / `list_block` / `table_block` / `json_block` / `text_block` / `code_block` |
| `targets([{kind, id}])` | Override the default "integration owner only" target (`PATIENT` / `DOCTOR` / `TENANT`) |
| `digest_key(key)` | Collapse repeated emissions with the same key into one notification inside the TTL window (default 6 h, clamped `[60s, 7d]`). Recommended for anything that may fire every sync; format `"{domain}:{type_id}:{scope}"` |

**Action button contract** (the frontend renders `payload.actions[]`):

| Field | Type | Notes |
|---|---|---|
| `id` | string | Unique per notification |
| `label` | string | Button text |
| `type` | `"link"` \| `"post"` | `link` → navigate; `post` → POST to `endpoint` |
| `url` | string? | Required for `type="link"`; relative → react-router, absolute → new tab |
| `endpoint` | string? | Required for `type="post"`, always under `/integrations/{domain}/notification-action/{iid}/{action_id}` |
| `method` | string | Default `"POST"` |
| `style` | `"primary"` \| `"danger"` \| `"ghost"` \| `"default"` | Visual treatment |

**Deep-link gotcha:** the biomarker detail route is
**`/biomarkers/details/<definition-UUID>`** — not patient-scoped, and the
path segment is the biomarker *definition id* (a UUID), not the LOINC
code. A URL like `/patients/{pid}/biomarkers/details/8867-4` will 404, and
providers don't know the definition UUID at emit time. Link to the
**patient dashboard** (`/patients/{pid}`) instead — `dev_dummy`
demonstrates the pattern.

**Per-type user preferences (opt-out).** Declare the kinds you emit so
users can toggle them individually (Android channels / iOS categories
analogue):

```python
from app.extensions.sdk import NotificationTypeSpec

class MyProvider(BaseHealthProvider):
    def get_notification_types(self) -> list[NotificationTypeSpec]:
        return [
            NotificationTypeSpec(
                id="elevated_heart_rate",
                label="Elevated heart-rate alerts",
                description="Fires when a synced heart-rate reading exceeds 100 bpm.",
                category="alert",
                severity="warning",
                default_enabled=True,
                channels=("IN_APP", "PUSH"),  # advisory
            ),
        ]
```

Tag runtime specs with `.type_id("elevated_heart_rate")` to link them to
the declaration. Filtering is server-side at emit time; prefs live at
`user.settings["notifications.integration.{integration_id}.{type_id}"]` —
keyed per **instance**, not per domain (two Fitbit accounts can be muted
separately). Specs without a `type_id` always pass through.

**Default-on convention:** set `default_enabled=True` for every type.
Users opt OUT, not IN — the platform baseline is already opt-in (the
capability gate), so a declared type that defaults off is undiscoverable.
Reserve `default_enabled=False` for genuinely noisy types.

Three filter layers compose (all server-side): per-source
(`notifications.sources.INTEGRATION`), per-channel
(`notifications.channels.PUSH`), per-instance-type
(`notifications.integration.{iid}.{type_id}`). A notification fires only
if it passes **all three**. The `emit()` pipeline auto-stamps a
`payload.preferences` hint so the UI renders an inline "Turn off this
kind" button.

**Limitation:** the per-type filter keys on the integration owner's prefs.
Recipients past the owner (via `targets`) are filtered only by the
per-source + per-channel layers.

Worked example: `builtin/dev_dummy/provider.py` declares **4
NotificationTypeSpecs** covering every category/severity combination, tags
every emitted spec with its `type_id`, and implements
`handle_notification_action`.

## Tool exposure (for the chat assistant)

Declare `chat-tools` + the `tools` list (each entry with a risk class),
then implement two methods:

```python
class MyProvider(BaseHealthProvider):
    domain = "my_integration"

    async def get_tools(self, integration: UserIntegration) -> List[Any]:
        # Return LangChain StructuredTool / @tool objects.
        # Swallow per-instance errors and return [] on failure.
        return [my_tool_1, my_tool_2]
```

The platform tool aggregator (`app/ai/tools/aggregator.py`) iterates all
active extensions whose manifest declares `chat-tools` and merges
`get_tools()` results with the built-ins before `llm.bind_tools()` — no
platform code references any specific extension domain.
`INTEGRATION_MAX_TOOLS_PER_SESSION` (default 20) bounds the tool count
per chat turn. Risk classes and the destructive-confirm funnel are
[Security](security-and-gotchas.md) +
[Reference](reference.md) material.

## Custom actions (services)

Expose interactive buttons on the instance detail page:

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

**Structured results (display blocks):** return an `action_result(...)`
with a `results` array of typed blocks — the frontend renders them in a
modal; `{"message": "..."}` alone still shows just a toast.

```python
from app.extensions.sdk import kv_block, list_block, table_block, code_block, action_result

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

Available builders (from `app.extensions.sdk`, full API in
`app/extensions/sdk/display.py`): `kv_block`, `list_block`, `table_block`,
`json_block`, `text_block`, `code_block`, `action_result`.

**Input:** the action endpoint accepts an optional JSON body, spread as
kwargs into `execute_custom_action` (e.g. `{"query": "smith"}` →
`kwargs.get("query")`). No-input actions send no body. **Modals:** an
action entry may declare `"modal": "<kind>"` — the current built-in kind
is `"patient_picker"` (the `fhir_server` **Find Patient** flow, whose
action supplies `find_patient` / `select_patient` sub-actions).

## Typed errors (the charter model)

Errors are `ExtensionError` — one closed 15-code enum + capped detail
(500 chars) + closed `retryable` sub-vocabulary. The engine routes:

- `auth` — flips the instance to ERROR (reconnect prompt). Automatically
  raised by the SDK HTTP helpers on 401/403 and on OAuth-shaped 4xx
  (RFC 6749 §5.2 `error` field).
- `rate-limited` — skips the current sync cycle gracefully; exhaustion of
  429 retries raises it automatically. Carries `retry_after_seconds`
  (from the last seen `Retry-After`, both integer-seconds and HTTP-date
  forms per RFC 7231); when set, the worker writes a Redis cooldown key
  `sync_cooldown:{integration_id}` with TTL clamped `[60s, 1h]` so the
  60-second beat doesn't re-stampede a closed window. Degrades gracefully
  when Redis is down.
- `invalid` — the configuration is invalid and the user must
  **reconfigure** (a malformed SMART discovery, a DCR rejection, a secret
  key that can't decrypt a stored value) — distinct from `auth`
  (reconnect) and `protocol` (bad upstream payload).
- `net-denied` — the SSRF guard; non-retryable config class: logged, the
  instance stays ACTIVE (the historical SSRFBlock semantics, preserved).

Raise it explicitly when you need to instruct the engine:

```python
from app.extensions.sdk import ExtensionError

raise ExtensionError(
    "rate-limited",
    "Upstream throttled us",
    retry_after_seconds=120,
)
```

## Secret encryption (at rest)

Integrations with secret config fields declare them via
`get_secret_fields()` + `x-secret` marks. The SDK handles the rest:

- **On save:** `submit_config_flow` → `config_flow.prepare_for_storage()`
  → encrypts secret fields with Fernet (`HA_DATA_KEY`).
- **On read:** `get_integration_details` → `prepare_for_read()` → masks
  secret fields as `"***"`.
- **On use:** the provider calls `config_flow.decrypt_for_use()` or
  `decrypt_fields()` to get plaintext.

The cipher is not SDK-local: `app.extensions.sdk.secrets` re-exports
`SecretCipher` from the shared `neuronection-auth-kit` package
(`nx_auth.atrest`) — the same audited at-rest implementation across the
Neuronection assistants, pinned by git SHA. The ciphertext format
(`{"_encrypted": ..., "_kid": ...}` wrappers, rotation ring, context
binding) is unchanged.

No per-domain code in the endpoint. Set `HA_DATA_KEY` in `.env` (Fernet
key, base64 32 bytes):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If the key is missing, saving config with secret fields fails fast with a
400 — no plaintext secrets are ever stored.

**Platform-provisioned machine secrets:** when a provider implements
`handle_webhook` / `handle_api_request`, instance creation automatically
generates `webhook_secret` / `api_secret` (`secrets.token_urlsafe(32)`),
stores them as context-bound encrypted wrappers inside `user_config`, and
returns the plaintexts **once** in the config-flow response. Senders and
clients must sign with them; unsigned machine traffic is rejected.

**Key rotation** (non-disruptive): set `HA_DATA_KEY` to the new key and
`HA_DATA_KEY_PREVIOUS` to the old (comma-separated for several). New
values encrypt with the new key; existing ciphertext decrypts with either.
Each encrypted value carries a short `_kid` fingerprint so a rotation
migration can find rows still needing re-encryption. A `context` arg
(typically the integration id) binds a ciphertext to its row so it can't
be cut-and-pasted elsewhere in multi-tenant JSONB. The full runbook —
including the data-loss gate (only drop a key from
`HA_DATA_KEY_PREVIOUS` after a `_kid` census shows zero remaining values
sealed under it) — lives in the kit's `docs/atrest.md`.

## Payload debugging

Decipher an undocumented third-party API or a webhook by dumping raw
payloads into a dedicated debug database viewable in the integration's
Debug Console UI. The platform provides the **Debug Mode OFF/ON** toggle
per connected instance — no settings code needed:

```python
# Automatically checks integration.is_debug_enabled before saving
await self.log_debug_payload(integration, "API Response", raw_json_data)
```

## Clinical events & examinations (write hooks)

Providers that sync longitudinal data — hospital admissions, chronic
conditions, pregnancies (clinical events) or lab encounters, imaging
appointments, hospital visits (examinations) — pull those records
alongside observations via two opt-in hooks. The engine resolves a
service-context actor from the integration's owning user, validates and
writes each record through the canonical service
(`clinical_event_service.create_event` / `examination_service.create_examination`),
and dedups across syncs when the provider sets the upstream stable id.

```python
class MyProvider(BaseHealthProvider):
    domain = "my_ehr"

    # --- Clinical events (FHIR Condition / EpisodeOfCare) ---

    async def pull_clinical_events(
        self, integration: UserIntegration
    ) -> List[ClinicalEventCreate]:
        # Set external_id on each payload to the upstream's stable
        # encounter/episode id so the engine dedups across syncs.
        ...

    # --- Examinations (FHIR Encounter) ---

    async def pull_examinations(
        self, integration: UserIntegration
    ) -> List[ExaminationCreate]:
        ...
```

Both `ClinicalEventCreate` and `ExaminationCreate` are re-exported from
`app.extensions.sdk`.

**Dedup contract (set `external_id`):** the engine looks up an existing
record by `(tenant_id, patient_id, source_integration_id, external_id)`
and returns it as-is on a hit; a partial unique index at the DB layer
catches the SELECT→INSERT race so concurrent syncs can't double-insert.
`external_id` is the provider's job (the upstream's stable id);
`source_integration_id` is **not** on the payload schema — the engine
always supplies it, so providers can't fake their provenance.

For examinations the service also runs a heuristic UI dedup (date +
category + notes) for interactive users, but that path is bypassed
entirely for integration-sourced writes — an integration-sourced exam
can't accidentally match an unrelated UI row.

**What the engine does per record:**

1. Resolves a service-context `TokenData` from the integration's owning
   user (`app/services/integration_actor.py`) — the integration inherits
   its owner's tenant, role, and user_id: same RBAC, same audit
   provenance, same tenant scoping as an interactive UI request. No
   "integration user" service account is created.
2. Calls the canonical write service (patient validation, category-text →
   concept resolution, dedup, ORM construction, doctor/category linking,
   audit columns, commit).
3. Logs per-record failures without aborting the sync.

`records_synced` in the `IntegrationSyncLog` includes observations plus
pulled events/exams.

| Use case | Hook |
|---|---|
| Lab integration grouping biomarker results under a single lab panel | `pull_examinations` |
| Hospital EHR syncing admission / discharge / visit records | `pull_examinations` |
| Chronic-condition tracking (ongoing pregnancy, long-term pain) | `pull_clinical_events` |
| Hospital EHR syncing problem list (Conditions) | `pull_clinical_events` |
| Wearable pulling heart-rate / steps telemetry | neither — return `ObservationCreate` from `pull_data` |

The two hooks are independent. Reference implementation: the bridge
provider's `_process_and_save_sync_data` builds `ExaminationCreate`
payloads and calls `examination_service.create_examination` directly from
its two-way API handler; the same call shape applies inside `run_sync`.

## Catalog proposals (auto-applied)

Providers that discover new catalog entries upstream — a wearable
integration that adds a "Sleep Quality Score" metric, a hospital
integration that knows a disease↔biomarker mapping — contribute them via
the catalog-proposal hook. The engine **auto-applies** each proposal
through the canonical catalog write path
(`catalog_proposal_service.apply_proposal`).

```python
from app.extensions.sdk import (
    BaseHealthProvider, CatalogProposal,
    biomarker_proposal, medication_proposal,
    concept_proposal, edge_proposal,
)

class MyProvider(BaseHealthProvider):
    domain = "wearable_sync"

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

`CatalogProposal` is a Pydantic spec with a `kind` discriminator
(`"biomarker"` / `"medication"` / `"concept"` / `"edge"`) + a `payload`
dict; four typed constructors build it without hand-writing the dict.

| `kind` | Payload fields |
|---|---|
| `biomarker` | `name`, `slug` (optional — derived from name), `category`, `coding_system`, `code`, `preferred_unit_symbol`, `reference_range_min/max`, `aliases`, `info`, `is_telemetry` |
| `medication` | `name`, `description`, `indications`, `dosage_info`, `contraindications`, `side_effects` |
| `concept` | `slug`, `name`, `kind` (a `ConceptKind` value like `"disease"` / `"body_system"`), `description`, `coding_system`, `code`, `aliases` |
| `edge` | `src_type`/`src_id`/`dst_type`/`dst_id`/`relation` (enum values), optional `properties` + `evidence` |

**Provenance** stamped by the engine: `created_by` = the owning user;
`scope` / `tenant_id` from the actor's role (USER → USER scope, ADMIN →
TENANT, SYSTEM_ADMIN → SYSTEM); `ConceptProvenance.INTEGRATION` on
`ConceptEdge.source`; `meta_data["_provenance"] = "integration"` on
`BiomarkerDefinition` (no dedicated column).

**Idempotency** — re-applying is a no-op: `biomarker` on `slug`
(globally unique), `medication` on `(tenant_id, name)`, `concept` on
`slug`, `edge` on `(src_type, src_id, dst_type, dst_id, relation)`. Just
keep emitting; the engine dedups.

**Per-sync cap** — `INTEGRATION_MAX_PROPOSALS_PER_SYNC = 50`;
excess dropped with a warning (module-level today; env-ification on the
roadmap). **Ordering matters for edges** — `create_edge` validates that
source + destination concepts exist, so a concept you propose must come
**before** the edge that references it, in the returned list.
**Role limitation** — concept/edge writes require role ADMIN or higher; a
USER-role owner can still contribute biomarkers and medications but
concept/edge proposals fail (the engine logs and skips).

## HITL proposals (human review)

The infrastructure proposal queue is the human-in-the-loop counterpart to
the auto-applied hook: same payload shapes, same write path — but instead
of auto-applying, the platform **queues for human review** and only an
approval fires the canonical write. Use auto-apply for entries you're
confident about (a known LOINC code the local catalog misses); use this
for entries needing human judgement (a novel biomarker mapped to an
existing class, a typed concept edge inferred from upstream data).

```python
from app.extensions.sdk import (
    BaseHealthProvider,
    biomarker_hitl_proposal, medication_hitl_proposal,
    concept_hitl_proposal, edge_hitl_proposal,
    ProposalOutcome,
)

class MyProvider(BaseHealthProvider):
    domain = "hospital_ehr"

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
        # cursor so you don't re-propose, log audit, suppress duplicates.
        # Default is a no-op; safe to leave unimplemented.
        if outcome.error is None:
            self.set_sync_cursor(
                integration, "last_approved_proposal", str(proposal_id)
            )
```

`IntegrationProposalSpec` uses a `proposal_type` discriminator
(`create_biomarker_definition` / `create_medication_definition` /
`create_concept` / `create_edge`) mirroring the chat-side AI HITL
`task_type` strings.

**State machine + persistence.** Each spec persists as an
`IntegrationProposal` row (status `PROPOSED`):

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

`approve` routes the (possibly user-edited) payload through the same
`apply_proposal` write path — `CONFIRMED` on success, `FAILED` on apply
error (the row stays for retry/re-review). `reject` / `cancel` →
`DISMISSED`, no apply; distinguishable in audit by the `note`.
Re-resolve from a terminal state returns **409** (idempotent contract).

**Dedup:** `dedup_key = sha256(canonical_json({"type": proposal_type,
"payload": proposed_payload}))` per spec, partial unique index on
`(integration_id, dedup_key)`. Re-emitting the same spec is a no-op —
even after a decision, the engine doesn't re-spam the inbox. Providers
wanting stronger don't-re-propose semantics advance their own cursor in
`handle_proposal_resolution`.

**Resolver endpoints:**

| Route | Auth | Purpose |
|---|---|---|
| `GET /api/v1/integrations/instance/{id}/proposals?status=&limit=&offset=` | user (owner) | List proposals |
| `GET /api/v1/integrations/instance/{id}/proposals/{proposal_id}` | user (owner) | Fetch one |
| `POST /api/v1/integrations/instance/{id}/proposals/{proposal_id}/resolve` | ADMIN+ to `approve`; USER may `reject`/`cancel` | Resolve |

The resolve body is
`{action: "approve"|"reject"|"cancel", payload?: Dict, note?: str}` — the
optional `payload` overrides `proposed_payload` on approve. Response:
`status`, `resolved_payload`, `applied_entity_id` (on successful approve),
`error` (on FAILED).

**Per-sync cap + notification:**
`INTEGRATION_MAX_HITL_PROPOSALS_PER_SYNC = 20`. For each *newly-inserted*
row the engine fires an `INTEGRATION / HITL_TASK / hitl / warning`
notification with an action button to the proposals view.
**USER role can list + view but not approve** (catalog writes require
ADMIN+) — 403 so the UI can hide the buttons.

## Document pull

Providers that can deliver document bytes from upstream — scanned lab
reports, fax-to-email PDFs, ECG printouts — hand them to the OCR + LLM
extraction pipeline via the documents hook (declare the `document` data
class):

```python
from app.extensions.sdk import BaseHealthProvider, DocumentPull

class MyProvider(BaseHealthProvider):
    domain = "hospital_portal"

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
                external_id=report.accession_number,  # upstream stable id — enables DB-layer dedup
                include_in_extraction=True,
            )
            for report in reports
        ]
```

`DocumentPull` fields: `filename` (medical-types allowlist — PDF, images,
DICOM, plain text), `content` (raw bytes), `content_type` (informational),
`examination_external_id` (links to an exam pulled in the *same* sync —
misses are non-fatal, the document is created unlinked),
`category_concept_slug` (resolves to a catalog concept; misses
non-fatal), `external_id` (upstream stable id — DB-layer dedup), and
`include_in_extraction` (default `True` — fires the OCR + LLM Celery
task).

**What the engine does:** cap check
(`INTEGRATION_MAX_DOCS_PER_SYNC = 20` items,
`INTEGRATION_MAX_DOC_BYTES_PER_SYNC = 50 MiB` running total — dropped
items log a warning), exam + category resolution, then
`document_service.ingest_document_bytes` — the **same canonical
ingestion path the UI upload endpoint uses** (`HA_UPLOAD_DIR/<tenant_id>/`,
`owner_id` = the integration's owning user, best-effort OCR dispatch).
A document pulled by an integration is indistinguishable in the UI from
one uploaded by the user; provenance is preserved on the row
(`source_integration_id` + `external_id`, surfaced in the FHIR projection
via `meta.tag[]`, system `urn:health-assistant:document-provenance`).

**Idempotency:** `DocumentModel` carries the provenance columns +
partial unique index `uq_document_integration_dedup` — with
`external_id` set, a re-pull is a no-op (no duplicate file write, no
re-OCR). Without a stable upstream id, use cursor dedup:

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

**Storage + size:** local disk only (`HA_UPLOAD_DIR`); a provider
returning 100 MB of PDFs in one batch holds them all in memory — the
50 MiB per-sync cap mitigates the worst case; chunk large documents
across syncs. Extension allowlist: `.pdf`, PNG/JPG/BMP/WebP/TIFF/GIF,
`.dcm`, `.txt`, `.md` — others raise `HTTPException(400)`.

## Medications, allergies & immunizations

Mirrors the write hooks for the patient-instance treatment resources. A
provider that can pull `MedicationStatement` / `MedicationRequest`,
`AllergyIntolerance`, or `Immunization` overrides the matching pull
hooks; the engine writes through the canonical services
(`medication_service.add_patient_medication`,
`allergy_service.add_patient_allergy`,
`vaccine_service.add_patient_immunization`).

```python
from app.extensions.sdk import (
    BaseHealthProvider, MedicationRecordCreate, AllergyIntoleranceCreate,
    PatientImmunizationCreate,
)

class MyEhrProvider(BaseHealthProvider):
    domain = "my_ehr"

    async def pull_medications(self, integration) -> List[MedicationRecordCreate]: ...

    async def pull_allergies(self, integration) -> List[AllergyIntoleranceCreate]: ...

    async def pull_immunizations(self, integration) -> List[PatientImmunizationCreate]: ...
```

**Dedup:** same as the events/exams contract — set `external_id` to the
upstream's stable id; the engine stamps `source_integration_id`; a
partial unique index catches races and re-syncs are no-ops.

**Medication intent:** `MedicationRecordCreate.intent` discriminates the
single `fhir_medications` row: `statement` (default, from a
`MedicationStatement`) or `order` (from a `MedicationRequest`) — each
projects back to the right FHIR resource on the R4 facade. Set it
explicitly when importing a request.

## Webhook signature verification (reusable helpers)

The platform provisions two machine routes per instance — a webhook
receiver and a wildcard two-way API proxy. **HMAC secrets are mandatory**:
every webhook/API-capable instance is auto-provisioned with a per-instance
secret at creation (Fernet-encrypted with the instance id as context
binding, shown **once** in the config-flow response — like an OAuth
client secret). The integration UUID is an identifier, never a
credential; secret-less instances are rejected 401 on both routes.
Request bodies are capped at 40 MiB.

The algorithm lives in one reusable SDK module —
`app.extensions.sdk.webhook_security` (three pure functions, stdlib-only,
no `app.*` imports — the SDK must not depend on the backend):

```python
from app.extensions.sdk import (
    verify_hmac_signature,
    verify_canonical_signature,
    verify_stripe_signature,
    get_signature_header,
    DEFAULT_WEBHOOK_SIGNATURE_HEADERS,
    STRIPE_SIGNATURE_HEADERS,
)
```

| Function | Purpose |
|---|---|
| `verify_hmac_signature(secret, raw_body, provided_signature, *, accepted_prefixes=("sha256=", "sha256:"))` | Constant-time HMAC-SHA256 of `raw_body`. Strips the conventional GitHub/Slack prefixes. Returns `False` on empty or non-ASCII inputs. **No replay protection**. |
| `verify_stripe_signature(secret, payload, header_value, *, tolerance=300)` | Parses Stripe's `t=<epoch>,v1=<hex>` header, folds the timestamp into the MAC, checks a ±`tolerance` skew window — the replay-protected counterpart. |
| `verify_canonical_signature(secret, method, path, raw_body, provided_signature, *, provided_timestamp=None, max_skew_seconds=300)` | Constant-time HMAC-SHA256 over `METHOD\n<path>\n[<timestamp>\n]<raw_body>`. When `provided_timestamp` is set, the timestamp is folded into the signed payload (replay defense) and skew beyond `max_skew_seconds` is rejected. Used by the two-way API proxy (`api_secret`) and by the webhook route when the sender supplies `X-Webhook-Timestamp`. **Route-level enforcement:** the API proxy requires `X-Api-Timestamp` (401 without it) and signs the full `path?query` so query params are tamper-proof. |
| `get_signature_header(headers, *, names=...)` | Case-insensitive lookup over the conventional names (`X-Webhook-Signature`, `X-Webhook-Signature-256`, `X-Hub-Signature-256`); providers with their own convention (e.g. dev_dummy's `X-DevDummy-Signature`) pass `names=` explicitly. |

Provider-side example:

```python
from app.extensions.sdk import verify_hmac_signature, get_signature_header
from app.extensions.sdk import ExtensionError

async def handle_webhook(self, integration, payload, request=None):
    secret = self._resolve_webhook_secret(integration)  # decrypt user_config["webhook_secret"]
    if secret:
        signature = get_signature_header(
            request.headers, names=("X-MyService-Signature",),
        )
        if not signature:
            raise ExtensionError("protocol", "Missing signature header.")
        body = await request.body()
        if not verify_hmac_signature(secret, body, signature):
            raise ExtensionError("protocol", "Invalid signature.")
    # ... build observations from payload ...
```

The platform endpoint layer's verifiers are thin wrappers around the same
SDK functions — providers, the webhook route and the API proxy route share
one canonical implementation.

**Other schemes:** `accepted_prefixes` covers GitHub/Slack/Stripe-style
prefixed HMAC. AWS SigV4 (request-scoped canonicalisation) and asymmetric
schemes (JWS) are not provided — implement them inline in your provider
if your upstream requires them.

## Building FHIR observations

Use the `ObservationBuilder` to map raw third-party data into FHIR
compliant objects.

### Supported coding systems

`set_biomarker()` defaults to LOINC; specify explicitly when needed:

* **`CodingSystem.LOINC`** (default) — standard clinical laboratory
  measurements and standard vital signs.
* **`CodingSystem.SNOMED`** — clinical terms and findings.
* **`CodingSystem.CUSTOM`** — **crucial for wearables/IoT**: proprietary
  upstream identifiers (Apple's `HKQuantityTypeIdentifierStepCount`,
  Garmin's internal metric keys) so they never collide with a real
  clinical code.

```python
builder = self.create_observation_builder(integration)

obs = (
    builder
    .set_biomarker("8867-4", "Heart rate") # Defaults to LOINC code
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

> **You don't interact with TimescaleDB.** Return `ObservationCreate`
> objects for all data. The core sync engine checks the matched Biomarker
> Definition's `is_telemetry` flag — `true` routes the observation to the
> `telemetry_data` hypertable, everything else persists as a standard
> FHIR observation.

### Categorical / string values (`set_value_string`)

FHIR R4 §3.1.1 allows exactly one `value[x]` per observation, so
`set_value` (numeric → `valueQuantity`) and `set_value_string`
(categorical → `valueString`) are **mutually exclusive**: the last setter
wins, the other slot is cleared. The builder leaves `raw_value` /
`normalized_value` / `relative_score` unset for categorical observations
— downstream analytics must not plot a string on a numeric axis.

```python
obs_categorical = (
    builder
    .set_biomarker("94500-6", "SARS-CoV-2 PCR", coding_system=CodingSystem.LOINC)
    .set_value_string("POSITIVE")    # emits valueString, omits valueQuantity
    .set_effective_date(timestamp_obj)
    .build()
)
```

> **STATE biomarkers:** if the matched biomarker has `value_type=state`,
> use `set_value_codeable_concept(code, system, display?)` instead — the
> hard validator rejects `value_string` on STATE biomarkers; only
> `valueCodeableConcept` with a coding from the biomarker's
> `allowed_states` set is accepted. See `docs/STATE_BIOMARKERS.md`.

```python
obs_state = (
    builder
    .set_biomarker("94500-6", "SARS-CoV-2 PCR", coding_system=CodingSystem.LOINC)
    .set_value_codeable_concept(
        code="POS",
        system="http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
        display="Positive",
    )
    .set_effective_date(timestamp_obj)
    .build()
)
```

## Enabling your integration

New integrations are enabled by default (the admin-curated host profile),
but a SYSTEM_ADMIN can disable/enable explicitly. Via the admin endpoints
(`POST /api/v1/integrations/admin/{domain}/enable|disable`) — or,
historically, directly via SQL on a deployment:

```sql
INSERT INTO system_integrations (domain, is_enabled)
VALUES ('notify', true)
ON CONFLICT (domain) DO UPDATE SET is_enabled = true;
```
