# Client SDK Setup

The Bridge ships three strictly-typed client SDKs in the repository — you don't need to hand-roll the HTTP calls. All support timeouts, bounded retry with full-jitter backoff, and HMAC signing when an `api_secret` is configured.

| SDK | Location | Package | Use cases |
|---|---|---|---|
| Python (sync + async) | `integrations/health_assistant_bridge/python-sdk/` | `health-assistant-bridge-sdk` | backend scrapers, cron jobs, data-science notebooks |
| TypeScript | `integrations/health_assistant_bridge/ts-sdk/` | `@health-assistant/bridge-client` | browser extensions, React Native, Node.js scripts |
| Kotlin | `integrations/health_assistant_bridge/kotlin-sdk/` | `io.healthassistant:kotlin-sdk` | Android (and future iOS via KMP) companion apps |

All three SDKs are versioned (`SDK_VERSION`) and the signed `/status` tier returns the server's advertised `latest_sdks` so the client can warn when a newer SDK is available.

## Python

```bash
pip install -e integrations/health_assistant_bridge/python-sdk
```

### Synchronous client (`requests`)

```python
from health_assistant_bridge import HealthAssistantBridgeClient

client = HealthAssistantBridgeClient(
    base_url="https://ha.example",
    integration_id="00000000-0000-0000-0000-000000000000",
    api_secret="a-very-long-random-secret",  # omit for UUID-only mode
    timeout=30.0,  # default 30s per request
)

status = client.get_status()  # → BridgeStatus
mapped = client.request_mapping(
    [  # → MapResponsePayload
        {"name": "Natrium (Na)"},
    ]
)
resp = client.sync_data(payload)  # → SyncResponse
```

### Asynchronous client (`httpx`, pooled)

```python
from health_assistant_bridge import AsyncHealthAssistantBridgeClient

async with AsyncHealthAssistantBridgeClient(
    base_url="https://ha.example",
    integration_id="00000000-0000-0000-0000-000000000000",
    api_secret="a-very-long-random-secret",
    timeout=httpx.Timeout(30.0, connect=10.0),
    max_retries=3,
) as client:
    status = await client.get_status()
    mapped = await client.request_mapping(metrics)
    resp = await client.sync_data(payload)
```

The async client uses one pooled `httpx.AsyncClient` for the whole session (created in the constructor, closed in `aclose()` / the context-manager exit) — re-use the same client across calls; a new client per call defeats pooling.

### Constructor options

| Argument | Required | Default | Notes |
|---|---|---|---|
| `base_url` | ✅ | — | Backend base URL, no trailing slash. |
| `integration_id` | ✅ | — | The bridge instance UUID. |
| `api_secret` | ❌ | `None` | When set, auto-signs `/map` and `/sync`. Omit for UUID-only mode. |
| `timeout` | ❌ | `30.0` / `httpx.Timeout(30.0)` | Per-request timeout (sync: seconds float; async: `httpx.Timeout`). |
| `max_retries` | ❌ | `3` (async only) | Max attempts on transient network/5xx errors. |

### The signing helper

```python
from health_assistant_bridge import sign_request

headers = sign_request(
    secret="a-very-long-random-secret",
    method="POST",
    path="/sync",
    raw_body=b'{"records":[]}',  # the exact bytes you'll send
    # timestamp=...               # override for tests
)
# → {"X-Api-Signature": "<hex>", "X-Api-Timestamp": "<epoch>"}
```

The helper is exported so a custom client can produce the same canonical form the server's `verify_canonical_signature` accepts — use it instead of inlining the MAC so you stay compatible across SDK releases.

### Interactive example

`python-sdk/examples/interactive_sync.py` is a runnable script that exercises the full `status → map → sync` loop against a live instance. Read it as a reference implementation of the recommended workflow.

## TypeScript

```bash
npm install @health-assistant/bridge-client
# or, from the repo:
cd integrations/health_assistant_bridge/ts-sdk && npm run build
```

