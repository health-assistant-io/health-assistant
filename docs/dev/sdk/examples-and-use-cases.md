# Examples & Use Cases

Five recipes, each with the four-part frame: **when to use**, **when NOT
to use**, **complete code** (runs as written), **what to be careful at**.
The reference implementations are the bundled extensions; the
[Quickstart](quickstart.md) transcript shows the dev-dummy surface live.

## Recipe 1 — dev-dummy: the full-surface walkthrough

### When to use

You are learning the SDK, or you are about to opt into a capability family
(notifications, write hooks, documents, tools) and want to see all moving
parts wired together before writing your own provider.

### When NOT to use

Any real integration: dev-dummy synthesizes data (random values, a fake
mood, a canned demo report) and exists purely as the contract test-bed.
Fork it for the *structure*, never run it in production. Do not copy its
"id returns `[]` until `X`" toggles as a permission model either.

### Complete code

No code to write — it ships. The walkthrough (all runnable):

```bash
# 1. Connect (the quickstart §5 does this over curl; here's the UI path):
#    Patients → <patient> → Integrations → Developer Dummy Data.
#    Pick a name, sync interval, and the capability toggles:

{
  "generate_heart_rate": true,     # telemetry observations
  "generate_blood_pressure": true, # FHIR observations (two-component)
  "generate_weight": true,         # FHIR observation
  "generate_mood": true,           # categorical observation (set_value_string)
  "enable_clinical_events": true,  # one flu-episode demo event, dedup-keyed
  "enable_examinations": true,     # one annual-checkup demo exam
  "enable_catalog_proposals": true,# auto-applies the sleep-quality biomarker
  "enable_hitl_proposals": true,   # queues the stress-index concept for review
  "enable_documents": true         # one text "lab report" once per instance
}

# 2. Hit "Sync now" (POST /api/v1/integrations/instance/{id}/sync).
#    Observed unit transcript (§ quickstart): 5 pulled → 5 synced:
#    4 FHIR observations + 1 telemetry row (heart rate), the exam,
#    the clinical event, the document, the auto-applied biomarker
#    proposal, and one queued HITL concept proposal. Notifications fire
#    whenever thresholds breach (HR > 100, BP ≥ 130/85, glitch > 200).

# 3. "Show Status" custom action prints the live cursor + capability list;
#    "Reset Sync Cursor" clears _sync_state so history repeats.
```

