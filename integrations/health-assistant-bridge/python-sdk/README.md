# Health Assistant Bridge — Python SDK

A lightweight Python SDK for building backend services, data scrapers, or
cron jobs that securely push medical data into a self-hosted Health
Assistant instance via the **Health Assistant Bridge** integration — the
signed two-way API (`HMAC-SHA256`). Strictly-typed payload models
(`SyncPayload`, `ClientRecord`, `ClientExaminationRecord`, …), a
`requests`-based sync client and a pooled-`httpx` async client, timeouts
and full-jitter retry built in.

## Install

```bash
# from this repo's checkout root:
pip install -e integrations/health-assistant-bridge/python-sdk
# or with uv:
uv pip install -e integrations/health-assistant-bridge/python-sdk
```

## Configure the three credentials

All three come from your Health Assistant instance's Bridge card
(**Integrations → Health Assistant Bridge → Connect your mobile app →
Show pairing code**; the paste format is `base_url|integration_id|api_secret`):

| Value | What it is |
|---|---|
| `base_url` | Your server's base URL (no trailing slash) |
| `integration_id` | The bridge **instance** UUID — bound to one patient |
| `api_secret` | The per-instance HMAC secret, shown **once** at instance creation. Mandatory on every data route — unsigned traffic is rejected. |

## Signed first call

```python
from health_assistant_bridge import HealthAssistantBridgeClient

client = HealthAssistantBridgeClient(
    base_url="http://127.0.0.1:8000",
    integration_id="27f13fd9-a577-40a1-a8ee-f0e096162c4d",
    api_secret="a-very-long-random-secret",
)

status = client.get_status()
print(status.status, status.cursor, status.bridge_features)
```

With a secret set, `/status` is signed and returns the **full discovery
payload** (cursor, `latest_sdks`, `server_version`, `bridge_features`);
without one it is the minimal `{status, server_time}` liveness probe.
Verified against a live dev server (real transcript):

```text
status: active | cursor: None | server_version: 0.8.0
features: ['exam_enrichment', 'ai_assistant', 'ai_streaming']
```

## First push — the Universal Data Contract

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
print(resp.success, resp.metrics_synced)  # real: True, 1
```

Group lab reports as `examinations=[ClientExaminationRecord(...)]` — the
upstream's stable `id` is the dedup key, so a re-push is a no-op.

## One streaming chat call

The Python SDK ships sync/async **push** helpers only — the chat/stream
surface is covered by the typed helpers in the TypeScript SDK. You can
still stream with the exported `sign_request` helper (the identical
canonical form every SDK signs):

```python
import json, httpx
from health_assistant_bridge import sign_request

body = json.dumps({"message": "Summarize my latest lab results."}).encode()
headers = {"Content-Type": "application/json",
           **sign_request(client.api_secret, "POST", "/ai/chat/stream", body)}

with httpx.Client(timeout=None) as sse:
    with sse.stream("POST", f"{client.api_url}/ai/chat/stream",
                    headers=headers, content=body) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                frame = json.loads(line[6:])
                if frame.get("event") == "delta":
                    print(frame.get("text", ""), end="")
```

Never replay a failed turn — start a new one (or `POST /ai/sessions/{id}/resume`
for a HITL continuation).

## Error handling

The client is thin: `requests.HTTPError` / `httpx.HTTPStatusError` carry
non-2xx answers. The bridge's typed errors arrive as a JSON detail
envelope — branch on `detail.code` (a closed vocabulary: `not_found` 404,
`turn_conflict` 409, `hitl_conflict` 409, `validation` 400,
`stt_failed` 502), never on message strings:

```python
resp_json = response.json() if response is not None else {}
detail = resp_json.get("detail")
if isinstance(detail, dict):
    code = detail["code"]          # e.g. "turn_conflict"
    message = detail["message"]
else:
    code = None                    # legacy plain-string detail from older servers
```

## Constructor options

| Argument | Required | Default | Notes |
|---|---|---|---|
| `base_url` | ✅ | — | Backend base URL, no trailing slash. |
| `integration_id` | ✅ | — | The bridge instance UUID. |
| `api_secret` | client: optional / server: ✅ | — | Auto-signs every data route. |
| `timeout` | ❌ | `30.0` / `httpx.Timeout(30.0)` | Per-request timeout. |
| `max_retries` | ❌ | `3` | Full-jitter retry on transient network/5xx; `1` disables. |

## Pinned by tests

- `backend/tests/test_bridge_sdk_signing.py` — `sign_request` produces
  signatures the server-side `verify_canonical_signature` accepts (the
  round-trip contract).
- `backend/tests/test_bridge_sdk_sync_client.py` — retry semantics +
  re-exports the README's imports rely on.
- `backend/tests/test_bridge_ai_assistant.py` — the chat/stream paths the
  raw call above rides.

## See also

- The doc set: [`docs/dev/sdk/`](../../..) — launch it at
  `docs/dev/sdk/overview.md` (the integration-SDK background the bridge
  contract rides on).
- `examples/interactive_sync.py` — the full `status → map → sync` loop
  against a live instance.
