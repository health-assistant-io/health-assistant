# Authentication & Security

The bridge exposes its endpoints under a unique-per-instance URL whose
`integration_id` is a UUID. **The UUID is an identifier, never a
credential** (audit 2026-08 H1/H2): every bridge instance is provisioned
with an HMAC `api_secret` at creation — the machine routes reject
instances without one (fail-closed). The UUID-only mode documented here
historically was removed with that audit.

## HMAC api_secret (the one mode)

Every data route requires a valid `X-Api-Signature` + `X-Api-Timestamp`
pair keyed by the instance's `api_secret`:

- The secret is **Fernet-encrypted at rest** (via `HA_DATA_KEY`) and masked as `"***"` on read — it never leaves the server in plaintext.
- It must be **at least 16 characters** for adequate HMAC strength.
- **An `api_secret` is mandatory** — every bridge instance is provisioned with one at creation (shown once in the config-flow response; the platform stores it Fernet-encrypted). All data routes require a valid `X-Api-Signature` + `X-Api-Timestamp` pair. An **unsigned** `GET /status` remains the pre-pairing connectivity probe but returns only `{status, server_time}` (use `server_time` to resync a skewed clock); the full status payload requires a signature.
- Rotating: disable + re-create the instance (or ask an admin to rotate the stored secret) — a cleared secret means the machine routes refuse the instance, not that it falls back to UUID-only.

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