```typescript
import { HealthAssistantBridgeClient } from "@health-assistant/bridge-client";

const client = new HealthAssistantBridgeClient(
  "https://ha.example",
  "00000000-0000-0000-0000-000000000000",
  {
    apiSecret: "a-very-long-random-secret",  // optional
    timeoutMs: 30_000,                       // default 30s
    maxRetries: 3,                            // default 3
  },
);

const status = await client.getStatus();      // → BridgeStatus
const mapped  = await client.requestMapping(m);  // → MapResponsePayload
const resp    = await client.syncData(payload);  // → SyncResponse
```

### Constructor options (`BridgeClientOptions`)

| Field | Default | Notes |
|---|---|---|
| `apiSecret` | `undefined` | When set, auto-signs `/map` and `/sync`. Omit for UUID-only. |
| `timeoutMs` | `30000` | Per-request timeout via `AbortController`. |
| `maxRetries` | `3` | Max attempts on transient network errors + 429/5xx. |

### The signing helper

```typescript
import { signRequest } from "@health-assistant/bridge-client";

const headers = signRequest(
  secret, "POST", "/sync", Buffer.from(JSON.stringify(payload)),
);
// → { "X-Api-Signature": "<hex>", "X-Api-Timestamp": "<epoch>" }
```

## How signing works in the clients

When an `api_secret`/`apiSecret` is set, the client:

1. Serializes the payload to bytes **once** (`model_dump_json().encode()` / `Buffer.from(JSON.stringify(...))`).
2. Signs those exact bytes.
3. Sends those exact bytes as the request body (never lets `requests`/`fetch` re-serialize the JSON) — so the MAC the server recomputes over the body always matches.

This is why both clients pass `data=`/`body=` (raw bytes) rather than `json=` — a re-serialization could change whitespace or key order and invalidate the signature.

## TypeScript needs the Node stdlib

The TS SDK uses Node's `crypto` module for HMAC. It targets Node 18+ (uses global `fetch` + `AbortController`). If you run it in a pure-browser context without Node `crypto` polyfilled, import a `crypto` polyfill or use the Python SDK from a small backend.

## Kotlin

The Kotlin SDK (`io.healthassistant:kotlin-sdk`, currently JVM — converts to Kotlin Multiplatform with an iOS target later) is consumed by the first-party Android app and any Kotlin/JVM client. If your Gradle build lives next to this repo, add it as a composite build:

```kotlin
// settings.gradle.kts
includeBuild("../core/integrations/health_assistant_bridge/kotlin-sdk")
```
```kotlin
// build.gradle.kts
dependencies {
    implementation("io.healthassistant:kotlin-sdk:0.1.0")
}
```

```kotlin
import io.healthassistant.bridge.BridgeClient
import io.healthassistant.bridge.ClientRecord
import io.healthassistant.bridge.SyncPayload

val client = BridgeClient(
    baseUrl = "https://ha.example",
    integrationId = "00000000-0000-0000-0000-000000000000",
    apiSecret = "a-very-long-random-secret", // omit for UUID-only mode
)

val status = client.getStatus()                       // signed → full payload
val resp = client.syncData(
    SyncPayload(clientVersion = "0.1", sourceSystem = "android",
        records = listOf(ClientRecord(type = "quantitative", code = "8867-4",
            codingSystem = "loinc", name = "Heart Rate", value = 75.0, unit = "bpm")))
)
// Read + management paths (HMAC-signed when the secret is set):
val body = client.requestText("GET", "/observations/latest")
client.close()
```

`/status` is two-tier: **unsigned** it is the pre-pairing liveness probe
(`{status, server_time}` only); **signed** — automatically, whenever
`apiSecret` is set — it returns the full discovery payload (cursor,
`latest_sdks`, `frontend_base_url`, `server_version`, `bridge_features`).
Every other call signs when `apiSecret` is set.
`requestText(...)` returns the response body (throwing `BridgeException`
on a non-2xx) so callers can decode JSON without depending on ktor. The HMAC
parity test (`SigningParityTest`) asserts byte-for-byte equality with the
Python `sign_request` across ASCII / empty / non-ASCII / GET vectors.

## Third-party Node/TS quickstart (status → chat → stream)

