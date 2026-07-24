# Troubleshooting

## Signed request rejected (HTTP 400)

The bridge returns `400` with one of two messages when an `api_secret` is configured:

### "… the request carries no X-Api-Signature header."

You set an `api_secret` on the instance but your client didn't sign the request. Either:

- pass the `api_secret` / `apiSecret` to the client constructor so it auto-signs `/map` and `/sync`, or
- if you hand-rolled the request, compute the headers with `sign_request()` / `signRequest()` and send them.

`/status` is never signed — if you're seeing this on `/status`, the instance isn't actually configured with a secret (or you hit the wrong instance id).

### "Invalid or expired request signature."

The signature didn't verify. Five common causes, in order of likelihood:

1. **Body mismatch** — the bytes the client signed aren't the bytes the server received. The SDKs avoid this by serializing the JSON to bytes once and sending those exact bytes (never letting `requests`/`fetch` re-serialize). If you hand-rolled it, sign `Buffer.from(JSON.stringify(payload))` and send *those* bytes as the body — don't pass `json=` to a library that re-serializes.
2. **Wrong path** — the signature covers `METHOD\n<path>\n…` where `path` is the part *after* the integration id with a leading `/` (`/map`, `/sync`). A full URL or a missing leading slash fails.
3. **Skew window** — `X-Api-Timestamp` is more than ±5 minutes from the server's clock. Sync the client clock (NTP) or generate the timestamp closer to the request.
4. **Wrong `api_secret`** — you configured a different secret on the server than the one the client is signing with. The masked UI shows `"***"` for the stored value; check the raw secret in your config.
5. **Method case** — the canonical form uppercases the method. The SDKs handle this; a hand-rolled signer must too (`POST`, not `post`).

## SDK version-mismatch warning

`get_status()` logs `You are using SDK version X, but the latest available is Y. Please consider updating.` when the server's `latest_sdks` field advertises a newer SDK than the one you imported. Update the client SDK (`pip install -U health-assistant-bridge-sdk` / `npm install @health-assistant/bridge-client@latest`). The mismatch warning does **not** block the request — it's advisory.

## Sync cursor stuck

`/status` keeps returning the same `cursor` and you're re-pushing the same data. Causes:

- **You're not sending a `cursor` on `/sync`** — without it, the server preserves the old one. Send the freshest timestamp you scraped.
- **`/sync` is failing before the cursor advances** — check the response: `success: false` means nothing was written, including the cursor. Fix the payload error first.
- **You want to re-pull history** — use the **Reset Sync Cursor** custom action on the instance detail page. It clears `_sync_state.last_timestamp` so the next `/status` returns `null` and your client pulls the full window again.

## 400 / 422 on a malformed payload

- `400 Invalid payload format: ...` — the request body isn't valid JSON or doesn't match the `MapRequestPayload` / `SyncPayload` schema. Check for a missing `client_version`, a `records` entry missing `name`, or a `type` that isn't `quantitative`/`categorical`.
- `422` — Pydantic validation failed on a field (wrong type, missing required). The error body names the field.

## Examinations duplicated

You're seeing the same lab report appear twice. Cause: the `examinations[].id` is missing or not the upstream's stable id. The dedup key is `(tenant, patient, integration_id, examination.id)`; without a stable `id` every sync creates a fresh exam. Fix by passing the portal's `reportId` / `encounterId` as `examinations[].id`.

## `/map` returns "AI mapping service is currently unavailable."

The configured AI provider isn't reachable (no NLP extractor configured, bad API key, provider quota). Check the AI provider settings in Health Assistant, or use a different task assignment for the `nlp_extractor`. `/sync` still works for records whose `biomarker_id` you already cached.

## HMAC works locally but fails in production

- The clocks drift. The skew window is ±5 minutes — `X-Api-Timestamp` must be epoch seconds close to server-now.
- A proxy/load-balancer is re-serializing the body. The signature covers the **raw** bytes; if anything re-encodes the JSON (whitespace, key order), the MAC breaks. Pin the body bytes end-to-end.

## Connection Details / example URL

The **Connection Details** custom action on the instance detail page prints the API base path + curl examples for `/status`, `/map`, and `/sync`. Use it to confirm the exact URL and copy a ready-made request.

## See also

- [Authentication & Security](authentication.md) — the canonical form + replay window.
- [Client SDK Setup](client-setup.md) — the signing helpers handle the canonical form for you.
- [API Reference](api-reference.md) — the payload schemas.