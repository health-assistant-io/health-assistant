# Authentication & Security

The bridge exposes three endpoints under a unique-per-instance URL whose `integration_id` is a UUID. There are two security modes; pick one when configuring the instance in the Health Assistant UI.

## Mode 1 — UUID-only (default)

The `integration_id` in the URL *is* the secret. No request signing is required. This is acceptable when:

- Health Assistant runs on a **trusted LAN** or behind a VPN, and
- the instance URL isn't logged or shared beyond the client.

It is the simplest mode for a self-hosted box at home. Configure the bridge with just an **Instance Name** — the generated URL is ready to use.

## Mode 2 — HMAC api_secret (recommended for internet-exposed instances)

When the bridge is reachable from the public internet, a URL-bound UUID isn't strong enough (it can't be rotated without recreating the instance, and a captured URL can be replayed). Set an **API Secret** in the bridge config flow:

- The secret is **Fernet-encrypted at rest** (via `INTEGRATION_SECRET_KEY`) and masked as `"***"` on read — it never leaves the server in plaintext.
- It must be **at least 16 characters** for adequate HMAC strength.
- **An `api_secret` is mandatory** — every bridge instance is provisioned with one at creation (shown once in the config-flow response; the platform stores it Fernet-encrypted). All data routes require a valid `X-Api-Signature` + `X-Api-Timestamp` pair. An **unsigned** `GET /status` remains the pre-pairing connectivity probe but returns only `{status, server_time}` (use `server_time` to resync a skewed clock); the full status payload requires a signature.
- Leave the secret empty/blank to clear it and revert to UUID-only mode.

## The signed canonical form

The signature covers this canonical string:

```
<METHOD>\n<path>[?query]\n<timestamp>\n<raw_body>
```

| Component | Value |
|---|---|
| `METHOD` | The HTTP method, uppercased (`POST`). |
| `path` | The API path component **after** the integration id, with a leading `/` (`/map` or `/sync`). |
| `timestamp` | Integer epoch seconds (the same value sent in `X-Api-Timestamp`). **Mandatory** — a request without it is rejected 401 (audit 2026-08 M2). |
| `query` | When the request URL carries query parameters, they are part of the signed path (`path?query`) — the MAC covers them so they cannot be tampered with on a captured request. |
| `raw_body` | The **exact** request body bytes the client sends — not a re-serialization. The signature and the HTTP body must come from the same bytes. |

The header `X-Api-Signature` is the hex HMAC-SHA256 of the canonical string keyed by the `api_secret`. `X-Api-Timestamp` is the integer timestamp string.

## Replay protection

The server rejects a request when `abs(server_now - timestamp) > 300` seconds (±5 min). The timestamp is folded into the MAC, so a captured signature can't be replayed after the window closes — capturing the headers alone isn't enough to forge a future request.

## Which endpoints are gated

| Endpoint | Signed when `api_secret` set? | Why |
|---|---|---|
| `GET /status` (unsigned) | ❌ never | Minimal pre-pairing probe: `{status, server_time}` only. |
| `GET /status` (signed) | ✅ always | Full status payload (SDK versions, cursor, frontend URL). |
| `POST /map` | ✅ | Triggers an LLM call on your account. |
| `POST /sync` | ✅ | Writes clinical data. |
| `GET /observations`, `/observations/latest`, `/biomarkers`, `/examinations`, `/examinations/{id}`, `/examinations/{id}/documents` | ✅ | Reads the bound patient's clinical data. |
| `POST /examinations`, `POST /examinations/{id}/documents` | ✅ | Writes clinical data (examinations, documents). |

## Server-side verification

Verification is delegated to `integrations.sdk.webhook_security.verify_canonical_signature` — the same constant-time helper the generic two-way API proxy uses. A missing-signature request returns **HTTP 400** with a message naming the missing header; a bad/expired signature returns **HTTP 400** with "Invalid or expired request signature." No timing-leak distinguishes the two failure modes.

## Don't hand-roll the signature

The official SDKs compute the headers for you — pass the `api_secret` (Python) / `apiSecret` (TypeScript) to the constructor. If you must build your own client, use the exported `sign_request()` / `signRequest()` helpers so the canonical form stays in sync with the server across releases. See [Client SDK Setup](client-setup.md).

## See also

- [Overview](overview.md)
- [Client SDK Setup](client-setup.md)
- [API Reference](api-reference.md)
- [Troubleshooting](troubleshooting.md)