The TS SDK is the fastest way to build a third-party client on Node (it
targets Node 18+: global `fetch`, `Buffer`, `AbortController`). Three calls
cover the loop: a signed `/status` for capability discovery, one
non-streaming chat turn, and the SSE stream. Typed chat helpers are on the
SDK roadmap — until they land, the chat calls below are signed raw requests
built with the exported `signRequest` helper (the same canonical form every
SDK uses).

```typescript
import {
  HealthAssistantBridgeClient,
  signRequest,
} from "@health-assistant/bridge-client";

const baseUrl = "https://ha.example";
const integrationId = "00000000-0000-0000-0000-000000000000";
const apiSecret = "a-very-long-random-secret";
const api = `${baseUrl}/api/v1/integrations/health_assistant_bridge/api/${integrationId}`;

// 1) Signed status — the full discovery payload (feature flags, server version).
const client = new HealthAssistantBridgeClient(baseUrl, integrationId, {
  apiSecret,
});
const status = await client.getStatus();
const canChat = status.bridge_features?.includes("ai_assistant") ?? false;
const canStream = status.bridge_features?.includes("ai_streaming") ?? false;

// 2) One non-streaming chat turn (signed POST /ai/chat).
async function chat(message: string, sessionId?: string) {
  const body = Buffer.from(
    JSON.stringify({ message, session_id: sessionId }),
    "utf8",
  );
  const headers = {
    "Content-Type": "application/json",
    ...signRequest(apiSecret, "POST", "/ai/chat", body),
  };
  const res = await fetch(`${api}/ai/chat`, { method: "POST", headers, body });
  if (!res.ok) throw new Error(`chat failed: ${res.status}`);
  return (await res.json()) as {
    message: string;
    session_id: string;
    success: boolean;
  };
}

// 3) The SSE stream (signed POST /ai/chat/stream) — yield `data:` frames.
async function* chatStream(message: string, sessionId?: string) {
  const body = Buffer.from(
    JSON.stringify({ message, session_id: sessionId }),
    "utf8",
  );
  const headers = {
    "Content-Type": "application/json",
    ...signRequest(apiSecret, "POST", "/ai/chat/stream", body),
  };
  const res = await fetch(`${api}/ai/chat/stream`, {
    method: "POST",
    headers,
    body,
  });
  if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl);
      buffer = buffer.slice(nl + 1);
      if (line.startsWith("data: ")) yield line.slice("data: ".length);
    }
  }
}

// Check the flags first, then pick the surface.
if (canStream) {
  for await (const frame of chatStream("Summarize my latest lab results.")) {
    const event = JSON.parse(frame);
    if (event.event === "delta") process.stdout.write(event.text ?? "");
  }
} else if (canChat) {
  const turn = await chat("Summarize my latest lab results.");
  console.log(turn.message);
} else {
  console.log("This server has no AI paths — push/sync still work.");
}
```

Every `/ai/*` call goes through the same HMAC gate as the rest of the
bridge: sign the exact bytes you send, send `X-Api-Signature` +
`X-Api-Timestamp`, keep the message ≤ 8000 chars, and remember only one
streaming turn per session runs at a time.

## See also

- [Authentication & Security](authentication.md) — the canonical form + replay window the signing helper reproduces.
- [API Reference](api-reference.md) — the push endpoints, the read/management paths, and the payload schemas.
- [Troubleshooting](troubleshooting.md) — signing failures, timeout, skew-window errors.

## Pairing (obtaining / renewing an api_secret)

Secrets are mandatory on every data route. The plaintext is shown exactly
once — at instance creation (config-flow response) or when you generate a
**pairing code**:

1. Open the integration's detail page in the web app → **Connect your mobile
   app** card → **Show pairing code**.
2. The card calls `POST /api/v1/integrations/instance/{id}/rotate-secret`
   (owner-only, `patient_id` + `field=api_secret`), which mints a fresh
   secret, stores it encrypted, and returns it once.
3. The QR / connection code then carries all three segments —
   `base_url|integration_id|api_secret` — which the app's scanner and the
   paste field accept directly. No typing.

Rotating invalidates the previous secret immediately: devices paired with an
older code must regenerate + re-scan. Senders that lost their secret use the
same button.
