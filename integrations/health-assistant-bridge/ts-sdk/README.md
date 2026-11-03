# Health Assistant Bridge — TypeScript SDK

A lightweight TypeScript SDK for building browser extensions, Node
scripts and React Native apps that securely push medical data into a
self-hosted Health Assistant instance via the **Health Assistant Bridge**
integration — the signed two-way API (`HMAC-SHA256`), including typed
chat + SSE stream helpers.

## Install

```bash
npm install @health-assistant/bridge-client
# or, from this repo's checkout (the source of truth — builds with tsc):
cd integrations/health-assistant-bridge/ts-sdk && npm run build
```

Targets Node 18+ at runtime (global `fetch`, `Buffer`, `AbortController`;
the package's `engines` floor is higher — builds use Node 24 tooling).
HMAC uses Node's `crypto` — polyfill it in a pure-browser build.

## Configure the three credentials

From your server's Bridge card (**Integrations → Health Assistant
Bridge** → "Show pairing code" → `base_url|integration_id|api_secret`):

| Value | What it is |
|---|---|
| `baseUrl` | Your server's base URL (no trailing slash) |
| `integrationId` | The bridge **instance** UUID — bound to one patient |
| `apiSecret` | The per-instance HMAC secret, shown **once** at creation; mandatory on every data route (unsigned traffic is rejected) |

## Signed first call

```typescript
import { HealthAssistantBridgeClient } from "@health-assistant/bridge-client";

const client = new HealthAssistantBridgeClient(
  "http://127.0.0.1:8000",
  "27f13fd9-a577-40a1-a8ee-f0e096162c4d",
  { apiSecret: "a-very-long-random-secret" },
);

const status = await client.getStatus();
console.log(status.status, status.cursor, status.bridge_features);
```

Signed `/status` returns the **full discovery payload** (cursor,
`latest_sdks`, `server_version`, `bridge_features`); unsigned it is the
minimal `{status, server_time}` probe. Real transcript against a live dev
server:

```text
status: active | cursor: null | features: [ 'exam_enrichment', 'ai_assistant', 'ai_streaming' ]
```

## First push — the Universal Data Contract

```typescript
const resp = await client.syncData({
  client_version: "1.4.0",
  source_system: "ts_quickstart",
  records: [{
    type: "quantitative", code: "55258-4", coding_system: "loinc",
    name: "Sleep Duration", value: 7.4, unit: "h",
    timestamp: new Date().toISOString(), performer: "Demo Ring",
  }],
});
// real: resp.success === true, resp.metrics_synced === 1
```

Group lab reports as `examinations=[{ id, ..., records }]` — the
upstream's stable `id` is the dedup key, so a re-push is a no-op.

## One streaming chat call

```typescript
for await (const line of client.chatStream("Summarize my latest lab results.")) {
  if (line.startsWith("data: ")) {
    const frame = JSON.parse(line.slice("data: ".length));
    if (frame.event === "delta") process.stdout.write(frame.text ?? "");
  }
}
```

`chatStream` signs `POST /ai/chat/stream` and yields raw SSE lines
(`requestLines` — never retried; errors throw `BridgeException` before
the first emission). The non-streaming twin is `chat(message, sessionId?)`
(→ `ChatTurnResult`, incl. HITL `tasks` cards); the HITL continuation is
`resumeChatStream(sessionId, messageId?)`. Never replay a failed turn —
start a new one.

## Error handling — the typed `BridgeError` codes

Throws are typed: `BridgeException` (from `request` / `requestLines` /
`chat`-family calls). **Branch on `code`, never on messages** — the
closed vocabulary (machine-pinned by the published schema):

| code | HTTP | Meaning |
|---|---|---|
| `not_found` | 404 | Missing or foreign row ("someone else's" and "doesn't exist" are indistinguishable) |
| `turn_conflict` | 409 | The session's single-flight turn lock is held |
| `hitl_conflict` | 409 | The HITL proposal was already resolved |
| `validation` | 400 | Malformed input / failed guard |
| `stt_failed` | 502 | Upstream speech-to-text failed on `/ai/transcribe` |

```typescript
try {
  await client.chat("Hello");
} catch (e) {
  if (e instanceof BridgeException) switch (e.code) {
    case "turn_conflict": break;        // wait out the running turn
    case "not_found":     break;        // session gone — create a new one
    default:              throw e;
  } else throw e;
}
```

Reads, `/sync` and idempotent mutations retry with full-jitter backoff on
429/5xx + transient network errors; the AI turn POSTs never retry.

## Options

| Field | Default | Notes |
|---|---|---|
| `apiSecret` | — | Auto-signs the data routes (mandatory server-side). |
| `timeoutMs` | `30000` | Per-request timeout via `AbortController` (streams: unbounded). |
| `maxRetries` | `3` | Max attempts on transient failures (idempotent calls only). |

## Pinned by tests

Run the suite from this package: `npm test` (node `--test`) — 27 tests
green, pinning the signing helper (parity with the server verifier),
`/status`, streaming, transcription and the typed-error decode. The
cross-SDK fixtures (`fixtures.test.mts`) are generated from the published
schema, so the docs cannot drift from the wire.

## Signing, if you go raw

```typescript
import { signRequest } from "@health-assistant/bridge-client";

const headers = signRequest(apiSecret, "POST", "/ai/chat", Buffer.from(JSON.stringify(body)));
// → { "X-Api-Signature": "<hex>", "X-Api-Timestamp": "<epoch>" }
```

The canonical form is `METHOD\n<path>[?query]\n<timestamp>\n<raw_body>`
(`path` after the integration id, leading `/`); send the exact bytes you
signed.

## See also

- The integration-SDK doc set (background): `docs/dev/sdk/overview.md`.
- The bridge's per-extension docs (the wire contract):
  `backend/app/extensions/builtin/health_assistant_bridge/docs/api-reference.md`.