The notification round-trip it demonstrates: four
`NotificationTypeSpec` declarations covering every category/severity
combination, every emitted spec tagged with its `type_id`, POST action
buttons routed through `handle_notification_action`, and the patient
dashboard deep-link (never the biomarker detail route —
[gotcha](authoring-and-configuration.md#notifications-event-driven-rich-actionable)).

### What to be careful at

- The toggles are per-instance demo switches — the engine has no
  per-domain branches; everything is declared capability + hook override.
- Senders of webhooks must sign (`X-DevDummy-Signature` when
  `webhook_secret` is set); the show-once secrets from the config-flow
  response are the only plaintext copy.
- Tool exposures need `langchain-core` installed (they no-op otherwise).

## Recipe 2 — a webhook receiver with HMAC verification

### When to use

Receiving server-to-server pushes (a wearable companion, a tasker, an
alert service) into Health Assistant.

### When NOT to use

If you need the *client* to also read data back, that's the two-way API
([recipe 5](#recipe-5--the-bridge-as-the-mobile-headless-transport)). If
you push standard FHIR from a SMART server, use
[recipe 3](#recipe-3--a-smart-on-fhir-sync-pull-and-push).

### Complete code

```
backend/app/extensions/builtin/hbpulse/
├── manifest.json
├── config_flow.py
└── provider.py
```

`manifest.json`:

```json
{
  "apiVersion": "extension/v1",
  "kindVersion": 1,
  "kind": "bridge",
  "id": "hbpulse",
  "version": "1.0.0",
  "description": "Receives signed heart-rate webhooks from the HBPulse companion service.",
  "author": "You",
  "license": "Apache-2.0",
  "categories": ["data"],
  "capabilities": ["inbound-webhook", "secrets", "state"],
  "configSchema": {
    "type": "object",
    "properties": {
      "webhook_secret": {"type": "string", "x-secret": true}
    }
  },
  "i18n": {"defaultLocale": "en",
           "locales": {"en": {"title": "HBPulse", "summary": "Signed heart-rate webhooks."}}}
}
```

`config_flow.py`:

```python
from typing import Any, Dict

from app.extensions.sdk import BaseConfigFlow


class HBPulseConfigFlow(BaseConfigFlow):
    domain = "hbpulse"

    async def get_schema(self) -> dict:
        return {
            "step_id": "user_config",
            "title": "Connect HBPulse",
            "data_schema": {
                "type": "object",
                "properties": {
                    "instance_name": {"type": "string", "title": "Instance Name"},
                },
            },
        }

    async def validate_input(self, user_input: dict) -> dict:
        if not user_input.get("instance_name"):
            raise ValueError("Instance name is required.")
        return user_input
```

(The endpoint auto-generates and seals `webhook_secret` at creation —
you don't add it to the schema by hand.)

`provider.py`:

```python
from typing import Any, Dict, List

from app.extensions.sdk import (
    BaseHealthProvider,
    ObservationCreate,
    get_signature_header,
    verify_hmac_signature,
)
from app.extensions.sdk import ExtensionError
from app.models.user_integration import UserIntegration


class HBPulseProvider(BaseHealthProvider):
    domain = "hbpulse"

    async def handle_webhook(
        self, integration: UserIntegration, payload: Any, request: Any = None
    ) -> List[ObservationCreate]:
        config = integration.user_config or {}
        secret = _decrypt_webhook_secret(self, integration, config.get("webhook_secret"))
        if not secret:
            raise ExtensionError("invalid", "No webhook secret configured.")

        signature = get_signature_header(
            request.headers, names=("X-HBPulse-Signature",)
        )
        if not signature:
            raise ExtensionError("protocol", "Missing signature header.")
        body = await request.body()
        if not verify_hmac_signature(secret, body, signature):
            raise ExtensionError("protocol", "Invalid signature.")

        builder = self.create_observation_builder(integration)
        return [
            builder
            .set_biomarker("8867-4", "Heart rate")
            .set_value(float(item["value"]), "bpm", "{beats}/min")
            .build()
            for item in payload["readings"]
        ]
```

For the `_decrypt_webhook_secret` helper, copy dev-dummy's
`_resolve_webhook_secret` (Fernet decrypt of the context-bound wrapper;
`config_flow.decrypt_for_use()` does the same for declared secret
fields).

### What to be careful at

- `verify_hmac_signature` has **no replay protection** — if a captured
  payload could be replayed, require the sender to timestamp and use
  `verify_canonical_signature(..., provided_timestamp=..., max_skew_seconds=300)`
  (the API-proxy shape) or Stripe-style signatures.
- Consumer headers: your upstream's header name goes into
  `get_signature_header(names=...)`.
- The route is uuid-per-instance; the HMAC secret is what authenticates.
- 40 MiB body cap; big batched payloads get rejected at the gate.

## Recipe 3 — a SMART on FHIR sync (pull and push)

### When to use

Syncing with a hospital EHR or research FHIR endpoint that speaks SMART
App Launch (authorization code + PKCE with dynamic client registration),
including tokenless local servers (e.g. vanilla HAPI without the SMART
module).

### When NOT to use

Push-only devices without FHIR (webhooks:
[recipe 2](#recipe-2--a-webhook-receiver-with-hmac-verification)); headless
mobile pushes (recipe 5). If you want an existing implementation, just
enable the bundled `fhir_server` — this recipe is the skeleton it follows.

### Complete code

Skeleton provider (the full reference is
`backend/app/extensions/builtin/fhir_server/provider.py`):

```python
from typing import Any, Dict, List

from app.extensions.sdk import (
    BaseHealthProvider, ObservationCreate,
    SmartOAuth,
    fhir_conditional_update, fhir_create, fhir_observation_to_create, fhir_search,
)
from app.extensions.sdk.fhir import parse_operation_outcome
from app.models.user_integration import UserIntegration


class MySmartProvider(BaseHealthProvider):
    domain = "hospital_fhir"

    async def setup(self, config: dict | None = None) -> None:
        await super().setup(config)
        self._smart = SmartOAuth(self._http_client)

    async def begin_oauth(self, integration: UserIntegration, redirect_uri: str, *, extra_state=None):
        return await self._smart.begin_connect(
            integration.user_config["server_url"], redirect_uri,
            "Health Assistant", extra_state=extra_state,
        )

    async def complete_oauth(self, integration: UserIntegration, pending, code: str):
        return await self._smart.complete_connect(integration, pending, code)

    async def pull_data(self, integration: UserIntegration) -> List[ObservationCreate]:
        token = await self.get_live_token(integration)  # refresh-on-expiry
        search = await fhir_search(
            self._http_client,
            integration.user_config["server_url"],
            "Observation",
            params={"code": "http://loinc.org|2951-2", "_sort": "-date", "_count": "100"},
            access_token=token,
        ) if token else await fhir_search(
            self._http_client,
            integration.user_config["server_url"],
            "Observation",
            params={"code": "http://loinc.org|2951-2", "_sort": "-date", "_count": "100"},
        )
        return [
            fhir_observation_to_create(
                entry["resource"],
                tenant_id=integration.tenant_id,
                patient_id=integration.patient_id,
            )
            for entry in search.get("entry", [])
        ]

    async def push_data(self, integration: UserIntegration, data: Any) -> None:
        """The engine's push leg: push LOCAL observations out to the
        FHIR server as conditional updates (the tokenless case signs
        nothing and sends access_token=None)."""
        token = await self.get_live_token(integration)
        for obs in await self._load_push_candidates(integration):
            body = _observation_to_fhir(obs)  # your FHIR-shaping mapper
            status, out = await fhir_conditional_update(
                self._http_client,
                integration.user_config["server_url"],
                "Observation",
                body,
                search_params={"identifier": obs.id},
                access_token=token,
            )
            if status == 412:
                continue  # precondition-failed = "skipped", not an error
```

Declare in the manifest: `capabilities: ["sync", "push", "secrets"]`,
`itemSchema: {"observation": ...}`, and a `configSchema` with
`server_url` + an `auth_mode` enum (`smart` / `none`) — the endpoint
creates `PENDING` for `smart` and `ACTIVE` for tokenless `none`.
Requires `HA_DATA_KEY` + Redis (`REDIS_URL`) for the state store.

### What to be careful at

- The OAuth round-trip is **decoupled** from config save: an instance
  starts `PENDING` and only flips `ACTIVE` after
  `GET /{domain}/oauth/callback` consumes the one-shot `state`.
- Multi-component observations (blood pressure panels) must map into
  correctly-shaped payloads — copy the mapping from
  `builtin/fhir_server/mappers.py` instead of hand-rolling it.
- `fhir_conditional_update` returns `412` rather than raising — code a
  "skipped" branch, don't let a 412 mark a sync failed.
- Refresh tokens live sealed in `user_config["_oauth"]` — treat the
  config as secret-bearing (never log provider configs raw).

## Recipe 4 — an MCP client connection

### When to use

You want an external tool server's tools callable from the Health
Assistant chat assistant — models, calculators, knowledge bases, device
control. A user connects per instance; tool calls ride the *chat*
endpoint.

### When NOT to use

Data integration: MCP Client produces **no** FHIR observations and never
syncs on a schedule (connections are lazy + idle-timed). If the goal is
data → use recipes 2/3/5.

### Complete code

The extension is bundled — the recipe is configuration. An instance's
config keys (`builtin/mcp_client/provider.py::get_schema`):

```json
{
  "config": {
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
    "request_timeout": 30,
    "verify_ssl": true,
    "env": {"FS_TOKEN": "x"},
    "enabled_tools": [], "disabled_tools": [],
    "include_tags": [],  "exclude_tags": []
  }
}
```

Transport choice (the extension's own docs):

| Transport | Use case | Notes |
|---|---|---|
| `stdio` | Local subprocess | Command must be in `MCP_STDIO_ALLOWED_COMMANDS`. |
| `http` | Remote Streamable HTTP (recommended remote) | `https://` enforced unless `MCP_ALLOW_INSECURE_HTTP=True`. |
| `sse` | Legacy SSE remote | Same as HTTP. |

Tools land namespaced `mcp__<instance>__<tool>`; the chat assistant calls
them through the authenticated chat endpoint (all tool calls go through
the authenticated chat endpoint — the tokenless proxy refuses
`handle_api_request`). The **Test Connection** custom action surfaces
connectivity state.

### What to be careful at

- Secret env fields are `x-secret`-sealed (`env`, `headers`, `auth_token`)
  — an `HA_DATA_KEY` must be set, or saving fails fast with a 400.
- Caps: `MCP_MAX_SERVERS_PER_USER=5`, `MCP_MAX_TOTAL_STDIO=20`,
  `MCP_REQUEST_TIMEOUT=30`, `MCP_TOOL_RESULT_MAX_BYTES=65536`.
- Trust is graded like the family T3 perimeter for *remote* servers: the
  remote's tools are its own code — descriptions and results can carry
  prompt injection, which the caps + sanitization bound but don't review.

## Recipe 5 — the bridge as the mobile/headless transport

### When to use

A mobile app, browser extension or backend scraper needs a *two-way*
connection: push records/examinations/documents up and read the bound
patient's clinical data back — exactly what the official Android
companion does.

### When NOT to use

Single-direction platform→device signals (pure webhooks:
[recipe 2](#recipe-2--a-webhook-receiver-with-hmac-verification)); FHIR
server syncs (recipe 3). Don't add a second bridge-style proxy per
project — fork/extend the bridge's surface instead, schema-first.

### Complete code

Enable the bundled `health_assistant_bridge`, create an instance
(show-once `api_secret`), then use an official client SDK — Python
(`pip install -e integrations/health-assistant-bridge/python-sdk`):

```python
from health_assistant_bridge import HealthAssistantBridgeClient

client = HealthAssistantBridgeClient(
    base_url="https://ha.example",
    integration_id="<instance uuid>",
    api_secret="<shown once at instance creation>",
)

status = client.get_status()
print(status.cursor, status.bridge_features)
```

Push one reading, then read it back:

```python
from datetime import datetime, timezone
from health_assistant_bridge import SyncPayload, ClientRecord

resp = client.sync_data(SyncPayload(
    client_version="1.4.0",
    source_system="python_quickstart",
    records=[ClientRecord(
        type="quantitative", code="8867-4", coding_system="loinc",
        name="Heart Rate", value=64.0, unit="bpm",
        timestamp=datetime.now(timezone.utc).isoformat(),
        performer="Demo Watch",
    )],
))

from health_assistant_bridge import AsyncHealthAssistantBridgeClient  # pooled httpx twin
```

One streaming chat turn over the signed raw path (the Python SDK has no
typed chat helpers — TS has; both sign identically):

```python
import httpx, json
from health_assistant_bridge import sign_request

body = json.dumps({"message": "Summarize my latest lab results."}).encode()
headers = {"Content-Type": "application/json", **sign_request(
    client.api_secret, "POST", "/ai/chat/stream", body,
)}
with httpx.Client(timeout=None) as sse:
    with sse.stream("POST", f"{client.api_url}/ai/chat/stream",
                    headers=headers, content=body) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                frame = json.loads(line[6:])
                if frame.get("event") == "delta":
                    print(frame.get("text", ""), end="")
```

Signed routing: every request is signed over
`METHOD\n<path>\n<timestamp>\n<raw_body>` where `path` is the part
*after* the integration id (`/sync`, `/observations/latest` — the query
string included); timestamps skew ±5 min; the SDKs never re-serialize the
signed bytes.

TypeScript and Kotlin mirrors: `npm install @health-assistant/bridge-client`
(exposes typed `chat` / `chatStream` / `resumeChatStream` /
`requestLines`) and the Kotlin composite build (`io.healthassistant:kotlin-sdk`,
`requestText` / typed readers); the Kotlin `SigningParityTest` asserts its
headers are byte-for-byte equal to Python's.

### What to be careful at

- The **actor is the integration owner**; every read/write is filtered to
  the bound patient — patient isolation is an explicit per-path filter
  (`_bound_*` loaders), not a credential property.
- All `/ai/*` paths share a per-integration budget (30 requests / 5 min,
  `HA_RATELIMIT_AI`); a 429 carries `Retry-After` and reaches you.
- The AI turn POSTs (`/ai/chat`, `/ai/chat/stream`,
  `/ai/sessions/{id}/resume`) are **never auto-retried** by the SDKs — a
  failed turn's recovery is a new turn, never a replay.
- Dedup is on you: `examinations[].id` must be the upstream's stable id
  or every sync duplicates the exam.
